"""R9 — the backup scheduler records an auditable run history and isolates failures.

schedule_tick used to keep only `last_run_at` (one timestamp, no outcome), and a failing
backup_now would crash the per-minute tick and block the other due schedules. Now each run
is recorded (ok + backup_id, or error), and a failure is contained.
"""
from __future__ import annotations

import api.routers.backup.routes_schedules as rs
from api.routers.backup.models import Schedule

_DUE = "2000-01-01T00:00:00+00:00"  # far in the past => always due
_NOW = "2024-06-01T00:00:00Z"


def _due_schedule():
    return Schedule(project="P", frequency="daily", next_run_at=_DUE, enabled=True)


def test_tick_records_a_successful_run(monkeypatch):
    captured = []
    sched = _due_schedule()
    monkeypatch.setattr(rs, "_load_schedules", lambda: [sched])
    monkeypatch.setattr(rs, "_save_schedules", lambda items: None)
    monkeypatch.setattr(rs, "_apply_retention", lambda *a, **k: None)
    monkeypatch.setattr(rs, "_append_run_history", lambda e: captured.append(e))
    monkeypatch.setattr(rs, "backup_now", lambda req: {"backup_id": "B1"})

    out = rs.schedule_tick(now_iso=_NOW)
    assert out["ok"] is True
    assert out["ran"] and out["ran"][0]["backup_id"] == "B1"
    assert captured and captured[0]["status"] == "ok" and captured[0]["backup_id"] == "B1"
    assert captured[0]["project"] == "P"
    # the schedule advanced (not stuck re-running every minute)
    assert sched.next_run_at != _DUE and sched.last_run_at is not None


def test_tick_isolates_a_failing_backup(monkeypatch):
    captured = []
    sched = _due_schedule()
    monkeypatch.setattr(rs, "_load_schedules", lambda: [sched])
    monkeypatch.setattr(rs, "_save_schedules", lambda items: None)
    monkeypatch.setattr(rs, "_apply_retention", lambda *a, **k: None)
    monkeypatch.setattr(rs, "_append_run_history", lambda e: captured.append(e))

    def boom(req):
        raise RuntimeError("pg_dump failed")
    monkeypatch.setattr(rs, "backup_now", boom)

    # The tick must NOT raise (the failure is contained) ...
    out = rs.schedule_tick(now_iso=_NOW)
    assert out["ok"] is True
    # ... it is recorded as an error ...
    assert captured and captured[0]["status"] == "error"
    assert "pg_dump failed" in captured[0]["error"]
    # ... and the schedule still advances (a broken schedule doesn't hammer every minute).
    assert sched.next_run_at != _DUE


def test_history_endpoint_filters_and_orders(monkeypatch):
    rows = [
        {"schedule_id": "a", "project": "P1", "ran_at": "t1", "status": "ok"},
        {"schedule_id": "b", "project": "P2", "ran_at": "t2", "status": "error"},
        {"schedule_id": "c", "project": "P1", "ran_at": "t3", "status": "ok"},
    ]
    monkeypatch.setattr(rs, "_load_run_history", lambda: rows)
    # most-recent-first, filtered by project
    out = rs.schedule_history(project="P1", limit=100)
    assert [r["schedule_id"] for r in out] == ["c", "a"]
    # limit applies to the most-recent N
    assert rs.schedule_history(project=None, limit=1) == [rows[-1]]
