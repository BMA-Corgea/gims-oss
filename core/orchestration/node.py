# core/orchestration/node.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Callable, Union

from fastapi import APIRouter, FastAPI, Query, Request
import httpx
from pathlib import Path

from core.errors import AppError


class NodeKind(str, Enum):
    UI = "ui"            # Serves HTML / front-end
    API = "api"          # JSON/REST endpoints
    RULES = "rules"      # Server-side logic endpoints
    LOGIN = "login"      # Auth/login endpoints
    LAUNCHER = "launcher"
    INFRASTRUCTURE = "infrastructure"
    STATE = "state"
    CHAIN = "chain"      # Automatic API calls based on user actions
    TRIGGER = "trigger"  # Calls an API based on a defined trigger


def kebab(s: str) -> str:
    import re
    s = re.sub(r"[^A-Za-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-").lower()


# ─────────────────────────────────────────────────────────────────────────────
# Chain primitives (pure; no I/O)
# ─────────────────────────────────────────────────────────────────────────────

BodyType = Union[dict, list, str, int, float, bool, None]
BodyFactory = Callable[[dict], BodyType]
ExpectFn = Callable[[Any], bool]


class ChainStep:
    """
    One HTTP call executed sequentially.
      - method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "HEAD"
      - url: absolute (http/https) OR relative (starts with "/")
             May contain .format() placeholders like {project} or any query param key.
      - body: static JSON-like value OR callable(ctx)->JSON-like; ignored for GET/HEAD
      - expect: predicate(data)->bool; data is parsed JSON if possible, else None
    """
    def __init__(
        self,
        name: str,
        url: str,
        *,
        method: str = "GET",
        body: Optional[Union[BodyType, BodyFactory]] = None,
        expect: Optional[ExpectFn] = None,
    ):
        self.name = name
        self.method = method.upper()
        self.url = url
        self.body = body
        self.expect = expect


class Chain:
    """A linear list of ChainStep instances."""
    def __init__(self, steps: list[ChainStep]):
        self.steps = steps


# ─────────────────────────────────────────────────────────────────────────────
# Node (no I/O in core; base is injected)
# ─────────────────────────────────────────────────────────────────────────────

# base_resolver: Given project -> base URL (e.g., "http://127.0.0.1:8000")
BaseResolver = Callable[[str], str]


@dataclass(slots=True)
class Node:
    """
    Generic node. For CHAIN kind, if `router` is None, a small router with GET /run is
    auto-generated that executes the provided `chain` sequentially.

    NOTE: This file performs NO I/O. Any base URL resolution must be injected via
    `base_resolver`. If a Chain uses relative URLs and no base_resolver is provided,
    an error is raised at runtime.
    """
    name: str
    kind: NodeKind
    router: Optional[APIRouter]
    route_prefix: str = ""
    template: Optional[Path] = None
    static_dir: Optional[Path] = None
    meta: dict[str, Any] = field(default_factory=dict)

    # Chain-only fields:
    chain: Optional[Chain] = None
    base_resolver: Optional[BaseResolver] = None  # pure function supplied by caller

    def __post_init__(self) -> None:
        # Fail-fast at CONSTRUCTION (owner decision) instead of at mount / first request, so a
        # malformed node surfaces immediately with a clear message rather than a late RuntimeError.
        if not self.name or not isinstance(self.name, str):
            raise ValueError(f"Node.name must be a non-empty string (got {self.name!r})")
        if not isinstance(self.kind, NodeKind):
            raise ValueError(f"Node {self.name!r}: kind must be a NodeKind (got {self.kind!r})")
        if self.kind == NodeKind.CHAIN:
            if self.router is None and self.chain is None:
                raise ValueError(f"CHAIN node {self.name!r} requires a `chain` or a `router`")
        elif self.router is None:
            raise ValueError(f"{self.kind.value} node {self.name!r} requires a `router`")

    def _make_chain_router(self) -> APIRouter:
        if not self.chain:
            raise RuntimeError("CHAIN node requires `chain` to be provided.")

        r = APIRouter()

        @r.get("/run")
        async def run_chain(request: Request, project: str = Query(...)):
            # Collect query params so steps can use them in .format() or body(ctx)
            params = dict(request.query_params)
            params.pop("project", None)  # keep project separate
            ctx: dict[str, Any] = {"project": project, "params": params, "last": None}

            # Helper: resolve final URL (pure)
            def resolve_url(raw: str) -> str:
                try:
                    formatted = raw.format(project=project, **params)
                except Exception:
                    formatted = raw
                if formatted.startswith("http://") or formatted.startswith("https://"):
                    return formatted
                if not formatted.startswith("/"):
                    # Enforce explicitness for non-absolute, non-rooted URLs
                    raise AppError(
                        "CHAIN_URL_INVALID",
                        "Relative URL must start with '/' or be absolute",
                        status=400,
                        details={"url": raw},
                    )
                if not self.base_resolver:
                    raise AppError(
                        "CHAIN_BASE_RESOLVER_MISSING",
                        "Relative URL requires base_resolver but none provided",
                        status=500,
                        details={"url": raw},
                    )
                base = self.base_resolver(project)
                return base.rstrip("/") + formatted

            results: list[dict[str, Any]] = []

            async with httpx.AsyncClient() as client:
                for step in self.chain.steps:
                    url = resolve_url(step.url)

                    try:
                        if step.method in ("GET", "HEAD"):
                            resp = await client.request(step.method, url)
                        else:
                            body: BodyType
                            if callable(step.body):
                                body = step.body(ctx)  # type: ignore[arg-type]
                            else:
                                body = step.body  # type: ignore[assignment]

                            resp = await client.request(step.method, url, json=body)

                        resp.raise_for_status()
                        data: Any = None
                        # Try JSON; fall back to None
                        try:
                            data = resp.json()
                        except Exception:
                            data = None

                    except Exception as e:
                        raise AppError(
                            "CHAIN_STEP_FAILED",
                            str(e),
                            status=502,
                            details={
                                "step": step.name,
                                "method": step.method,
                                "url": url,
                            },
                        )

                    if step.expect and not step.expect(data):
                        raise AppError(
                            "CHAIN_STEP_EXPECTATION_FAILED",
                            "Expectation failed",
                            status=422,
                            details={"step": step.name, "method": step.method, "url": url},
                        )

                    ctx["last"] = data
                    results.append({"step": step.name, "method": step.method, "url": url, "ok": True})

            return {"ok": True, "steps": results}

        return r

    def mount(self, app: FastAPI, prefix: str | None = None) -> None:
        use_prefix = prefix if prefix is not None else self.route_prefix
        if self.kind == NodeKind.CHAIN:
            router_to_use = self.router or self._make_chain_router()
            app.include_router(router_to_use, prefix=use_prefix)
            return

        if not self.router:
            raise RuntimeError("Non-CHAIN nodes must provide a router.")
        app.include_router(self.router, prefix=use_prefix)

    @property
    def slug(self) -> str:
        return kebab(self.name)
        