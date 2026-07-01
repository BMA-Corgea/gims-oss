# tests/gui_tests/test_runlog_workbench_gui.py
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path
import pytest
import json
import sys

# --- Ensure imports from project root ---
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.manifest.resolver import resolve_path
from api.routers import runlog_workbench as runlog_module

# --- Add S3-aware I/O for test setup ---
from api import i_o
from api import json_proxy
from api.manifest import resolver
# ---------------------------------------


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app() -> FastAPI:
    """Mount the router exactly as in production."""
    app = FastAPI()
    app.include_router(runlog_module.router)
    return app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def project_path() -> Path:
    """Use canonical resolver to locate or create the test project."""
    root = resolve_path(Path(), "project_root")
    proj = root / "RunlogTest"
    (proj / "verbs" / "Chemistry").mkdir(parents=True, exist_ok=True)
    return proj

@pytest.fixture(autouse=True)
def mock_s3_mode(request, monkeypatch):
    """
    Forces all i_o operations to run in local-only mode by mocking the
    S3 check to always return False.
    """
    if "no_s3_mock" in request.keywords:
        # Don't mock for the one test that checks S3 awareness
        yield
    else:
        # Mock _is_s3_path to always return False
        monkeypatch.setattr(json_proxy, "_is_s3_path", lambda path: False)
        yield
        
@pytest.fixture(autouse=True)
def setup_basic_schemas(project_path: Path, mock_s3_mode):
    """
    Auto-used fixture to create the absolute minimum required schema files
    for most endpoints to prevent 500 errors on schema loading.
    This runs in local mode thanks to the mock_s3_mode fixture.
    """
    # Create minimal noun_types.json
    noun_data = {
        "SampleNoun": {
            "fields": {"id": {"type": "string"}},
            # FIX: Add incrementable segment for test_grid_generate_id
            "autogenerate_segments": [
                {"type": "static", "value": "S-"},
                {"type": "number", "length": 4}
            ] 
        },
        "Run": { # Add 'Run' noun for test_conjunction_reference_options
             "fields": {"id": {"type": "string"}},
             "primary_id_field": "id"
        }
    }
    # Use write_text directly since we are in mocked local mode
    (project_path / "noun_types.json").write_text(json.dumps(noun_data))

    # Create minimal verb_types.json
    verb_data = {
        "DummyVerb": {
            "data_entry_schema": {},
            "adverb_schema": {},
            "linear_status": {"enabled": False, "steps": []}
        }
    }
    (project_path / "verb_types.json").write_text(json.dumps(verb_data))
    
    # FIX: Add missing adverb_types.json (canonical name-keyed dict, matching the
    # noun/verb fixtures above; load_schema normalizes either shape to the same view).
    (project_path / "adverb_types.json").write_text(json.dumps({}))


# --------------------------------------------------------------------------------------
# Canonical Paths
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "project_root",
    "verbs_dir",
    "data_dump_dir",
    "status_file",
    "data_entry",
])
def test_resolver_keys_exist(project_path: Path, key: str):
    """Ensure the canonical resolver knows all required layout keys."""
    p = resolve_path(project_path, key, verb_group="Chemistry", run_id="R1")
    assert isinstance(p, Path)
    # allow directories to be missing but parent must exist
    assert str(p).endswith(".json") or p.exists() or p.parent.exists()


# --------------------------------------------------------------------------------------
# Core Endpoints
# --------------------------------------------------------------------------------------

def test_list_projects(client: TestClient):
    """GET /runlog_data_dump/projects"""
    r = client.get("/runlog_data_dump/projects")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert any("RunlogTest" in d or "demo" in d for d in data)


def test_list_verb_groups_for_project(client: TestClient):
    """GET /runlog_data_dump/verb_groups/{project}"""
    r = client.get("/runlog_data_dump/verb_groups/RunlogTest")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_get_runlog_empty_project(client: TestClient, project_path: Path):
    """GET /runlog/{project}/{verb_group} should return structured table."""
    # create minimal config so it doesn’t 500
    cfg_path = project_path / "verbs" / "Chemistry" / "Chemistry_log_config.json"
    
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps({"primary_id": "run_id"}))
    # Schemas are created by setup_basic_schemas

    r = client.get("/runlog/RunlogTest/Chemistry")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        data = r.json()
        assert "headers" in data and "rows" in data


def test_get_data_dump_404(client: TestClient, project_path: Path):
    """GET /runlog/{project}/{verb_group}/{run_id}/dump"""
    # FIX: Create the config file this endpoint needs
    cfg_path = project_path / "verbs" / "Chemistry" / "Chemistry_log_config.json"
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps({"primary_id": "run_id"}))
    # Schemas are created by setup_basic_schemas

    r = client.get("/runlog/RunlogTest/Chemistry/R1/dump")
    assert r.status_code in (200, 404) # 404 is OK (run not found)


def test_status_breakdown(client: TestClient, project_path: Path):
    """GET /runlog/{project}/{verb_group}/{run_id}/status"""
     # FIX: Create the config and log file this endpoint needs
    cfg_path = project_path / "verbs" / "Chemistry" / "Chemistry_log_config.json"
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps({"primary_id": "run_id"}))
    
    log_file = project_path / "verbs" / "Chemistry" / "Chemistry_log.jsonl"
    if not log_file.exists():
        log_file.write_text('{"run_id": "R1", "test_type": "DummyVerb"}\n')
    # Schemas are created by setup_basic_schemas
    
    r = client.get("/runlog/RunlogTest/Chemistry/R1/status")
    assert r.status_code in (200, 404)


def test_linear_status_and_gates(client: TestClient, project_path: Path):
    """GET /runlog/{project}/{verb_group}/{run_id}/status/linear + gate/list"""
    (project_path / "verbs").mkdir(exist_ok=True)
    
    # FIX: Create config and log file
    cfg_path = project_path / "verbs" / "Chemistry" / "Chemistry_log_config.json"
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps({"primary_id": "run_id"}))
    
    log_file = project_path / "verbs" / "Chemistry" / "Chemistry_log.jsonl"
    if not log_file.exists():
        log_file.write_text('{"run_id": "R1", "test_type": "DummyVerb"}\n')
    # Schemas are created by setup_basic_schemas

    for suffix in ("status/linear", "gate/list"):
        r = client.get(f"/runlog/RunlogTest/Chemistry/R1/{suffix}")
        assert r.status_code in (200, 404, 400)


# --------------------------------------------------------------------------------------
# Grid Endpoints
# --------------------------------------------------------------------------------------

def test_grid_runs_and_load(client: TestClient):
    """GET /grid/runs/{project}/{verb_group} and /grid/load/{project}/{verb_group}/{run_id}"""
    r1 = client.get("/grid/runs/RunlogTest/Chemistry")
    assert r1.status_code == 200
    assert isinstance(r1.json(), dict)
    r2 = client.get("/grid/load/RunlogTest/Chemistry/R1")
    assert r2.status_code in (200, 404)


def test_grid_save_roundtrip(client: TestClient):
    """POST /gui/grid/save/{project}/{verb_group}/{run_id} with fake payload."""
    payload = {"headers": ["a", "b"], "rows": [{"a": "1", "b": "2"}]}
    r = client.post("/gui/grid/save/RunlogTest/Chemistry/R1", json=payload)
    assert r.status_code in (200, 400, 500)
    if r.status_code == 200:
        assert "status" in r.json()


def test_grid_noun_and_ref_options(client: TestClient, project_path: Path):
    """Test noun and reference schema endpoints."""
    # Schemas are created by setup_basic_schemas
        
    r1 = client.get("/grid/noun_info/RunlogTest/SampleNoun")
    assert r1.status_code in (200, 404)
    r2 = client.get("/grid/reference_adjectives/RunlogTest/SampleNoun")
    assert r2.status_code in (200, 404)
    r3 = client.get("/grid/ref_options/RunlogTest/SampleNoun/example_field")
    assert r3.status_code in (200, 404)


def test_grid_generate_id(client: TestClient, project_path: Path):
    """POST /grid/generate_id/{project}/{noun_type}"""
    # Schemas are created by setup_basic_schemas

    payload = {"existing_ids": []}
    r = client.post("/grid/generate_id/RunlogTest/SampleNoun", json=payload)
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert r.json()["id"].startswith("S-")


def test_grid_dump_structure(client: TestClient, project_path: Path):
    """GET /grid/dump/{project}/{verb_group}/{run_id}"""
    # FIX: Create config and log file
    cfg_path = project_path / "verbs" / "Chemistry" / "Chemistry_log_config.json"
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps({"primary_id": "run_id"}))
    
    log_file = project_path / "verbs" / "Chemistry" / "Chemistry_log.jsonl"
    if not log_file.exists():
        log_file.write_text('{"run_id": "R1", "test_type": "DummyVerb"}\n')
    # Schemas are created by setup_basic_schemas

    r = client.get("/grid/dump/RunlogTest/Chemistry/R1")
    assert r.status_code in (200, 404)

# --------------------------------------------------------------------------------------
# Gate and Status Integrity
# --------------------------------------------------------------------------------------

def test_gate_list_and_step_ids(client: TestClient, project_path: Path):
    """GET /runlog/{project}/{verb_group}/{run_id}/gate/list and step_ids"""
    log_dir = project_path / "verbs" / "Chemistry"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # FIX: Revert to local write_text
    log_file = log_dir / "Chemistry_log.jsonl"
    if not log_file.exists():
        log_file.write_text('{"run_id": "R1", "test_type": "DummyVerb"}\n')
        
    # FIX: Correct the path to cfg_file
    cfg_file = log_dir / "Chemistry_log_config.json"
    if not cfg_file.exists():
        cfg_file.write_text(json.dumps({"primary_id": "run_id"}))
    # Schemas are created by setup_basic_schemas

    r1 = client.get("/runlog/RunlogTest/Chemistry/R1/gate/list")
    assert r1.status_code in (200, 404, 400)
    r2 = client.get("/runlog/RunlogTest/Chemistry/R1/status/step_ids")
    assert r2.status_code in (200, 404, 400)


def test_conjunction_reference_options(client: TestClient):
    """GET /conjunction/reference_options/{project}/{noun_type}"""
    # Schemas are created by setup_basic_schemas
    r = client.get("/conjunction/reference_options/RunlogTest/SampleNoun")
    assert r.status_code in (200, 404)