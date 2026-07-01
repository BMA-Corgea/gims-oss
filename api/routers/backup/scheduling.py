# api/routers/backup/scheduling.py
#
# Schedule persistence (S3-aware JSON store) + next-run computation + retention.
# Moved VERBATIM from the former single-file api/routers/backup.py (no logic
# changes).

from pathlib import Path
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
import shutil

from api import i_o

from ._router import log
from .paths import _cfg_root, _now_utc, _backups_root
from .fsio import _save_json_s3
from .models import Schedule


# ──────────────────────────────────────────────────────────────────────────────
# Simple JSON store (schedules) — now S3-aware
# ──────────────────────────────────────────────────────────────────────────────
def _schedules_path() -> Path:
    return _cfg_root() / "schedules.json"

def _load_schedules() -> list[Schedule]:
    path = _schedules_path()
    try:
        data = i_o.load_data(path)  # S3-aware read + JSON parse
        if not data:
            return []
        return [Schedule(**s) for s in data]
    except Exception as e:
        log.debug(f"[S3] failed to read schedules: {e}")
        return []

def _save_schedules(items: List[Schedule]):
    _save_json_s3(_schedules_path(), [s.dict() for s in items])

# ──────────────────────────────────────────────────────────────────────────────
# Run history (R9): an auditable, capped log of each scheduled execution + outcome.
# A Schedule only kept `last_run_at` (one timestamp, no result); this records every
# tick-driven run — when, which schedule/project, success or failure, the backup id or
# the error — so scheduled backups are accountable (Part-11) and silent failures surface.
# ──────────────────────────────────────────────────────────────────────────────
_HISTORY_CAP = 500

def _history_path() -> Path:
    return _cfg_root() / "schedule_history.json"

def _load_run_history() -> List[dict]:
    try:
        data = i_o.load_data(_history_path())  # S3-aware read + JSON parse
        return data if isinstance(data, list) else []
    except Exception as e:
        log.debug(f"[S3] failed to read schedule history: {e}")
        return []

def _append_run_history(entry: dict) -> None:
    """Append one run record (most-recent-last), capped to ``_HISTORY_CAP``. Best-effort:
    a history-write failure must never break a scheduled backup."""
    try:
        hist = _load_run_history()
        hist.append(entry)
        if len(hist) > _HISTORY_CAP:
            hist = hist[-_HISTORY_CAP:]
        _save_json_s3(_history_path(), hist)
    except Exception as e:
        log.debug(f"[S3] failed to write schedule history: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# Scheduling helpers
# ──────────────────────────────────────────────────────────────────────────────
def _compute_next_run(s: Schedule, from_time: Optional[datetime] = None) -> datetime:
    t = (from_time or _now_utc()).replace(second=0, microsecond=0)
    minute = int(s.minute or 0)
    hour = int(s.hour or 0)
    if s.frequency == "hourly":
        candidate = t.replace(minute=minute)
        if candidate <= t:
            candidate += timedelta(hours=1)
        return candidate
    if s.frequency == "daily":
        candidate = t.replace(hour=hour, minute=minute)
        if candidate <= t:
            candidate += timedelta(days=1)
        return candidate
    if s.frequency == "weekly":
        dow = int(s.dow if s.dow is not None else 0)  # 0=Mon
        days_ahead = (dow - t.weekday()) % 7
        candidate = t + timedelta(days=days_ahead)
        candidate = candidate.replace(hour=hour, minute=minute)
        if candidate <= t:
            candidate += timedelta(days=7)
        return candidate
    if s.frequency == "monthly":
        dom = int(s.dom if s.dom is not None else 1)
        candidate = t.replace(day=min(dom, 28), hour=hour, minute=minute)
        if candidate <= t:
            month = candidate.month + 1
            year = candidate.year + (1 if month == 13 else 0)
            month = 1 if month == 13 else month
            candidate = candidate.replace(year=year, month=month, day=min(dom, 28))
        return candidate
    return t + timedelta(hours=1)

def _apply_retention(project: str, keep_last: Optional[int]):
    if not keep_last or keep_last <= 0:
        return
    root = _backups_root() / project
    if not root.exists():
        return
    entries: List[Tuple[str, Path]] = []
    for dated in sorted(root.iterdir(), reverse=True):
        if not dated.is_dir():
            continue
        for bdir in sorted(dated.iterdir(), reverse=True):
            if not bdir.is_dir():
                continue
            entries.append((f"{dated.name}/{bdir.name}", bdir))
    if len(entries) <= keep_last:
        return
    to_delete = entries[keep_last:]
    for _, path in to_delete:
        log.debug("retention: deleting", path)
        shutil.rmtree(path, ignore_errors=True)
