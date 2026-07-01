"""Version-robust route enumeration for the front-door guard tests.

FastAPI >= ~0.131 stopped flattening ``app.include_router(...)`` into individual
``APIRoute`` objects inside ``app.routes``. It now keeps a *tree*: each include is a
single ``fastapi.routing._IncludedRouter`` entry (no ``.path``) that wraps the
``original_router`` and an ``include_context`` carrying the mount ``prefix``. Routing
and ``app.openapi()`` recurse into that tree, but naive ``[r.path for r in app.routes]``
introspection silently sees only the directly-added routes.

The guard baselines were captured under the old flat representation, so the tests must
walk the tree the same way FastAPI does. These helpers do that, with a graceful fallback
to the flat shape (older FastAPI / plain Starlette ``Mount``) so the tests stay correct
across versions.
"""
from __future__ import annotations

from typing import Iterable

try:  # FastAPI >= ~0.131 — included routers live as a tree node
    from fastapi.routing import _IncludedRouter  # type: ignore
except Exception:  # pragma: no cover - older FastAPI flattens, so no tree node exists
    _IncludedRouter = ()  # isinstance(x, ()) is always False -> flat path only


def _walk(routes: Iterable[object], prefix: str, sink: list[str]) -> None:
    for rt in routes:
        if _IncludedRouter and isinstance(rt, _IncludedRouter):
            ctx = getattr(rt, "include_context", None)
            sub_prefix = prefix + (getattr(ctx, "prefix", "") or "")
            sub = getattr(rt, "original_router", None)
            _walk(getattr(sub, "routes", []) or [], sub_prefix, sink)
        else:
            path = getattr(rt, "path", None)
            if path is not None:
                sink.append(prefix + path)


def iter_mounted_paths(app) -> list[str]:
    """Every mounted path (with duplicates preserved), recursing included routers.

    Duplicates are kept on purpose so callers can detect a router mounted more than once.
    """
    sink: list[str] = []
    _walk(app.routes, "", sink)
    return sink


def mounted_path_set(app) -> set[str]:
    """The set of all mounted paths, including ``include_in_schema=False`` page nodes."""
    return set(iter_mounted_paths(app))


def count_path(app, path: str) -> int:
    """How many times ``path`` is exposed across the whole route tree (dedup detector)."""
    return sum(1 for p in iter_mounted_paths(app) if p == path)
