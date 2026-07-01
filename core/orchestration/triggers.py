# core/orchestration/triggers.py
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional, Union

from fastapi import Request

from utils.logger import get_logger
from utils import config

log = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────────────────────────────────────
ChainHandlerAsync = Callable[[dict, Request], Awaitable[None]]
ChainHandlerSync  = Callable[[dict, Request], None]
ChainHandler = Union[ChainHandlerAsync, ChainHandlerSync]


def _dbg(*a: Any) -> None:
    # One logger (GIMS_LOG_LEVEL); never a module-level print/DEBUG flag.
    log.debug("[triggers]", *a)


# ──────────────────────────────────────────────────────────────────────────────
# Request-scoped context (R7)
# ──────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ChainContext:
    """Per-publish, request-scoped context threaded into every handler's structured logs.

    Gives each chain publish a short correlation id so a slow/broken handler's WARNING can be
    tied back to the exact request + phase that triggered it (the dormant global lists logged
    no such context)."""
    cid: str
    phase: str
    path: str

    @classmethod
    def new(cls, phase: str, env: Optional[dict]) -> "ChainContext":
        return cls(cid=uuid.uuid4().hex[:8], phase=phase, path=str((env or {}).get("path") or ""))

    def tag(self) -> str:
        return f"[chain {self.phase} {self.cid} {self.path}]"


# ──────────────────────────────────────────────────────────────────────────────
# Per-handler runner
# ──────────────────────────────────────────────────────────────────────────────
async def _maybe_await(fn: Callable[..., Any], *args: Any, _ctx: Optional[ChainContext] = None, **kwargs: Any) -> None:
    """Run one event handler, bounded by a per-handler timeout. Side-effect chain/trigger handlers
    fail OPEN — but LOUD (owner decision): a broken audit/cache handler is logged at WARNING (with
    the request-scoped context when present) and abandoned, never blocking or crashing the publish
    loop. Hooks that must BLOCK an action live in the fetch-node PRE path (fail-closed), not here."""
    name = getattr(fn, "__name__", str(fn))
    tag = _ctx.tag() if _ctx is not None else "[triggers]"
    try:
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            await asyncio.wait_for(result, timeout=config.chain_handler_timeout())
    except asyncio.TimeoutError:
        log.warning(tag, "handler timed out:", name, f"(>{config.chain_handler_timeout()}s)")
    except Exception as e:
        log.warning(tag, "handler error:", name, repr(e))


def _add_unique(lst: List, item: Any) -> None:
    # Avoid duplicate registrations (same object identity)
    if any(id(x) == id(item) for x in lst):
        return
    lst.append(item)


# ──────────────────────────────────────────────────────────────────────────────
# The one EventDispatcher (R7): owns the chain-pre / chain-post subscriber lists and
# the publish loop. ONE registration model (subscribe(phase, fn), identity-deduped);
# each publish builds a request-scoped ChainContext; handlers run with a per-handler
# timeout and fail open-but-loud.
# ──────────────────────────────────────────────────────────────────────────────
class EventDispatcher:
    def __init__(self) -> None:
        self.pre: List[ChainHandler] = []
        self.post: List[ChainHandler] = []

    def _list(self, phase: str) -> List[ChainHandler]:
        return self.pre if phase == "pre" else self.post

    def subscribe(self, phase: str, fn: ChainHandler) -> ChainHandler:
        _add_unique(self._list(phase), fn)
        _dbg(f"subscribe[{phase}]:", getattr(fn, "__name__", str(fn)))
        return fn

    def unsubscribe(self, phase: str, fn: ChainHandler) -> None:
        lst = self._list(phase)
        for i, f in enumerate(list(lst)):
            if id(f) == id(fn):
                lst.pop(i)
                break

    async def publish(self, phase: str, env: dict, request: Optional[Request]) -> None:
        ctx = ChainContext.new(phase, env)
        handlers = tuple(self._list(phase))  # snapshot in case a handler mutates the list
        if handlers:
            _dbg(ctx.tag(), f"-> {len(handlers)} handler(s)")
        for fn in handlers:
            await _maybe_await(fn, env, request, _ctx=ctx)


# The single dispatcher instance. The module-level functions + lists below are a thin,
# fully back-compatible facade over it (callers and tests keep using triggers.*).
_dispatcher = EventDispatcher()

# Back-compat aliases: the SAME list objects the dispatcher mutates in place, so existing
# readers of `triggers._chain_pre` / `triggers._chain_post` keep seeing the live subscribers.
_chain_pre: List[ChainHandler] = _dispatcher.pre
_chain_post: List[ChainHandler] = _dispatcher.post


# ──────────────────────────────────────────────────────────────────────────────
# Public subscription API (decorator-style and subscribe_* style both delegate here)
# ──────────────────────────────────────────────────────────────────────────────
def on_chain_pre(fn: ChainHandler) -> ChainHandler:
    """Decorator: @on_chain_pre"""
    return _dispatcher.subscribe("pre", fn)

def on_chain_post(fn: ChainHandler) -> ChainHandler:
    """Decorator: @on_chain_post"""
    return _dispatcher.subscribe("post", fn)

# Back-compat for modules that call subscribe_* explicitly
def subscribe_chain_pre(fn: ChainHandler) -> ChainHandler:
    return _dispatcher.subscribe("pre", fn)

def subscribe_chain_post(fn: ChainHandler) -> ChainHandler:
    return _dispatcher.subscribe("post", fn)

# Optional unsub APIs (handy in tests)
def unsubscribe_chain_pre(fn: ChainHandler) -> None:
    _dispatcher.unsubscribe("pre", fn)

def unsubscribe_chain_post(fn: ChainHandler) -> None:
    _dispatcher.unsubscribe("post", fn)

# ──────────────────────────────────────────────────────────────────────────────
# Publishers (to be called by orchestrated_fetch_node, etc.)
# ──────────────────────────────────────────────────────────────────────────────
async def publish_chain_pre(env: dict, request: Optional[Request]) -> None:
    await _dispatcher.publish("pre", env, request)

async def publish_chain_post(env: dict, request: Optional[Request]) -> None:
    await _dispatcher.publish("post", env, request)
