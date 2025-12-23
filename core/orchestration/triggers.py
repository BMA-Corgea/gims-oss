# core/orchestration/triggers.py
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, List, Tuple, Optional, Set, Union
from fastapi import FastAPI, Request

# ──────────────────────────────────────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────────────────────────────────────
ChainHandlerAsync = Callable[[dict, Request], Awaitable[None]]
ChainHandlerSync  = Callable[[dict, Request], None]
ChainHandler = Union[ChainHandlerAsync, ChainHandlerSync]

IntervalHandlerAsync = Callable[[], Awaitable[None]]
IntervalHandlerSync  = Callable[[], None]
IntervalHandler = Union[IntervalHandlerAsync, IntervalHandlerSync]

FsHandlerAsync = Callable[[Set[str]], Awaitable[None]]
FsHandlerSync  = Callable[[Set[str]], None]
FsHandler = Union[FsHandlerAsync, FsHandlerSync]

# ──────────────────────────────────────────────────────────────────────────────
# Config & utils
# ──────────────────────────────────────────────────────────────────────────────
DEBUG = False

def _dbg(*a: Any) -> None:
    if DEBUG:
        print("[triggers]", *a, flush=True)

async def _maybe_await(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    try:
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            await result  # type: ignore[func-returns-value]
    except Exception as e:
        _dbg("handler error:", getattr(fn, "__name__", str(fn)), repr(e))

# ──────────────────────────────────────────────────────────────────────────────
# Registries
# ──────────────────────────────────────────────────────────────────────────────
_chain_pre:  List[ChainHandler] = []
_chain_post: List[ChainHandler] = []

_interval_jobs:  List[Tuple[float, IntervalHandler]] = []
_interval_tasks: List[asyncio.Task] = []

_watch_specs:  List[Tuple[str, Optional[str], FsHandler]] = []  # (path, glob, handler)
_watch_tasks:  List[asyncio.Task] = []

def _add_unique(lst: List, item: Any) -> None:
    # Avoid duplicate registrations (same object identity)
    if any(id(x) == id(item) for x in lst):
        return
    lst.append(item)

# ──────────────────────────────────────────────────────────────────────────────
# Public subscription API (both decorator-style and subscribe_* style)
# ──────────────────────────────────────────────────────────────────────────────
def on_chain_pre(fn: ChainHandler) -> ChainHandler:
    """Decorator: @on_chain_pre"""
    _add_unique(_chain_pre, fn)
    _dbg("on_chain_pre registered:", getattr(fn, "__name__", str(fn)))
    return fn

def on_chain_post(fn: ChainHandler) -> ChainHandler:
    """Decorator: @on_chain_post"""
    _add_unique(_chain_post, fn)
    _dbg("on_chain_post registered:", getattr(fn, "__name__", str(fn)))
    return fn

# Back-compat for modules that call subscribe_* explicitly
def subscribe_chain_pre(fn: ChainHandler) -> ChainHandler:
    return on_chain_pre(fn)

def subscribe_chain_post(fn: ChainHandler) -> ChainHandler:
    return on_chain_post(fn)

def every(seconds: float):
    """Decorator: @every(5.0)"""
    def deco(fn: IntervalHandler) -> IntervalHandler:
        _interval_jobs.append((seconds, fn))
        _dbg("every registered:", seconds, getattr(fn, "__name__", str(fn)))
        return fn
    return deco

def on_fs_change(path: str, glob: Optional[str] = None):
    """
    Decorator: @on_fs_change('/some/path', '*.py')
    Requires `watchfiles` installed for async watching.
    """
    def deco(fn: FsHandler) -> FsHandler:
        _watch_specs.append((path, glob, fn))
        _dbg("on_fs_change registered:", path, glob, getattr(fn, "__name__", str(fn)))
        return fn
    return deco

# Optional unsub APIs (handy in tests)
def unsubscribe_chain_pre(fn: ChainHandler) -> None:
    for i, f in enumerate(list(_chain_pre)):
        if id(f) == id(fn):
            _chain_pre.pop(i); break

def unsubscribe_chain_post(fn: ChainHandler) -> None:
    for i, f in enumerate(list(_chain_post)):
        if id(f) == id(fn):
            _chain_post.pop(i); break

# ──────────────────────────────────────────────────────────────────────────────
# Publishers (to be called by orchestrated_fetch_node, etc.)
# ──────────────────────────────────────────────────────────────────────────────
async def publish_chain_pre(env: dict, request: Request) -> None:
    # Iterate over a snapshot in case handlers mutate the list
    for fn in tuple(_chain_pre):
        await _maybe_await(fn, env, request)

async def publish_chain_post(env: dict, request: Request) -> None:
    for fn in tuple(_chain_post):
        await _maybe_await(fn, env, request)

# ──────────────────────────────────────────────────────────────────────────────
# Runtime (intervals & file watchers)
# ──────────────────────────────────────────────────────────────────────────────
async def _run_every(seconds: float, fn: IntervalHandler):
    while True:
        await _maybe_await(fn)
        await asyncio.sleep(seconds)

async def _run_fs_watch(path: str, glob: Optional[str], fn: FsHandler):
    try:
        from watchfiles import awatch, DefaultFilter
    except Exception:
        _dbg("watchfiles not installed; FS watchers disabled.")
        return

    import fnmatch as _fn
    async for changes in awatch(path, watch_filter=DefaultFilter()):
        files = {p for _, p in changes}
        if glob:
            files = {f for f in files if _fn.fnmatch(f, glob)}
        await _maybe_await(fn, files)

def mount_triggers(app: FastAPI) -> None:
    @app.on_event("startup")
    async def _start_triggers():
        loop = asyncio.get_event_loop()
        # intervals
        for seconds, fn in _interval_jobs:
            _interval_tasks.append(loop.create_task(_run_every(seconds, fn)))
        # file watchers
        for path, glob, fn in _watch_specs:
            _watch_tasks.append(loop.create_task(_run_fs_watch(path, glob, fn)))
        _dbg("startup: intervals:", len(_interval_jobs), "watchers:", len(_watch_specs))

    @app.on_event("shutdown")
    async def _stop_triggers():
        for t in _interval_tasks + _watch_tasks:
            t.cancel()
        _dbg("shutdown: cancelled interval/watch tasks")
        