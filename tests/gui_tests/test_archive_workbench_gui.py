# tests/gui_tests/test_archive_workbench_gui.py
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ──────────────────────────────────────────────────────────────────────────────
# Helper: dynamically import the target module (gui/archive_workbench_gui.py
# with fallback to gui/archive_gui.py)
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def awg_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    mod_path = repo_root / "gui" / "archive_workbench_gui.py"
    if not mod_path.exists():
        alt = repo_root / "gui" / "archive_gui.py"
        mod_path = alt if alt.exists() else mod_path
    spec = importlib.util.spec_from_file_location("archive_workbench_gui", mod_path)
    assert spec and spec.loader, f"Could not load module at {mod_path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["archive_workbench_gui"] = mod
    spec.loader.exec_module(mod)
    return mod


# ──────────────────────────────────────────────────────────────────────────────
# Fixture: fabricate a temp project & monkeypatch resolver + i/o helpers
# Also force the module to use local FS fallbacks instead of S3 json_proxy.
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def temp_project(tmp_path: Path, awg_module, monkeypatch) -> Dict[str, Any]:
    """
    Creates a minimal FS layout:
      <tmp>/projects/TestProject/
        - verbs/<verb_group>/data_dumps/<run_id>/
        - backups/data_dump_archive/<verb_group>/
        - objects.db (SQLite)
        - archive.db (SQLite)
      And patches resolve_path/get_db_uri/load_schema/... to point here.

    IMPORTANT: We stub the module's _jp_* helpers so it won't consult
    json_proxy/S3 for listing/existence — tests operate purely on the temp FS.
    """
    root = tmp_path / "projects"
    root.mkdir(parents=True, exist_ok=True)
    project_name = "TestProject"
    project_path = root / project_name
    (project_path / "verbs").mkdir(parents=True, exist_ok=True)
    (project_path / "backups" / "data_dump_archive").mkdir(parents=True, exist_ok=True)

    # DB locations
    hot_db = project_path / "objects.db"
    arc_db = project_path / "archive.db"
    for db in (hot_db, arc_db):
        sqlite3.connect(db.as_posix()).close()

    # -------------------------- monkeypatch resolve_path -----------------------
    def fake_resolve_path(base: Path, key: str, **kwargs) -> Path:
        if key == "project_root":
            return root
        if key == "archive_policy":
            return project_path / "archive_policy.json"
        if key == "verbs_dir":
            return project_path / "verbs"
        if key == "verb_group":
            vg = kwargs.get("verb_group") or "Tests"
            return project_path / "verbs" / vg
        if key == "data_dump_dir":
            vg = kwargs.get("verb_group") or "Tests"
            rid = kwargs.get("run_id") or "R-UNKNOWN"
            return project_path / "verbs" / vg / "data_dumps" / rid
        if key == "data_dump_archive":
            vg = kwargs.get("verb_group") or "Tests"
            return project_path / "backups" / "data_dump_archive" / vg
        if key == "object_sql_db":
            return hot_db
        if key == "archive_sql_db":
            return arc_db
        return project_path / key

    monkeypatch.setattr(awg_module, "resolve_path", fake_resolve_path, raising=True)

    # Force SQLite branch
    monkeypatch.setattr(awg_module, "get_db_uri", lambda key: "", raising=True)

    # ---------------------- Disable S3/json_proxy behaviors --------------------
    # Make the module prefer its local-fallback _jp_* helpers by:
    # 1) flipping the feature flag
    # 2) stubbing _jp_list_projects / _jp_project_exists to read our tmp FS
    monkeypatch.setattr(awg_module, "_HAS_S3", False, raising=True)

    def fake_jp_list_projects() -> List[str]:
        return sorted([p.name for p in root.iterdir() if p.is_dir()])

    def fake_jp_project_exists(name: str) -> bool:
        return (root / name).exists()

    monkeypatch.setattr(awg_module, "_jp_list_projects", fake_jp_list_projects, raising=True)
    monkeypatch.setattr(awg_module, "_jp_project_exists", fake_jp_project_exists, raising=True)
    # _jp_list_dirnames/_jp_prefix_exists fall back to Path-based logic when _HAS_S3=False,
    # so we don't need to override them.

    # ------------------------ monkeypatch i_o schema/logs ----------------------
    NOUNS = {"Sample": {"primary_id_field": "id"}}
    VERBS = {
        "HPLC": {
            "verb_group": "Chemistry",
            "data_entry_schema": {"set_up_inputs": {"noun_type_ref": "Sample"}},
        }
    }

    def fake_load_schema(project_path_arg: Path, kind: str):
        if kind == "noun":
            return NOUNS
        if kind == "verb":
            return VERBS
        return {}

    def fake_get_verb_schema(project_path_arg: Path, test_type: Optional[str]):
        return VERBS.get(test_type or "", {})

    def fake_get_verb_group_log_config(project_path_arg: Path, verb_group: str):
        return {"primary_id": "run_id", "verb_field": "test_type"}

    RUN_LOG_ENTRIES = [
        {"run_id": "R1", "test_type": "HPLC"},
        {"run_id": "R2", "test_type": "HPLC"},
    ]

    def fake_load_verb_group_log(project_path_arg: Path, verb_group: str):
        return RUN_LOG_ENTRIES

    monkeypatch.setattr(awg_module, "load_schema", fake_load_schema, raising=True)
    monkeypatch.setattr(awg_module, "get_verb_schema", fake_get_verb_schema, raising=True)
    monkeypatch.setattr(awg_module, "get_verb_group_log_config", fake_get_verb_group_log_config, raising=True)
    monkeypatch.setattr(awg_module, "load_verb_group_log", fake_load_verb_group_log, raising=True)

    # Optional: silence module debug during tests
    monkeypatch.setattr(awg_module, "DEBUG_ENABLED", False, raising=True)

    return {
        "root": root,
        "project": project_name,
        "project_path": project_path,
        "hot_db": hot_db,
        "arc_db": arc_db,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Fixture: FastAPI app mounting the archive_workbench router
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def client(awg_module, temp_project) -> TestClient:
    app = FastAPI()
    app.include_router(awg_module.router)  # router has prefix="/api/archive_workbench"
    return TestClient(app)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers: create noun tables + sample data
# ──────────────────────────────────────────────────────────────────────────────
def _mk_hot_noun_table(db: Path, table: str = "noun_Sample"):
    con = sqlite3.connect(db.as_posix())
    cur = con.cursor()
    cur.execute(
        f'CREATE TABLE IF NOT EXISTS "{table}" ('
        'id TEXT PRIMARY KEY, archived INTEGER, archived_at TEXT, _runID TEXT)'
    )
    con.commit()
    con.close()


def _mk_arc_noun_table(db: Path, table: str = "noun_Sample"):
    con = sqlite3.connect(db.as_posix())
    cur = con.cursor()
    cur.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (id TEXT PRIMARY KEY)')
    con.commit()
    con.close()


def _insert_hot_noun(db: Path, pid: str, archived: int = 0, run_id: Optional[str] = None, table="noun_Sample"):
    con = sqlite3.connect(db.as_posix())
    cur = con.cursor()
    cur.execute(
        f'INSERT OR REPLACE INTO "{table}" (id, archived, archived_at, _runID) VALUES (?,?,?,?)',
        (pid, archived, "2024-01-01T00:00:00Z" if archived else None, run_id),
    )
    con.commit()
    con.close()


def _insert_arc_noun(db: Path, pid: str, table="noun_Sample"):
    con = sqlite3.connect(db.as_posix())
    cur = con.cursor()
    cur.execute(f'INSERT OR REPLACE INTO "{table}" (id) VALUES (?)', (pid,))
    con.commit()
    con.close()


# ──────────────────────────────────────────────────────────────────────────────
# Smoke tests: projects & verb groups
# ──────────────────────────────────────────────────────────────────────────────
def test_list_projects_and_verb_groups(client: TestClient, temp_project: Dict[str, Any]):
    (temp_project["project_path"]).mkdir(parents=True, exist_ok=True)
    (temp_project["project_path"] / "verbs" / "Chemistry").mkdir(parents=True, exist_ok=True)

    r = client.get("/api/archive_workbench/projects")
    assert r.status_code == 200
    assert temp_project["project"] in r.json()

    r = client.get(f"/api/archive_workbench/{temp_project['project']}/verb_groups")
    assert r.status_code == 200
    groups = r.json()
    assert "Chemistry" in groups


# ──────────────────────────────────────────────────────────────────────────────
# Noun archive candidates + preview/apply restore
# ──────────────────────────────────────────────────────────────────────────────
def test_noun_candidates_and_restore(client: TestClient, temp_project: Dict[str, Any]):
    _mk_hot_noun_table(temp_project["hot_db"])
    _mk_arc_noun_table(temp_project["arc_db"])
    _insert_hot_noun(temp_project["hot_db"], "S1", archived=1)
    _insert_arc_noun(temp_project["arc_db"], "H1")

    r = client.get(f"/api/archive_workbench/{temp_project['project']}/nouns/archived")
    assert r.status_code == 200
    payload = r.json()
    assert "Sample" in payload
    assert payload["Sample"]["strategy"] == "soft"
    assert "S1" in payload["Sample"]["ids"]

    r = client.get(f"/api/archive_workbench/{temp_project['project']}/nouns/archived?strategy=hard")
    assert r.status_code == 200
    payload = r.json()
    assert "H1" in payload["Sample"]["ids"]

    r = client.post(
        f"/api/archive_workbench/{temp_project['project']}/nouns/restore/preview?strategy=hard",
        json={"Sample": ["H1"]},
    )
    assert r.status_code == 200
    pv = r.json()["Sample"]
    assert pv["strategy"] == "hard"
    assert pv["drift_detected"] in (True, False)
    assert "plan" in pv and isinstance(pv["plan"].get("steps", []), list)

    r = client.post(
        f"/api/archive_workbench/{temp_project['project']}/nouns/restore/apply?strategy=soft",
        json={"Sample": ["S1"]},
    )
    assert r.status_code == 200
    res = r.json()
    assert res["Sample"]["ok"] is True

    con = sqlite3.connect(temp_project["hot_db"].as_posix())
    cur = con.cursor()
    row = cur.execute('SELECT archived FROM "noun_Sample" WHERE id=?', ("S1",)).fetchone()
    con.close()
    assert row and (row[0] == 0 or row[0] is None)


# ──────────────────────────────────────────────────────────────────────────────
# Run archive/apply + index + list_runs + restore (with linked nouns)
# ──────────────────────────────────────────────────────────────────────────────
def test_run_archive_and_restore_flow(client: TestClient, temp_project: Dict[str, Any]):
    project = temp_project["project"]
    proj_path = temp_project["project_path"]
    verbs = proj_path / "verbs" / "Chemistry"
    hot_r1 = verbs / "data_dumps" / "R1"
    hot_r1.mkdir(parents=True, exist_ok=True)
    (hot_r1 / "foo.txt").write_text("hello", encoding="utf-8")

    _mk_hot_noun_table(temp_project["hot_db"])
    _mk_arc_noun_table(temp_project["arc_db"])
    _insert_hot_noun(temp_project["hot_db"], "N1", archived=0, run_id="R1")

    r = client.get(f"/api/archive_workbench/{project}/runs/list?verb_group=Chemistry&where=active")
    assert r.status_code == 200
    assert "R1" in r.json().get("runs", [])

    r = client.post(
        f"/api/archive_workbench/{project}/runs/archive/preview?strategy=hard",
        json=[{"run_id": "R1", "verb_group": "Chemistry"}],
    )
    assert r.status_code == 200
    plan = r.json()
    assert "steps" in plan

    r = client.post(
        f"/api/archive_workbench/{project}/runs/archive/apply?strategy=hard",
        json=[{"run_id": "R1", "verb_group": "Chemistry"}],
    )
    assert r.status_code == 200
    res = r.json()
    assert res["ok"] is True

    arc_dir = proj_path / "backups" / "data_dump_archive" / "Chemistry" / "R1"
    assert arc_dir.exists()
    assert (arc_dir / "foo.txt").exists()
    assert not hot_r1.exists()

    con = sqlite3.connect(temp_project["arc_db"].as_posix())
    cur = con.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS runs_archive_index (project TEXT, run_id TEXT, verb TEXT, verb_group TEXT, archive_path TEXT, archived_at TEXT, strategy TEXT, notes TEXT)"
    )
    row = cur.execute(
        'SELECT project, run_id, verb_group, strategy FROM "runs_archive_index" WHERE run_id=?',
        ("R1",),
    ).fetchone()
    con.close()
    assert row is not None
    assert row[0] == project and row[1] == "R1" and row[2] == "Chemistry" and row[3] == "hard"

    con = sqlite3.connect(temp_project["hot_db"].as_posix())
    cur = con.cursor()
    row = cur.execute('SELECT archived FROM "noun_Sample" WHERE id=?', ("N1",)).fetchone()
    con.close()
    assert row and (row[0] == 1)

    r = client.get(f"/api/archive_workbench/{project}/runs/list?verb_group=Chemistry&where=active")
    assert r.status_code == 200
    assert "R1" not in r.json().get("runs", [])

    r = client.get(f"/api/archive_workbench/{project}/runs/list?verb_group=Chemistry&where=archived")
    assert r.status_code == 200
    assert "R1" in r.json().get("runs", [])

    r = client.post(
        f"/api/archive_workbench/{project}/runs/restore/apply",
        json={"verb_group": "Chemistry", "run_ids": ["R1"]},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] is True

    assert (verbs / "data_dumps" / "R1" / "foo.txt").exists()

    con = sqlite3.connect(temp_project["hot_db"].as_posix())
    cur = con.cursor()
    row = cur.execute('SELECT archived FROM "noun_Sample" WHERE id=?', ("N1",)).fetchone()
    con.close()
    assert row and (row[0] == 0 or row[0] is None)



# ──────────────────────────────────────────────────────────────────────────────
# APPENDED SECTION: S3 + RDS smoke tests (non-destructive)
# These tests aim to validate that the module's S3/RDS awareness paths work.
# They auto-skip if your module doesn't expose the expected hooks.
# ──────────────────────────────────────────────────────────────────────────────

# ---------- Utilities for S3 fake proxy ----------

class _FakeJsonProxy:
    """
    Minimal S3-like proxy over a local temp directory.
    We emulate "prefix" operations with Path ops under a provided root.
    """
    def __init__(self, bucket_root: Path):
        self.root = bucket_root

    # project listing helpers
    def list_projects(self) -> List[str]:
        return sorted([p.name for p in self.root.iterdir() if p.is_dir()])

    def project_exists(self, project: str) -> bool:
        return (self.root / project).exists()

    # generic prefix helpers
    def list_dirnames(self, prefix: str) -> List[str]:
        base = Path(prefix)
        if not base.exists():
            return []
        return sorted([p.name for p in base.iterdir() if p.is_dir()])

    def prefix_exists(self, prefix: str) -> bool:
        return (self.root / prefix).exists()

    def copy_prefix(self, src: str, dst: str) -> None:
        import shutil
        s = self.root / src
        d = self.root / dst
        d.parent.mkdir(parents=True, exist_ok=True)
        if s.is_dir():
            if d.exists():
                shutil.rmtree(d)
            shutil.copytree(s, d)
        elif s.is_file():
            shutil.copy2(s, d)

    def delete_prefix(self, prefix: str) -> None:
        import shutil
        p = self.root / prefix
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()


# ---------- S3 env fixture ----------

@pytest.fixture()
def s3_project(tmp_path: Path, awg_module, monkeypatch) -> Dict[str, Any]:
    """
    Emulates your real S3 layout:
        gims-bucket/GIMS-Project/projects/<project_name>/
    The module will list under 'GIMS-Project/' and see 'projects/S3TestProject'.
    """
    if not hasattr(awg_module, "_HAS_S3"):
        pytest.skip("_HAS_S3 flag not present on module; skipping S3 tests")

    # Fake bucket with correct nested layout
    bucket = tmp_path / "s3bucket" / "GIMS-Project"
    project_name = "S3TestProject"
    project_root = bucket / "projects"
    project_path = project_root / project_name
    (project_path / "verbs" / "Chemistry" / "data_dumps").mkdir(parents=True, exist_ok=True)
    (project_path / "backups" / "data_dump_archive" / "Chemistry").mkdir(parents=True, exist_ok=True)

    # DBs
    hot_db = project_path / "objects.db"
    arc_db = project_path / "archive.db"
    sqlite3.connect(hot_db.as_posix()).close()
    sqlite3.connect(arc_db.as_posix()).close()

    # Correct resolver — include GIMS-Project segment
    def s3_resolve_path(base: Path, key: str, **kwargs) -> Path:
        if key == "project_root":
            return project_root
        if key == "verbs_dir":
            return project_path / "verbs"
        if key == "verb_group":
            vg = kwargs.get("verb_group") or "Chemistry"
            return project_path / "verbs" / vg
        if key == "data_dump_dir":
            vg = kwargs.get("verb_group") or "Chemistry"
            rid = kwargs.get("run_id") or "R-UNKNOWN"
            return project_path / "verbs" / vg / "data_dumps" / rid
        if key == "data_dump_archive":
            vg = kwargs.get("verb_group") or "Chemistry"
            return project_path / "backups" / "data_dump_archive" / vg
        if key == "object_sql_db":
            return hot_db
        if key == "archive_sql_db":
            return arc_db
        return project_path / key

    monkeypatch.setattr(awg_module, "resolve_path", s3_resolve_path, raising=True)
    monkeypatch.setattr(awg_module, "_HAS_S3", True, raising=True)

    # --- S3 proxy ---
    fake_root = tmp_path / "s3bucket"
    fake_proxy = _FakeJsonProxy(fake_root)
    if hasattr(awg_module, "json_proxy"):
        monkeypatch.setattr(awg_module, "json_proxy", fake_proxy, raising=True)

    # Force the _jp_* methods to list from the correct prefix depth
    def _jp_list_projects():
        # Return just the project name
        return ["S3TestProject"]

    def _jp_project_exists(name: str) -> bool:
        # Check against just the project name
        return name == "S3TestProject"

    monkeypatch.setattr(awg_module, "_jp_list_projects", _jp_list_projects, raising=False)
    monkeypatch.setattr(awg_module, "_jp_project_exists", _jp_project_exists, raising=False)

    # Schema + logging stubs (same as before)
    NOUNS = {"Sample": {"primary_id_field": "id"}}
    VERBS = {"HPLC": {"verb_group": "Chemistry", "data_entry_schema": {"set_up_inputs": {"noun_type_ref": "Sample"}}}}
    RUN_LOG_ENTRIES = [{"run_id": "R1", "test_type": "HPLC"}]

    for name, func in {
        "load_schema": lambda *_: NOUNS if _[1] == "noun" else VERBS,
        "get_verb_schema": lambda *_: VERBS["HPLC"],
        "get_verb_group_log_config": lambda *_: {"primary_id": "run_id", "verb_field": "test_type"},
        "load_verb_group_log": lambda *_: RUN_LOG_ENTRIES,
    }.items():
        monkeypatch.setattr(awg_module, name, func, raising=True)

    if hasattr(awg_module, "DEBUG_ENABLED"):
        monkeypatch.setattr(awg_module, "DEBUG_ENABLED", False, raising=True)

    return {
        "bucket": fake_root,
        "project_root": project_root,
        "project": project_name,
        "project_path": project_path,
        "hot_db": hot_db,
        "arc_db": arc_db,
    }


@pytest.fixture()
def s3_client(awg_module, s3_project) -> TestClient:
    app = FastAPI()
    app.include_router(awg_module.router)
    return TestClient(app)


def test_s3_projects_and_run_archive_restore(s3_client: TestClient, s3_project: Dict[str, Any], awg_module):
    # Skip if module can’t do S3 workflows
    if not getattr(awg_module, "_HAS_S3", False):
        pytest.skip("Module not S3-aware")

    proj = s3_project["project"]
    verbs_dir = s3_project["project_path"] / "verbs" / "Chemistry"
    data_dump = verbs_dir / "data_dumps" / "R1"
    data_dump.mkdir(parents=True, exist_ok=True)
    (data_dump / "hello.txt").write_text("hi", encoding="utf-8")

    # Projects listing (S3 path)
    r = s3_client.get("/api/archive_workbench/projects")
    assert r.status_code == 200
    assert proj in r.json()

    # Verb groups (S3 path)
    r = s3_client.get(f"/api/archive_workbench/{proj}/verb_groups")
    assert r.status_code == 200
    assert "Chemistry" in r.json()

    # Archive the run (S3 copy/delete should fire under the hood)
    r = s3_client.post(
        f"/api/archive_workbench/{proj}/runs/archive/apply?strategy=hard",
        json=[{"run_id": "R1", "verb_group": "Chemistry"}],
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True

    # Assert file moved to archive
    arc_dir = s3_project["project_path"] / "backups" / "data_dump_archive" / "Chemistry" / "R1"
    assert arc_dir.exists()
    assert (arc_dir / "hello.txt").exists()
    assert not data_dump.exists()

    # Restore the run
    r = s3_client.post(
        f"/api/archive_workbench/{proj}/runs/restore/apply",
        json={"verb_group": "Chemistry", "run_ids": ["R1"]},
    )
    assert r.status_code == 200
    out = r.json()
    assert out.get("ok") is True

    # Files back in hot area
    assert (verbs_dir / "data_dumps" / "R1" / "hello.txt").exists()


# ---------- RDS env fixture ----------

@pytest.fixture()
def rds_project(tmp_path: Path, awg_module, monkeypatch) -> Dict[str, Any]:
    """
    Simulates RDS mode:
      - get_db_uri returns a PostgreSQL-looking DSN (so module thinks 'pg/RDS').
      - psycopg may be unavailable → module should gracefully fall back to SQLite
        or its compatibility layer. We help by pointing resolver to local db files.
    The intent is to test the 'RDS-aware' code paths without needing a live Postgres.
    """
    # If module has a psycopg feature flag, force it "unavailable" to avoid real network
    if hasattr(awg_module, "_PSYCOPG_AVAILABLE"):
        monkeypatch.setattr(awg_module, "_PSYCOPG_AVAILABLE", False, raising=True)

    # Basic project layout on FS (module can still use FS for runs and verb groups)
    root = tmp_path / "projects"
    root.mkdir(parents=True, exist_ok=True)
    project_name = "RDSTestProject"
    project_path = root / project_name
    (project_path / "verbs" / "Chemistry" / "data_dumps").mkdir(parents=True, exist_ok=True)
    (project_path / "backups" / "data_dump_archive" / "Chemistry").mkdir(parents=True, exist_ok=True)

    hot_db = project_path / "objects.db"
    arc_db = project_path / "archive.db"
    sqlite3.connect(hot_db.as_posix()).close()
    sqlite3.connect(arc_db.as_posix()).close()

    # DSN returns PostgreSQL-looking URI to trigger 'pg' branch
    monkeypatch.setattr(
        awg_module,
        "get_db_uri",
        lambda key: "postgresql+asyncpg://user:pass@db.example.com:5432/nodes_db?ssl=require",
        raising=True,
    )

    def rds_resolve_path(base: Path, key: str, **kwargs) -> Path:
        if key == "project_root":
            return root
        if key == "verbs_dir":
            return project_path / "verbs"
        if key == "verb_group":
            vg = kwargs.get("verb_group") or "Chemistry"
            return project_path / "verbs" / vg
        if key == "data_dump_dir":
            vg = kwargs.get("verb_group") or "Chemistry"
            rid = kwargs.get("run_id") or "R-UNKNOWN"
            return project_path / "verbs" / vg / "data_dumps" / rid
        if key == "data_dump_archive":
            vg = kwargs.get("verb_group") or "Chemistry"
            return project_path / "backups" / "data_dump_archive" / vg
        if key == "object_sql_db":
            return hot_db
        if key == "archive_sql_db":
            return arc_db
        return project_path / key

    monkeypatch.setattr(awg_module, "resolve_path", rds_resolve_path, raising=True)

    # Provide the same schema/log stubs
    NOUNS = {"Sample": {"primary_id_field": "id"}}
    VERBS = {"HPLC": {"verb_group": "Chemistry", "data_entry_schema": {"set_up_inputs": {"noun_type_ref": "Sample"}}}}
    RUN_LOG_ENTRIES = [{"run_id": "R1", "test_type": "HPLC"}]

    def fake_load_schema(project_path_arg: Path, kind: str):
        if kind == "noun":
            return NOUNS
        if kind == "verb":
            return VERBS
        return {}

    def fake_get_verb_schema(project_path_arg: Path, test_type: Optional[str]):
        return VERBS.get(test_type or "", {})

    def fake_get_verb_group_log_config(project_path_arg: Path, verb_group: str):
        return {"primary_id": "run_id", "verb_field": "test_type"}

    def fake_load_verb_group_log(project_path_arg: Path, verb_group: str):
        return RUN_LOG_ENTRIES

    for name, func in {
        "load_schema": fake_load_schema,
        "get_verb_schema": fake_get_verb_schema,
        "get_verb_group_log_config": fake_get_verb_group_log_config,
        "load_verb_group_log": fake_load_verb_group_log,
    }.items():
        monkeypatch.setattr(awg_module, name, func, raising=True)

    if hasattr(awg_module, "DEBUG_ENABLED"):
        monkeypatch.setattr(awg_module, "DEBUG_ENABLED", False, raising=True)

    return {
        "root": root,
        "project": project_name,
        "project_path": project_path,
        "hot_db": hot_db,
        "arc_db": arc_db,
    }


@pytest.fixture()
def rds_client(awg_module, rds_project) -> TestClient:
    app = FastAPI()
    app.include_router(awg_module.router)
    return TestClient(app)


def test_rds_mode_runs_archive_restore_smoke(rds_client: TestClient, rds_project: Dict[str, Any], awg_module):
    """
    Smoke-test the RDS-aware path:
      - get_db_uri returns Postgres DSN
      - psycopg flagged as unavailable so module should gracefully continue (e.g., use SQLite fallback or logic-only)
      - verify endpoints don't error and basic archive/restore works on FS while DB writes don't crash
    """
    # If module insists on psycopg or lacks RDS branching, skip cleanly
    if hasattr(awg_module, "_PSYCOPG_AVAILABLE") and getattr(awg_module, "_PSYCOPG_AVAILABLE"):
        pytest.skip("psycopg available; this smoke test is for 'no-psycopg' fallback path")

    proj = rds_project["project"]
    verbs_dir = rds_project["project_path"] / "verbs" / "Chemistry"
    hot = verbs_dir / "data_dumps" / "R1"
    hot.mkdir(parents=True, exist_ok=True)
    (hot / "x.txt").write_text("x", encoding="utf-8")

    # Projects and verb groups should still enumerate
    r = rds_client.get("/api/archive_workbench/projects")
    assert r.status_code == 200
    assert proj in r.json()

    r = rds_client.get(f"/api/archive_workbench/{proj}/verb_groups")
    assert r.status_code == 200
    assert "Chemistry" in r.json()

    # Archive apply should succeed without a real Postgres, relying on fallback logic
    r = rds_client.post(
        f"/api/archive_workbench/{proj}/runs/archive/apply?strategy=hard",
        json=[{"run_id": "R1", "verb_group": "Chemistry"}],
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True

    # File moved to archive as usual
    arc_dir = rds_project["project_path"] / "backups" / "data_dump_archive" / "Chemistry" / "R1"
    assert arc_dir.exists() and (arc_dir / "x.txt").exists()
    assert not hot.exists()

    # Restore apply should work too
    r = rds_client.post(
        f"/api/archive_workbench/{proj}/runs/restore/apply",
        json={"verb_group": "Chemistry", "run_ids": ["R1"]},
    )
    assert r.status_code == 200
    assert r.json().get("ok") is True

    assert (verbs_dir / "data_dumps" / "R1" / "x.txt").exists()
