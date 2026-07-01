# api/routers/backup/routes_schedules.py
#
# Schedules API + the per-minute schedule tick. Handlers moved VERBATIM from the
# former single-file api/routers/backup.py (no logic changes). Registered LAST
# so the route REGISTRATION ORDER matches the original file.
#
# schedule_tick drives a backup via backup_now -> one-way import from
# .routes_backups (routes_backups must NOT import this module).

from fastapi import Body, Query, Path as FPath
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

from core.errors import AppError

from ._router import router, log
from .paths import _now_utc
from .models import Schedule, BackupNowRequest
from .scheduling import (
    _load_schedules,
    _save_schedules,
    _compute_next_run,
    _apply_retention,
    _load_run_history,
    _append_run_history,
)
from .routes_backups import backup_now


# ──────────────────────────────────────────────────────────────────────────────
# Schedules API (S3-aware JSON for schedules.json)
# ──────────────────────────────────────────────────────────────────────────────
class ScheduleRequest(BaseModel):
    id: Optional[str] = None
    project: str
    type: str = "hybrid"
    frequency: str
    minute: int = 0
    hour: int = 2
    dow: Optional[int] = None
    dom: Optional[int] = None
    retention_keep: Optional[int] = 10
    enabled: bool = True
    notes: Optional[str] = None

@router.get("/schedules")
def list_schedules(project: Optional[str] = Query(None)):
    items = _load_schedules()
    if project:
        items = [s for s in items if s.project == project]
    return [s.dict() for s in items]

@router.post("/schedules")
def create_or_update_schedule(req: ScheduleRequest):
    items = _load_schedules()
    if req.id:
        idx = next((i for i, s in enumerate(items) if s.id == req.id), None)
        if idx is None:
            raise AppError("SCHEDULE_NOT_FOUND", "schedule id not found", status=404,
                           details={"schedule_id": req.id})
        s = items[idx]
        s.project = req.project
        s.type = req.type
        s.frequency = req.frequency
        s.minute = req.minute
        s.hour = req.hour
        s.dow = req.dow
        s.dom = req.dom
        s.retention_keep = req.retention_keep
        s.enabled = req.enabled
        s.notes = req.notes
        s.next_run_at = _compute_next_run(s).isoformat()
        items[idx] = s
    else:
        s = Schedule(
            project=req.project,
            type=req.type,
            frequency=req.frequency,
            minute=req.minute,
            hour=req.hour,
            dow=req.dow,
            dom=req.dom,
            retention_keep=req.retention_keep,
            enabled=req.enabled,
            notes=req.notes,
        )
        s.next_run_at = _compute_next_run(s).isoformat()
        items.append(s)
    _save_schedules(items)
    return {"ok": True, "schedules": [s.dict() for s in items]}

@router.delete("/schedules/{sch_id}")
def delete_schedule(sch_id: str = FPath(...)):
    items = _load_schedules()
    new_items = [s for s in items if s.id != sch_id]
    if len(new_items) == len(items):
        raise AppError("SCHEDULE_NOT_FOUND", "schedule id not found", status=404,
                       details={"schedule_id": sch_id})
    _save_schedules(new_items)
    return {"ok": True, "deleted": sch_id}

@router.post("/schedule/tick")
def schedule_tick(now_iso: Optional[str] = Body(None, embed=True)):
    """
    Community edition: have system cron call this every minute.
    Enterprise: server can call internally on a loop.
    Runs due schedules and updates next_run_at/last_run_at. Applies retention.
    """
    now = _now_utc() if not now_iso else datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    ran: List[Dict[str, Any]] = []
    items = _load_schedules()
    changed = False
    for s in items:
        if not s.enabled:
            continue
        if not s.next_run_at:
            s.next_run_at = _compute_next_run(s, from_time=now).isoformat()
            changed = True
            continue
        next_run = datetime.fromisoformat(s.next_run_at.replace("Z", "+00:00"))
        if next_run <= now:
            log.debug("schedule due:", s.id, s.project, s.frequency, "@", s.next_run_at)
            ran_at = now.replace(microsecond=0).isoformat()
            # Run the scheduled backup with per-schedule isolation: a failure records a
            # history entry and is skipped, so it never blocks the other due schedules nor
            # crashes the per-minute tick (R9). next_run_at advances either way, so a
            # persistently-failing schedule doesn't hammer every minute.
            backup_ok = False
            try:
                req = BackupNowRequest(
                    project=s.project,
                    type=s.type,
                    paranoid=False,
                    notes=f"(scheduled {s.frequency})",
                )
                res = backup_now(req)
                backup_id = res["backup_id"]
                ran.append({"schedule_id": s.id, "backup_id": backup_id, "project": s.project})
                _append_run_history({
                    "schedule_id": s.id, "project": s.project, "frequency": s.frequency,
                    "ran_at": ran_at, "status": "ok", "backup_id": backup_id,
                })
                backup_ok = True
            except Exception as e:
                log.warning("[schedule] scheduled backup failed:", s.id, s.project, repr(e))
                ran.append({"schedule_id": s.id, "project": s.project, "error": str(e)})
                _append_run_history({
                    "schedule_id": s.id, "project": s.project, "frequency": s.frequency,
                    "ran_at": ran_at, "status": "error", "error": str(e),
                })
            # Retention is post-success cleanup, NOT part of backup success/failure — keep it out
            # of the try so a retention error can't misrecord a good backup as failed.
            if backup_ok:
                try:
                    _apply_retention(s.project, s.retention_keep)
                except Exception as re:
                    log.warning("[schedule] retention failed (backup still ok):", s.id, repr(re))
            s.last_run_at = ran_at
            s.next_run_at = _compute_next_run(s, from_time=now + timedelta(seconds=1)).isoformat()
            changed = True
    if changed:
        _save_schedules(items)
    return {"ok": True, "ran": ran, "now": now.replace(microsecond=0).isoformat()}


@router.get("/schedule/history")
def schedule_history(
    project: Optional[str] = Query(None, description="Filter to one project"),
    limit: int = Query(100, ge=1, le=500, description="Most-recent N runs"),
):
    """Auditable history of scheduled backup runs (most recent first): each tick-driven
    execution with its outcome (ok + backup_id, or error)."""
    hist = _load_run_history()
    if project:
        hist = [h for h in hist if h.get("project") == project]
    return list(reversed(hist[-limit:]))
