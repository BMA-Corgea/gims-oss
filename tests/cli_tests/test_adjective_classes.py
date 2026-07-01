import pytest
from utils.handlers.adjective import (
    ActionRequirementAdjective,
    ReferenceAdjective,
    TagAdjective,
)
from utils.display import format_display_id
from pathlib import Path
import json

def test_prompt_instance_edit_returns_selected(monkeypatch):
    adj = ActionRequirementAdjective(
        data={
            "class": "ActionRequirement",
            "adjective": "request",
            "request_options": {
                "potency": ["Potency_Test"],
                "micro": ["Micro_Test"]
            }
        }
    )

    monkeypatch.setattr("utils.handlers.adjective.indexed_choice", lambda opts, msg: 1)

    result = adj.prompt_instance_edit("request", "default_value")
    assert result == "micro"


def test_prompt_instance_edit_returns_current_when_none(monkeypatch):
    adj = ActionRequirementAdjective(
        data={
            "class": "ActionRequirement",
            "adjective": "request",
            "request_options": {
                "potency": ["Potency_Test"]
            }
        }
    )

    monkeypatch.setattr("utils.handlers.adjective.indexed_choice", lambda opts, msg: None)

    result = adj.prompt_instance_edit("request", "default_value")
    assert result == "default_value"


def test_prompt_instance_edit_warns_if_empty(capfd):
    adj = ActionRequirementAdjective(
        data={
            "class": "ActionRequirement",
            "adjective": "request",
            "request_options": {}
        }
    )

    result = adj.prompt_instance_edit("request", "fallback")
    out, _ = capfd.readouterr()
    assert "⚠️ No request_options defined." in out
    assert result == "fallback"


def test_ask_verbs_valid(monkeypatch):
    adj = ActionRequirementAdjective(data={"class": "ActionRequirement"})
    adj.verb_names = ["Micro_Test", "Potency_Test"]

    monkeypatch.setattr("builtins.input", lambda _: "Micro_Test, Potency_Test")
    result = adj._ask_verbs()

    assert result == ["Micro_Test", "Potency_Test"]


def test_ask_verbs_invalid(monkeypatch, capfd):
    adj = ActionRequirementAdjective(data={"class": "ActionRequirement"})
    adj.verb_names = ["Micro_Test", "Potency_Test"]

    monkeypatch.setattr("builtins.input", lambda _: "Micro_Test, Garbage_Test")
    result = adj._ask_verbs()

    out, _ = capfd.readouterr()
    assert result is None
    assert "❌ Invalid verb: 'Garbage_Test'" in out

def test_show_request_status_invokes_check_and_evaluate(monkeypatch, tmp_path, capsys):
    from utils.monitoring import check_next_step, evaluate_condition
    project = "MyProj"
    noun_type = "Submission"
    item_id = "Sub0001"
    
    # Setup test paths
    proj_path = tmp_path / "projects" / project
    (proj_path / "nouns" / noun_type).mkdir(parents=True, exist_ok=True)
    (proj_path / "verbs").mkdir(parents=True)
    
    # Add noun_types.json
    noun_schema = {
        noun_type: {
            "primary_id_field": "submission_id",
            "fields": {
                "request": {"type": "adjective", "adjective_class": "ActionRequirement"}
            }
        }
    }
    (proj_path / "noun_types.json").write_text(json.dumps(noun_schema))

    # Add one instance to items.jsonl
    item = {"submission_id": item_id, "request": "potency"}
    (proj_path / "nouns" / noun_type / "items.jsonl").write_text(json.dumps(item) + "\n")

    # Add dummy verb config
    verb_defs = {
        "Potency_Test": {
            "verb_group": "TestLog",
            "data_entry_schema": {
                "raw_data_inputs": ["raw_hplc"]
            }
        }
    }
    (proj_path / "verb_types.json").write_text(json.dumps(verb_defs))

    # Patch Path resolution
    monkeypatch.setattr("utils.handlers.adjective.Path", lambda *a: proj_path.joinpath(*a))

    # Patch check_next_step to return a dummy run
    dummy_steps = [{"linked_id": item_id, "run_id": "Run0001"}]
    monkeypatch.setattr("utils.monitoring.check_next_step", lambda *_: dummy_steps)

    # Patch evaluate_condition to just print something we can verify
    monkeypatch.setattr("utils.monitoring.evaluate_condition", lambda **kwargs: print(" evaluate_condition called"))

    # Create the ActionRequirementAdjective
    adj = ActionRequirementAdjective(data={
        "adjective": "request",
        "adjective_class": "ActionRequirement",
        "request_options": {"potency": ["Potency_Test"]}
    }, noun_type=noun_type, project_name=project)

    adj.show_request_status(project_path=proj_path, instance_id=item_id)

    out, _ = capsys.readouterr()
    assert "✔️ evaluate_condition called" in out
    assert "requests: potency" in out

def test_set_field_stores_inferred_value():
    ra = ReferenceAdjective({}, noun_type="submission_id", project_name="test_proj")
    ra.set_field("submission_id", "12345")
    assert ra.data["submission_id"] == 12345

def test_interactive_configure_sets_reference_and_filters(monkeypatch, tmp_path):
    proj_name = "TestProj"
    proj_path = tmp_path / "projects" / proj_name
    noun_file = proj_path / "noun_types.json"
    noun_file.parent.mkdir(parents=True)
    noun_file.write_text(json.dumps({"Sample": {"fields": {}}}))

    # Patch selection and filters
    monkeypatch.setattr("utils.handlers.adjective.indexed_choice", lambda opts, msg: 0)
    monkeypatch.setattr("utils.handlers.adjective.ReferenceAdjective.prompt_filters", lambda self, noun: {"type": "Cooliolio"})

    ra = ReferenceAdjective({}, noun_type="sample_id", project_name=proj_name)
    monkeypatch.setattr(
        "utils.handlers.adjective.Path",
        lambda *args: tmp_path.joinpath(*args)
    )
    ra.interactive_configure()

    assert ra.data["reference_noun"] == "Sample"
    assert ra.data["filters"] == {"type": "Cooliolio"}

def test_prompt_instance_edit_applies_filters(monkeypatch, tmp_path):
    proj = "MyProj"
    ref_noun = "Sample"
    field = "linked_sample"

    # Create fake noun items and noun schema
    items_path = tmp_path / "projects" / proj / "nouns" / ref_noun / "items.jsonl"
    items_path.parent.mkdir(parents=True, exist_ok=True)
    items = [
        {"sample_id": "S123", "type": "Cooliolio"},
        {"sample_id": "S456", "type": "Uncooliolio"}
    ]
    with open(items_path, "w") as f:
        for i in items:
            f.write(json.dumps(i) + "\n")

    noun_schema = {"Sample": {"primary_id_field": "sample_id", "fields": {"sample_id": {"type": "string"}}}}
    noun_file = tmp_path / "projects" / proj / "noun_types.json"
    noun_file.write_text(json.dumps(noun_schema))

    ra = ReferenceAdjective({}, noun_type=field, project_name=proj)
    ra.data["reference_noun"] = ref_noun
    ra.data["filters"] = {"type": "Cooliolio"}

    # Patch Path to resolve to tmp_path
    monkeypatch.setattr("utils.handlers.adjective.Path", lambda *args: tmp_path.joinpath(*args))

    # Patch selection to choose index 0
    monkeypatch.setattr("utils.handlers.adjective.indexed_choice", lambda opts, msg: 0)

    result = ra.prompt_instance_edit(field, "")
    assert result == "S123"

def test_tag_prompt_instance_edit(monkeypatch):
    adj = TagAdjective(
        data={
            "adjective": "flag",
            "adjective_class": "Tag",
            "valid_options": [
                {"value": "A", "explanation": "", "display_in_id": True},
                {"value": "B", "explanation": "", "display_in_id": False},
            ],
        }
    )
    monkeypatch.setattr("utils.handlers.adjective.indexed_choice", lambda opts, msg: 1)
    result = adj.prompt_instance_edit("flag", "")
    assert result == "B"


def test_tag_prompt_instance_edit_returns_current(monkeypatch):
    adj = TagAdjective(
        data={
            "adjective": "flag",
            "adjective_class": "Tag",
            "valid_options": [
                {"value": "A", "explanation": "", "display_in_id": True}
            ],
        }
    )
    monkeypatch.setattr("utils.handlers.adjective.indexed_choice", lambda opts, msg: None)
    result = adj.prompt_instance_edit("flag", "default")
    assert result == "default"