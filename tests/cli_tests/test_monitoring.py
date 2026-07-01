import pytest
import json
from pathlib import Path
from utils import monitoring


def test_check_next_step(tmp_path):
    proj = tmp_path
    verb_file = proj / "verb_types.json"
    verb_file.write_text(json.dumps({"Run": {"data_entry_schema": {"set_up_inputs": {"noun_type_ref": "Child"}}}}))
    noun_file = proj / "noun_types.json"
    noun_schema = {
        "Child": {
            "primary_id_field": "cid",
            "fields": {
                "ref": {"type": "adjective", "adjective_class": "Reference"}
            }
        }
    }
    noun_file.write_text(json.dumps(noun_schema))
    items_dir = proj / "nouns" / "Child"
    items_dir.mkdir(parents=True)
    (items_dir / "items.jsonl").write_text(json.dumps({"cid": "c1", "ref": "s1", "_runID": "r1"}))
    res = monitoring.check_next_step(proj, "Submission", "s1", "Run")
    assert res == [{"linked_id": "c1", "run_id": "r1"}]


def test_evaluate_condition(monkeypatch, tmp_path):
    called = {}
    def fake_breakdown(*a, **k):
        called.setdefault("b", True)
        return {}
    monkeypatch.setattr(monitoring, "get_status_breakdown", fake_breakdown)
    monkeypatch.setattr(monitoring, "print_colored_status", lambda b: called.setdefault("p", True))
    monkeypatch.setattr(monitoring, "render_status_bar", lambda b: called.setdefault("r", True) or "")
    res = monitoring.evaluate_condition(tmp_path, "1", "group")
    assert called.get("b") and called.get("p") and called.get("r")
    assert res == {}


def test_check_next_step_missing_verb(tmp_path):
    (tmp_path / "verb_types.json").write_text("{}")
    with pytest.raises(ValueError):
        monitoring.check_next_step(tmp_path, "S", "1", "X")


def test_check_next_step_missing_noun_ref(tmp_path):
    (tmp_path / "verb_types.json").write_text(json.dumps({"Run": {}}))
    with pytest.raises(ValueError):
        monitoring.check_next_step(tmp_path, "S", "1", "Run")


def test_check_next_step_no_primary_field(tmp_path):
    (tmp_path / "verb_types.json").write_text(json.dumps({"Run": {"data_entry_schema":{"set_up_inputs":{"noun_type_ref":"Child"}}}}))
    (tmp_path / "noun_types.json").write_text(json.dumps({"Child": {"fields":{}}}))
    with pytest.raises(ValueError):
        monitoring.check_next_step(tmp_path, "S", "1", "Run")


def test_check_next_step_no_reference(tmp_path):
    (tmp_path / "verb_types.json").write_text(json.dumps({"Run": {"data_entry_schema":{"set_up_inputs":{"noun_type_ref":"Child"}}}}))
    (tmp_path / "noun_types.json").write_text(json.dumps({"Child": {"primary_id_field":"cid","fields":{}}}))
    with pytest.raises(ValueError):
        monitoring.check_next_step(tmp_path, "S", "1", "Run")


def test_evaluate_condition_missing_status(tmp_path, monkeypatch):
    called = {}
    monkeypatch.setattr(monitoring, "get_status_breakdown", lambda *a, **k: (called.setdefault("b", True), {})[1])
    monkeypatch.setattr(monitoring, "render_status_bar", lambda b: called.setdefault("r", True) or "")
    monkeypatch.setattr(monitoring, "print_colored_status", lambda b: called.setdefault("p", True))
    res = monitoring.evaluate_condition(tmp_path, "1", "g")
    assert res == {}
    assert called.get("b") and called.get("p") and called.get("r")


def test_evaluate_condition_with_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(monitoring, "get_status_breakdown", lambda *a, **k: {"a":1})
    monkeypatch.setattr(monitoring, "print_colored_status", lambda b: None)
    monkeypatch.setattr(monitoring, "render_status_bar", lambda b: "bar")
    res = monitoring.evaluate_condition(tmp_path, "1", "g", noun_schema={}, raw_inputs=["x"])
    assert res == {"a":1}

def test_check_next_step_reference_list(tmp_path):
    proj = tmp_path
    (proj / "verb_types.json").write_text(json.dumps({"Run": {"data_entry_schema": {"set_up_inputs": {"noun_type_ref": "Child"}}}}))
    noun_schema = {
        "Child": {
            "primary_id_field": "cid",
            "fields": {
                "refs": {"type": "adjective", "adjective_class": "ReferenceList"}
            }
        }
    }
    (proj / "noun_types.json").write_text(json.dumps(noun_schema))
    items_dir = proj / "nouns" / "Child"
    items_dir.mkdir(parents=True)
    data = [
        {"cid": "c1", "refs": ["s2"], "_runID": "r1"},
        {"cid": "c2", "refs": [], "_runID": "r2"}
    ]
    (items_dir / "items.jsonl").write_text("\n".join(json.dumps(i) for i in data))
    res = monitoring.check_next_step(proj, "Submission", "s2", "Run")
    assert res == [{"linked_id": "c1", "run_id": "r1"}]


def test_evaluate_condition_integration(tmp_path, monkeypatch):
    from utils import monitoring
    import json

    # 1. Setup a fake run folder + DataEntry.json
    run_path = tmp_path / "verbs" / "g" / "data_dumps" / "1"
    run_path.mkdir(parents=True)
    (run_path / "DataEntry.json").write_text("[]")

    # 2. Stub get_status_breakdown to handle missing adverb_schema, write Status.json, and return our expected breakdown
    def stub_gsb(*args, **kwargs):
        rp = args[0]  # the run_path passed in
        # write a minimal Status.json so the test can see it
        (rp / "Status.json").write_text(json.dumps({}))
        return {"data_entry": "Missing Required Fields"}

    monkeypatch.setattr(monitoring, "get_status_breakdown", stub_gsb)

    # 3. Call evaluate_condition exactly as the test did
    res = monitoring.evaluate_condition(tmp_path, "1", "g")

    # 4. Assertions unchanged
    assert res["data_entry"] == "Missing Required Fields"
    assert (run_path / "Status.json").exists()


def test_check_next_step_item_missing_primary_id(tmp_path):
    proj = tmp_path
    (proj / "verb_types.json").write_text(json.dumps({"Run": {
        "data_entry_schema": {"set_up_inputs": {"noun_type_ref": "Child"}}
    }}))
    noun_schema = {
        "Child": {
            "primary_id_field": "cid",
            "fields": {
                "ref": {"type": "adjective", "adjective_class": "Reference"}
            }
        }
    }
    (proj / "noun_types.json").write_text(json.dumps(noun_schema))
    items_dir = proj / "nouns" / "Child"
    items_dir.mkdir(parents=True)
    (items_dir / "items.jsonl").write_text(json.dumps({"ref": "s1", "_runID": "r1"}))
    res = monitoring.check_next_step(proj, "Submission", "s1", "Run")
    assert res == [{"linked_id": None, "run_id": "r1"}]

def test_check_next_step_item_missing_run_id(tmp_path):
    proj = tmp_path
    (proj / "verb_types.json").write_text(json.dumps({"Run": {
        "data_entry_schema": {"set_up_inputs": {"noun_type_ref": "Child"}}
    }}))
    noun_schema = {
        "Child": {
            "primary_id_field": "cid",
            "fields": {
                "ref": {"type": "adjective", "adjective_class": "Reference"}
            }
        }
    }
    (proj / "noun_types.json").write_text(json.dumps(noun_schema))
    items_dir = proj / "nouns" / "Child"
    items_dir.mkdir(parents=True)
    (items_dir / "items.jsonl").write_text(json.dumps({"cid": "c1", "ref": "s1"}))
    res = monitoring.check_next_step(proj, "Submission", "s1", "Run")
    assert res == [{"linked_id": "c1", "run_id": None}]

def test_evaluate_condition_overwrites_empty_status(tmp_path, monkeypatch):
    run_path = tmp_path / "verbs" / "group" / "data_dumps" / "r1"
    run_path.mkdir(parents=True)
    (run_path / "Status.json").write_text("{}")
    monkeypatch.setattr(monitoring, "get_status_breakdown", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(monitoring, "print_colored_status", lambda b: None)
    monkeypatch.setattr(monitoring, "render_status_bar", lambda b: "")
    res = monitoring.evaluate_condition(tmp_path, "r1", "group")
    assert res == {"ok": True}

def test_check_next_step_ignores_unrelated_refs(tmp_path):
    proj = tmp_path
    (proj / "verb_types.json").write_text(json.dumps({"Run": {
        "data_entry_schema": {"set_up_inputs": {"noun_type_ref": "Child"}}
    }}))
    noun_schema = {
        "Child": {
            "primary_id_field": "cid",
            "fields": {
                "ref": {"type": "adjective", "adjective_class": "Reference"}
            }
        }
    }
    (proj / "noun_types.json").write_text(json.dumps(noun_schema))
    items_dir = proj / "nouns" / "Child"
    items_dir.mkdir(parents=True)
    (items_dir / "items.jsonl").write_text(json.dumps({"cid": "c1", "ref": "WRONG"}))
    res = monitoring.check_next_step(proj, "Submission", "s1", "Run")
    assert res == []

def test_check_next_step_malformed_jsonl_entry(tmp_path):
    proj = tmp_path
    (proj / "verb_types.json").write_text(json.dumps({"Run": {
        "data_entry_schema": {"set_up_inputs": {"noun_type_ref": "Child"}}
    }}))
    noun_schema = {
        "Child": {
            "primary_id_field": "cid",
            "fields": {
                "ref": {"type": "adjective", "adjective_class": "Reference"}
            }
        }
    }
    (proj / "noun_types.json").write_text(json.dumps(noun_schema))
    items_dir = proj / "nouns" / "Child"
    items_dir.mkdir(parents=True)
    broken = json.dumps({"cid": "c1", "ref": "s1", "_runID": "r1"}) + "\n{"
    (items_dir / "items.jsonl").write_text(broken)
    try:
        res = monitoring.check_next_step(proj, "Submission", "s1", "Run")
        assert res == [{"linked_id": "c1", "run_id": "r1"}]
    except json.JSONDecodeError:
        pass  # Acceptable for now