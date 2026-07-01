# nodes/auto_backup_node.py
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional, Any

from fastapi import APIRouter, Body
from core.orchestration.node import Node, NodeKind

# Phase 6: orchestration must not import the GUI layer. The backup-schedule service
# (gui/backup_gui.py) registers its entry points with this core hook at import time;
# we call through the hook so this node depends only on core.
from core.orchestration.backup_hook import (
    run_schedule_tick as _schedule_tick,
    load_schedules as _load_schedules,
)


# -----------------------------------------------------------------------------
# Debug utilities
# -----------------------------------------------------------------------------
# Debug control - set to False to disable all backend debug logging
from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
# START MODE:
#   "RUN_ONCE" -> run one tick at startup (scan JSON and execute due backups)
#   "LOOP"     -> run a background loop calling schedule_tick() every INTERVAL
START_MODE: str = "RUN_ONCE"

# Only used if START_MODE == "LOOP"
INTERVAL_SECONDS: int = 3600  # e.g., run every hour


# -----------------------------------------------------------------------------
# Internal state & helpers
# -----------------------------------------------------------------------------
router = APIRouter(prefix="/auto-backups", tags=["Auto Backups"])
_loop_task: Optional[asyncio.Task] = None
_last_status: dict[str, Any] | None = None
_last_error: str | None = None

log.debug("module import: START_MODE =", START_MODE, "| INTERVAL_SECONDS =", INTERVAL_SECONDS)


def _iso_now_utc() -> str:
    s = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    log.debug("_iso_now_utc:", s)
    return s


def _tick_once(now_iso: Optional[str] = None) -> dict[str, Any]:
    """
    Synchronously run one schedule tick via the core backup hook (which dispatches to
    the GUI-registered schedule_tick service). Returns its JSON result.
    """
    log.debug("_tick_once: begin | now_iso =", now_iso)
    res = _schedule_tick(now_iso=now_iso)
    ran_count = len(res.get("ran", [])) if isinstance(res, dict) else "?"
    log.debug("_tick_once: end   | ran_count =", ran_count, "| result_keys =", list(res.keys()) if isinstance(res, dict) else type(res))
    return res


async def _tick_loop():
    """Background loop (when START_MODE == 'LOOP')."""
    global _last_status, _last_error
    log.debug("_tick_loop: starting loop | interval =", INTERVAL_SECONDS, "seconds")
    i = 0
    while True:
        i += 1
        try:
            log.debug("_tick_loop: iteration", i, "-> calling _tick_once()")
            _last_status = _tick_once()
            _last_error = None
            log.debug("_tick_loop: iteration", i, "-> ok | last_status_keys =", list(_last_status.keys()) if isinstance(_last_status, dict) else type(_last_status))
        except Exception as e:
            _last_error = f"{type(e).__name__}: {e}"
            log.debug("_tick_loop: iteration", i, "-> ERROR:", _last_error)
        log.debug("_tick_loop: sleeping", INTERVAL_SECONDS, "seconds")
        await asyncio.sleep(INTERVAL_SECONDS)


# -----------------------------------------------------------------------------
# UPON MODULE LOAD BEHAVIOR (easy to switch later)
# -----------------------------------------------------------------------------
@router.on_event("startup")
async def _on_startup():
    """
    This is the only place that controls what happens automatically when the
    module loads. Switch START_MODE to 'LOOP' to keep ticking on an interval.
    """
    global _loop_task, _last_status, _last_error
    log.debug("startup: invoked | START_MODE =", START_MODE)

    if START_MODE == "RUN_ONCE":
        # Run one tick right now, but don't block the event loop too long.
        # schedule_tick() is sync; run in a thread to be polite.
        try:
            loop = asyncio.get_running_loop()
            log.debug("startup: RUN_ONCE -> scheduling run_in_executor(_tick_once)")
            _last_status = await loop.run_in_executor(None, _tick_once, None)
            _last_error = None
            log.debug("startup: RUN_ONCE -> done | last_status_keys =", list(_last_status.keys()) if isinstance(_last_status, dict) else type(_last_status))
        except Exception as e:
            _last_error = f"{type(e).__name__}: {e}"
            log.debug("startup: RUN_ONCE -> ERROR:", _last_error)
    elif START_MODE == "LOOP":
        if _loop_task is None or _loop_task.done():
            log.debug("startup: LOOP -> creating background _tick_loop task")
            _loop_task = asyncio.create_task(_tick_loop())
        else:
            log.debug("startup: LOOP -> background task already running")
    else:
        log.debug("startup: unknown START_MODE (no-op):", START_MODE)


@router.on_event("shutdown")
async def _on_shutdown():
    """Cleanly stop the background task if running."""
    global _loop_task
    log.debug("shutdown: invoked")
    if _loop_task and not _loop_task.done():
        log.debug("shutdown: cancelling background task")
        _loop_task.cancel()
        try:
            await _loop_task
        except asyncio.CancelledError:
            log.debug("shutdown: background task cancelled")
        finally:
            _loop_task = None
    else:
        log.debug("shutdown: no background task to cancel")


# -----------------------------------------------------------------------------
# Minimal API (manual control & visibility)
# -----------------------------------------------------------------------------
@router.get("/status")
def status():
    """
    Returns current settings, last tick results, and whether a loop is running.
    Useful for debugging or a tiny admin UI later.
    """
    log.debug("GET /status: begin")
    schedules = [s.dict() for s in _load_schedules()]
    resp = {
        "ok": True,
        "config": {
            "start_mode": START_MODE,
            "interval_seconds": INTERVAL_SECONDS,
            "debug_enabled": DEBUG_ENABLED,
        },
        "loop_running": bool(_loop_task and not _loop_task.done()),
        "last_error": _last_error,
        "last_status": _last_status,
        "schedules_count": len(schedules),
        "schedules": schedules,
        "now": _iso_now_utc(),
    }
    log.debug("GET /status: end | schedules_count =", len(schedules), "| loop_running =", resp["loop_running"])
    return resp


@router.post("/run")
def run_now(now_iso: Optional[str] = Body(None, embed=True)):
    """
    Manually trigger a tick via HTTP. Optional body: {"now_iso": "..."} to test.
    """
    global _last_status, _last_error
    log.debug("POST /run: begin | now_iso =", now_iso)
    try:
        _last_status = _tick_once(now_iso=now_iso)
        _last_error = None
        log.debug("POST /run: end   | ok | last_status_keys =", list(_last_status.keys()) if isinstance(_last_status, dict) else type(_last_status))
        return {"ok": True, "result": _last_status}
    except Exception as e:
        _last_error = f"{type(e).__name__}: {e}"
        log.debug("POST /run: ERROR:", _last_error)
        return {"ok": False, "error": _last_error}


# -----------------------------------------------------------------------------
# Debug endpoints (optional)
# -----------------------------------------------------------------------------
@router.get("/debug/enabled")
def get_debug_enabled():
    log.debug("GET /debug/enabled")
    return {"debug_enabled": DEBUG_ENABLED}

@router.post("/debug/enabled")
def set_debug_enabled(enabled: bool = Body(..., embed=True)):
    global DEBUG_ENABLED
    DEBUG_ENABLED = bool(enabled)
    log.debug("POST /debug/enabled ->", DEBUG_ENABLED)
    return {"ok": True, "debug_enabled": DEBUG_ENABLED}


# -----------------------------------------------------------------------------
# Export as a Node so this can be dropped into any Module
# -----------------------------------------------------------------------------
auto_backup_node = Node(
    name="Auto Backups",
    kind=NodeKind.INFRASTRUCTURE,
    router=router,
    # meta is empty; this node doesn't inject front-end assets
    meta={},
)

log.debug("node constructed: auto_backup_node ready")
