# core/orchestration/backup_hook.py
"""
Inversion seam for the backup-schedule service (Phase 6 — invert nodes→gui imports).

Orchestration nodes (e.g. ``nodes/auto_backup_node.py``) must trigger scheduled backups
without importing the GUI layer. The backup *service* currently lives in
``gui/backup_gui.py`` (route handlers + a large body of poorly-tested execution helpers),
so rather than move it wholesale, the GUI registers its two entry points here at import
time and the node calls them through this core-level hook.

Dependency direction is now correct:
    gui.backup_gui ──registers──▶ core.orchestration.backup_hook ◀──calls── nodes.auto_backup_node

If the GUI layer was never imported (e.g. an isolated unit test that imports the node
alone), the hooks are unregistered and these functions degrade gracefully instead of
raising — the node already tolerates an empty/failed tick.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from utils.logger import get_logger

log = get_logger(__name__)

# Registered by gui.backup_gui at import time.
_schedule_tick: Optional[Callable[..., dict]] = None
_schedules_loader: Optional[Callable[[], list]] = None


def register_schedule_tick(fn: Callable[..., dict]) -> None:
    """Register the function that runs one schedule tick (the GUI's schedule_tick)."""
    global _schedule_tick
    _schedule_tick = fn
    log.debug("backup_hook: schedule_tick registered")


def register_schedules_loader(fn: Callable[[], list]) -> None:
    """Register the function that loads the configured schedules (the GUI's _load_schedules)."""
    global _schedules_loader
    _schedules_loader = fn
    log.debug("backup_hook: schedules loader registered")


def run_schedule_tick(now_iso: Optional[str] = None) -> dict[str, Any]:
    """Run one schedule tick via the registered service. No-op (logged) if unregistered."""
    if _schedule_tick is None:
        log.debug("backup_hook: run_schedule_tick called but no service registered; skipping")
        return {"ok": False, "ran": [], "error": "backup schedule service not registered"}
    return _schedule_tick(now_iso=now_iso)


def load_schedules() -> list:
    """Load configured schedules via the registered service. Empty list if unregistered."""
    if _schedules_loader is None:
        log.debug("backup_hook: load_schedules called but no service registered; returning []")
        return []
    return _schedules_loader()
