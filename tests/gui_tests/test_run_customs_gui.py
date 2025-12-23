# tests/gui_tests/test_run_customs_gui.py
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ──────────────────────────────────────────────────────────────────────────────
# Helper: dynamically import the target module (repo-root run_customs_gui.py)
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def gb_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    mod_path = repo_root / "gui" / "run_customs_gui.py"
    spec = importlib.util.spec_from_file_location("run_customs_gui", mod_path)
    assert spec and spec.loader, f"Could not load module at {mod_path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_customs_gui"] = mod
    spec.loader.exec_module(mod)
    return mod


# ──────────────────────────────────────────────────────────────────────────────
# Fixture: fabricate a temp project & monkeypatch resolver + i/o + orchestrator
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def temp_project(tmp_path: Path, gb_module, monkeypatch) -> Dict[str, Any]:
    """
    Creates a minimal FS layout under:
      <tmp>/projects/DemoProject/
        - verbs/Tests/data_dumps/<run_id>/{DataEntry.json, adverbs.json, Status.json}
        - custom/custom_parser/<parser>/<parser>.py
        - custom/prepositional phrases/<pphrase>/<pphrase>.py
        - pphrase_outputs/ (canonical)
    And patches resolve_path + i_o helpers to point here.
    """
    # ---- FS scaffold
    root = tmp_path / "projects"
    root.mkdir(parents=True, exist_ok=True)

    project_name = "DemoProject"
    project_path = root / project_name
    (project_path / "verbs" / "Tests" / "data_dumps" / "R1").mkdir(parents=True, exist_ok=True)
    (project_path / "custom" / "custom_parser").mkdir(parents=True, exist_ok=True)
    (project_path / "custom" / "prepositional phrases").mkdir(parents=True, exist_ok=True)
    (project_path / "pphrase_outputs").mkdir(parents=True, exist_ok=True)

    # hot/arc DBs for parity with other tests (not used heavily here)
    hot_db = project_path / "objects.db"
    arc_db = project_path / "archive.db"
    sqlite3.connect(hot_db.as_posix()).close()
    sqlite3.connect(arc_db.as_posix()).close()

    # Seed run data files
    run_dir = project_path / "verbs" / "Tests" / "data_dumps" / "R1"
    (run_dir / "DataEntry.json").write_text('{"alpha": 1, "_runID": "R1"}', encoding="utf-8")
    (run_dir / "adverbs.json").write_text('{"flags": ["x"]}', encoding="utf-8")
    (run_dir / "Status.json").write_text('{"state": "ready"}', encoding="utf-8")

    # ---- Create on-disk dummy modules for both types
    # custom_parser: TOOL as dict + run()
    parser_name = "toy_parser"
    parser_dir = project_path / "custom" / "custom_parser" / parser_name
    parser_dir.mkdir(parents=True, exist_ok=True)
    (parser_dir / f"{parser_name}.py").write_text(
        # NOTE: TOOL is dict so backend will coerce to IoSpec(**TOOL)
        'TOOL = {\n'
        '  "kind": "parser",\n'
        '  "raw_folders": [],\n'
        '  "file_inputs": ["DataEntry.json"],\n'
        '  "outputs": {"files": ["Results.csv"]}\n'
        '}\n'
        'def run(context=None):\n'
        '  # simple stdout action; backend may adapt zero-arg run to env\n'
        '  print("toy_parser run invoked")\n',
        encoding="utf-8",
    )

    # prepositional phrase module with PREPHRASE_SETTINGS literal
    pphrase_name = "toy_pphrase"
    pphrase_dir = project_path / "custom" / "prepositional phrases" / pphrase_name
    pphrase_dir.mkdir(parents=True, exist_ok=True)
    (pphrase_dir / f"{pphrase_name}.py").write_text(
        'PREPHRASE_SETTINGS = [\n'
        '  {"name": "sample", "type": "noun", "noun": "Sample"},\n'
        '  {"name": "when", "type": "text"},\n'
        '  {"name": "range", "type": "between", "field": "date"}\n'
        ']\n',
        encoding="utf-8",
    )

    # Put something in canonical pphrase output dir for tree/download tests
    out_root = project_path / "pphrase_outputs" / pphrase_name
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "report.txt").write_text("hello world", encoding="utf-8")

    # ---- monkeypatch resolve_path mapping used by run_customs_gui
    def fake_resolve_path(base: Path, key: str, **kwargs) -> Path:
        if key == "project_root":
            return root
        if key == "custom_parser_dir":
            return project_path / "custom" / "custom_parser"
        if key == "prepositional_phrases_dir":
            return project_path / "custom" / "prepositional phrases"
        if key == "data_dump_dir":
            vg = kwargs.get("verb_group") or "Tests"
            rid = kwargs.get("run_id") or "R1"
            return project_path / "verbs" / vg / "data_dumps" / rid
        if key == "data_entry":
            vg = kwargs.get("verb_group") or "Tests"
            rid = kwargs.get("run_id") or "R1"
            return project_path / "verbs" / vg / "data_dumps" / rid / "DataEntry.json"
        if key == "adverb_file":
            vg = kwargs.get("verb_group") or "Tests"
            rid = kwargs.get("run_id") or "R1"
            return project_path / "verbs" / vg / "data_dumps" / rid / "adverbs.json"
        if key == "status_file":
            vg = kwargs.get("verb_group") or "Tests"
            rid = kwargs.get("run_id") or "R1"
            return project_path / "verbs" / vg / "data_dumps" / rid / "Status.json"
        if key == "prepositional_phrase_output_dir":
            return project_path / "pphrase_outputs"
        # Fallback for any direct file lookups:
        return project_path / key

    monkeypatch.setattr(gb_module, "resolve_path", fake_resolve_path, raising=True)

    # ---- monkeypatch i_o helpers the backend calls
    NOUNS = {
        "Sample": {
            "primary_id_field": "id",
            "fields": {"id": {"required": True}, "date": {"required": False}},
        }
    }
    VERBS = {
        "ToyVerb": {
            "verb_group": "Tests",
            "data_entry_schema": {
                "raw_data_inputs": [],
                "interpretation": {"tabs": ["Results"], "parsers": [parser_name]},
            },
        }
    }
    RUN_LOG = [{"run_id": "R1", "test_type": "ToyVerb", "date_tested": "062925"}]

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
        return RUN_LOG

    def fake_get_noun_schema(project_path_arg: Path, noun: str):
        return NOUNS.get(noun, {"fields": {}})

    def fake_get_noun_items(project_path_arg: Path, noun: str):
        # minimal items to exercise expansion/use in tests
        return [{"id": "S1", "date": "2025-01-01"}, {"id": "S2", "date": "2025-01-02"}]

    for name, func in {
        "load_schema": fake_load_schema,
        "get_verb_schema": fake_get_verb_schema,
        "get_verb_group_log_config": fake_get_verb_group_log_config,
        "load_verb_group_log": fake_load_verb_group_log,
        "get_noun_schema": fake_get_noun_schema,
        "get_noun_items": fake_get_noun_items,
    }.items():
        monkeypatch.setattr(gb_module.i_o, name, func, raising=True)

    # ---- monkeypatch orchestrator: run_custom_tool
    def fake_run_custom_tool(**kwargs):
        # Pretend layout_resolver + executor worked; return "produced"
        iospec = kwargs.get("iospec") or {}
        kind = getattr(iospec, "kind", None) or (iospec.get("kind") if isinstance(iospec, dict) else None)
        produced = []
        if kind == "parser":
            produced = ["Results.csv"]
        elif kind == "pphrase":
            produced = ["analysis/summary.txt"]
        return {"ok": True, "produced": produced, "post_doc": {"kind": kind}, "logs": ["ok"]}

    monkeypatch.setattr(gb_module, "run_custom_tool", fake_run_custom_tool, raising=True)

    # ---- monkeypatch expand_prephrase_settings_dynamic to verify wiring
    def fake_expand_prephrase(settings, user_values, fetch_noun_schema, fetch_noun_items):
        # Use provided providers to derive a tiny, stable shape
        items = fetch_noun_items("Sample")
        return {
            "fields": settings,
            "user_values": user_values,
            "items_count": len(items),
        }

    monkeypatch.setattr(gb_module, "expand_prephrase_settings_dynamic", fake_expand_prephrase, raising=True)

    # Quiet backend debug
    if hasattr(gb_module, "DEBUG_ENABLED"):
        monkeypatch.setattr(gb_module, "DEBUG_ENABLED", False, raising=True)

    return {
        "root": root,
        "project": project_name,
        "project_path": project_path,
        "hot_db": hot_db,
        "arc_db": arc_db,
        "parser_name": parser_name,
        "pphrase_name": pphrase_name,
        "pphrase_report": out_root / "report.txt",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Fixture: FastAPI app mounting the parser_test router
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def client(gb_module, temp_project) -> TestClient:
    app = FastAPI()
    app.include_router(gb_module.router)  # prefix="/api/parser_test"
    return TestClient(app)


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────
def test_check_parser_projects_listing(client: TestClient, temp_project: Dict[str, Any]):
    r = client.get("/api/parser_test/check_parser/projects")
    assert r.status_code == 200
    projects = r.json()
    assert temp_project["project"] in projects
    # 'custom' must be excluded if present
    assert "custom" not in [p.lower() for p in projects]


def test_check_parser_exists_and_tool_spec(client: TestClient, temp_project: Dict[str, Any]):
    proj = temp_project["project"]
    parser_name = temp_project["parser_name"]

    r = client.get(f"/api/parser_test/check_parser/{proj}/{parser_name}?type=custom_parser")
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is True
    assert body["parser_name"] == parser_name
    # backend should have attempted to load & report run/tool flags
    assert body.get("has_run") is True
    assert body.get("has_tool") is True
    spec = body.get("tool_spec") or {}
    assert spec.get("kind") == "parser"
    assert "DataEntry.json" in (spec.get("file_inputs") or [])


def test_run_custom_parser_native_success(client: TestClient, temp_project: Dict[str, Any]):
    proj = temp_project["project"]
    parser_name = temp_project["parser_name"]

    # Supply run context via query, and some params (including nested _runID extraction path)
    payload = {"params": {"target": {"_runID": "R1"}, "note": "hello"}}
    r = client.post(
        f"/api/parser_test/test_parser/{proj}/{parser_name}"
        "?parser_type=custom_parser&verb_group=Tests&run_id=R1",
        json=payload,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["exec_mode"] == "native"
    assert "Results.csv" in body.get("produced", [])


def test_list_available_pphrases(client: TestClient, temp_project: Dict[str, Any]):
    proj = temp_project["project"]
    r = client.get(f"/api/parser_test/list_custom_parsers?project={proj}")
    assert r.status_code == 200
    out = r.json()
    names = [p["name"] for p in out.get("pphrases", [])]
    assert temp_project["pphrase_name"] in names


def test_get_runs_for_parser(client: TestClient, temp_project: Dict[str, Any]):
    proj = temp_project["project"]
    parser_name = temp_project["parser_name"]
    r = client.get(f"/api/parser_test/check_parser/get_runs/{proj}/{parser_name}")
    assert r.status_code == 200
    runs = r.json()
    assert isinstance(runs, list)
    assert any(rn.get("run_id") == "R1" and rn.get("verb_group") == "Tests" for rn in runs)


def test_prephrase_expand_and_outputs_tree_download(client: TestClient, temp_project: Dict[str, Any]):
    proj = temp_project["project"]
    pphrase = temp_project["pphrase_name"]

    # Expand (uses PREPHRASE_SETTINGS literal + our fake expander)
    r = client.post(
        f"/api/parser_test/prephrase/expand/{proj}",
        json={
            "pphrase_name": pphrase,
            "user_values": {"when": "today", "range": ["2025-01-01", "2025-12-31"]},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    exp = body.get("expanded") or {}
    assert exp.get("items_count") == 2  # from our stubbed get_noun_items

    # Tree
    r2 = client.get(f"/api/parser_test/pphrase_outputs/{proj}/tree?depth=4")
    assert r2.status_code == 200
    t = r2.json()
    assert t["project"] == proj
    # Ensure our seeded file exists in tree
    paths = []

    def _walk(node):
        if node.get("type") == "file":
            paths.append(node.get("path"))
        for c in node.get("children", []):
            _walk(c)

    _walk(t["tree"])
    assert any(p.endswith("report.txt") for p in paths)

    # Download seeded file
    r3 = client.get(f"/api/parser_test/pphrase_outputs/{proj}/download?path={pphrase}/report.txt")
    assert r3.status_code == 200
    assert r3.content == b"hello world"
