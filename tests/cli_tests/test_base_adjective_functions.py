import pytest
import json
from pathlib import Path
from utils.handlers.adjective import BaseAdjective
from tools.register import register_adjective_interactive
from tools.edit import edit_adjective_interactive
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import utils.semantics as sem

# ─────────────────────────────────────────────────────────────
# 🧪 Pure unit logic test
# ─────────────────────────────────────────────────────────────
def test_apply_filters_to_items():
    adj = BaseAdjective(data={})
    items = [{"type": "flower"}, {"type": "edible"}, {"type": "flower"}]
    filters = {"type": "flower"}
    result = adj.apply_filters_to_items(items, filters)
    assert len(result) == 2

# ─────────────────────────────────────────────────────────────
# 🧪 Integration test: demote_attribute
# ─────────────────────────────────────────────────────────────
def test_demote_attribute(tmp_path, monkeypatch):
    noun_schema = {
        "Submission": {
            "fields": {
                "request": {
                    "type": "adjective",
                    "adjective_class": "ActionRequirement",
                    "required": True,
                    "custom_field": "keep_this"
                }
            }
        }
    }

    project = "MyTestProject"
    proj_path = tmp_path / "projects" / project
    proj_path.mkdir(parents=True)
    noun_file = proj_path / "noun_types.json"
    noun_file.write_text(json.dumps(noun_schema, indent=2))

    adj = BaseAdjective(
        data={"adjective": "request"},
        noun_type="Submission",
        project_name=project
    )

    monkeypatch.setattr("utils.handlers.adjective.Path", lambda *a: tmp_path.joinpath(*a))

    # Bypass registry disentanglement
    monkeypatch.setattr("utils.handlers.adjective.WordRegistry", lambda _: type("MockRegistry", (), {"enforce_disentanglement": lambda self, *args: None})())

    adj.demote_attribute()

    updated = json.loads(noun_file.read_text())
    field = updated["Submission"]["fields"]["request"]
    assert field["type"] == "string"
    assert field["required"] is True
    assert "adjective_class" not in field
    assert field["custom_field"] == "keep_this"

# ─────────────────────────────────────────────────────────────
# 🧪 Integration test: register_adjective_interactive
# ─────────────────────────────────────────────────────────────
def test_register_adjective_promotes_field(tmp_path, monkeypatch):
    project = "MyProj"
    proj_path = tmp_path / "projects" / project
    proj_path.mkdir(parents=True)

    # Write dummy noun and verb types
    noun_types = {
        "Submission": {
            "fields": {
                "request": {"type": "string"}
            }
        }
    }
    verb_types = {
        "Potency_Test": {"verb_group": "TestLog"}
    }

    (proj_path / "noun_types.json").write_text(json.dumps(noun_types))
    (proj_path / "verb_types.json").write_text(json.dumps(verb_types))
    (proj_path / "adjective_types.json").write_text("[]")

    # Simulate user input: noun index 0, field index 0, class index 0 (ActionRequirement), then quit out of config
    inputs = iter(["0", "0", "0", "q"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("utils.handlers.adjective.Path", lambda *a: tmp_path.joinpath(*a))
    monkeypatch.setattr("tools.register.Path", lambda *a: tmp_path.joinpath(*a))
    monkeypatch.setattr("tools.register.sem.generate_project_selector", lambda _: project)

    register_adjective_interactive()

    adjectives = json.loads((proj_path / "adjective_types.json").read_text())
    assert adjectives[0]["adjective"] == "request"
    assert adjectives[0]["adjective_class"] == "ActionRequirement"
    assert adjectives[0]["applies_to"] == ["Submission"]

# ─────────────────────────────────────────────────────────────
# 🧪 Integration test: edit_adjective_interactive → demotion
# ─────────────────────────────────────────────────────────────
def test_edit_adjective_demotes_and_deletes(tmp_path, monkeypatch):
    project = "MyProj"
    proj_path = tmp_path / "projects" / project
    (proj_path / "aliases").mkdir(parents=True)

    # Add dummy noun_types.json so demotion doesn't crash
    noun_types = {
        "Submission": {
            "fields": {
                "request": {
                    "type": "adjective",
                    "adjective_class": "ActionRequirement",
                    "required": True
                }
            }
        }
    }
    (proj_path / "noun_types.json").write_text(json.dumps(noun_types))

    # Define one ActionRequirement adjective on "Submission"
    adjectives = [{
        "adjective": "request",
        "adjective_class": "ActionRequirement",
        "applies_to": ["Submission"]
    }]
    aliases = {"request": "Request (nice label)"}
    verb_types = {}

    (proj_path / "adjective_types.json").write_text(json.dumps(adjectives))
    (proj_path / "aliases" / "adjectives.json").write_text(json.dumps(aliases))
    (proj_path / "verb_types.json").write_text(json.dumps(verb_types))

    # Simulate: select index 0, then action "2" (demote)
    inputs = iter(["0", "2"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("utils.handlers.adjective.Path", lambda *a: tmp_path.joinpath(*a))

    # Patch registry
    monkeypatch.setattr("utils.handlers.adjective.WordRegistry", lambda _: type("MockRegistry", (), {"enforce_disentanglement": lambda self, *args: None})())

    monkeypatch.setattr("tools.edit.Path", lambda *a: tmp_path.joinpath(*a))
    edit_adjective_interactive(project)

    # Confirm adjective and alias were deleted
    updated_adjs = json.loads((proj_path / "adjective_types.json").read_text())
    updated_alias = json.loads((proj_path / "aliases" / "adjectives.json").read_text())
    assert updated_adjs == []
    assert "request" not in updated_alias
