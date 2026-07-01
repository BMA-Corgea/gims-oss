"""R17 — the archive plan executor is atomic-or-rollback across DB + FS.

The executor used to commit the DB before each filesystem op, so a mid-plan failure
stranded files (and partial DB state) with no way back. It now runs every FileOp
through a journal that records the inverse and commits the DB once at the end; on any
failure it rolls back the DB AND replays the FS inverses. These tests pin that a
failing plan leaves the filesystem exactly as it started.
"""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

from core.archive_workbench import Plan, FileOp


@pytest.fixture()
def seams(tmp_path: Path, monkeypatch):
    s = importlib.import_module("api.routers.archive_workbench._seams")
    hot = tmp_path / "objects.db"
    arc = tmp_path / "archive.db"
    sqlite3.connect(hot.as_posix()).close()
    sqlite3.connect(arc.as_posix()).close()

    def fake_resolve(base, key, **kw):
        if key == "object_sql_db":
            return hot
        if key == "archive_sql_db":
            return arc
        return tmp_path / key

    monkeypatch.setattr(s, "resolve_path", fake_resolve, raising=True)
    monkeypatch.setattr(s, "get_db_uri", lambda key: "", raising=True)  # force the SQLite branch
    return s


def test_failed_plan_rolls_the_filesystem_back(seams, tmp_path: Path):
    """A plan whose 2nd op fails must undo the 1st op's move."""
    src = tmp_path / "run_R1"
    src.mkdir()
    (src / "data.txt").write_text("hello", encoding="utf-8")
    dst = tmp_path / "archive" / "R1"
    missing = tmp_path / "does_not_exist"

    plan = Plan(description="archive R1", steps=[
        FileOp(op="move", src=str(src), dst=str(dst)),
        FileOp(op="move", src=str(missing), dst=str(tmp_path / "x")),  # raises -> rollback
    ])

    with pytest.raises(Exception):
        seams._execute_plan(tmp_path, plan)

    # Filesystem fully reverted: the first move was undone.
    assert src.exists() and (src / "data.txt").read_text(encoding="utf-8") == "hello"
    assert not dst.exists()


def test_failed_plan_restores_a_deleted_file(seams, tmp_path: Path):
    """A delete is moved aside first, so a later failure restores it."""
    keep = tmp_path / "marker.txt"
    keep.write_text("important", encoding="utf-8")
    missing = tmp_path / "nope"

    plan = Plan(description="restore", steps=[
        FileOp(op="delete", src=str(keep)),
        FileOp(op="move", src=str(missing), dst=str(tmp_path / "y")),  # raises -> rollback
    ])

    with pytest.raises(Exception):
        seams._execute_plan(tmp_path, plan)

    # The deleted file is restored with its original content.
    assert keep.exists() and keep.read_text(encoding="utf-8") == "important"


def test_successful_plan_applies_and_purges_backups(seams, tmp_path: Path):
    """The happy path still moves files + deletes (no backup left behind)."""
    src = tmp_path / "run_R2"
    src.mkdir()
    (src / "d.txt").write_text("x", encoding="utf-8")
    dst = tmp_path / "archive" / "R2"
    gone = tmp_path / "old_marker.txt"
    gone.write_text("bye", encoding="utf-8")

    plan = Plan(description="ok", steps=[
        FileOp(op="move", src=str(src), dst=str(dst)),
        FileOp(op="delete", src=str(gone)),
    ])

    res = seams._execute_plan(tmp_path, plan)
    assert res["ok"] is True
    assert dst.exists() and (dst / "d.txt").read_text(encoding="utf-8") == "x"
    assert not src.exists()
    assert not gone.exists()
    # no journal backup files linger
    assert not list(tmp_path.glob("*__arcjournal_bak_*"))
