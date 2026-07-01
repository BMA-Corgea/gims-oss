import os
import json
import builtins
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import utils.semantics as sem
import utils.interface as ui

import tools.edit     as edit_module
import tools.register as register_module

from tools.edit     import edit_noun_interactive
from tools.register import register_noun_interactive

from utils.handlers.noun import (
    NounType,
    register_noun_type,
    configure_autogenerate_id,
)

@ pytest.fixture(autouse=True)
def change_tmp_dir(tmp_path, monkeypatch):
    # Switch working directory to temp
    monkeypatch.chdir(tmp_path)
    return tmp_path

# Helpers to create a basic noun schema
BASIC_SCHEMA = {
    "fields": {
        "name": {"type": "string"},
        "value": {"type": "float"},
    },
    "primary_id_field": "name"
}

@pytest.fixture
def tmp_project(tmp_path):
    # Create project structure
    project = tmp_path / "proj"
    project.mkdir()
    noun_file = project / "noun_types.json"
    noun_file.write_text("{}")
    return project

### Unit tests for validate_field_structure ###

def test_validate_field_structure_valid(tmp_path):
    schema = {"fields": {"f1": {"type": "string"}}}
    nt = NounType("Test", schema, tmp_path / "noun_types.json")
    # Should not raise
    nt.validate_field_structure()


def test_validate_field_structure_invalid_type(tmp_path):
    schema = {"fields": {"f1": {"type": "badtype"}}}
    nt = NounType("Test", schema, tmp_path / "noun_types.json")
    with pytest.raises(ValueError) as exc:
        nt.validate_field_structure()
    assert "Invalid type 'badtype'" in str(exc.value)


def test_validate_field_structure_missing_pid(tmp_path):
    schema = {"fields": {"f1": {"type": "string"}}, "primary_id_field": "nope"}
    nt = NounType("Test", schema, tmp_path / "noun_types.json")
    with pytest.raises(ValueError) as exc:
        nt.validate_field_structure()
    assert "primary_id_field 'nope' is not defined" in str(exc.value)

### Tests for add/edit/delete field ###

def test_add_field_creates_field_and_saves(tmp_path, monkeypatch):
    # Create a noun file
    noun_file = tmp_path / "noun_types.json"
    noun_file.write_text(json.dumps({}))
    schema = {"fields": {}}
    nt = NounType("Test", schema, noun_file)
    # Patch prompt_date_format for date tests
    with patch("utils.id_generator.prompt_date_format", return_value="%Y-%m-%d"):
        # Add date field
        nt.add_field("d1", "date", required=True)
    assert "d1" in nt.schema["fields"]
    entry = nt.schema["fields"]["d1"]
    assert entry["type"] == "date"
    assert entry.get("required") is True
    assert entry.get("format") == "%Y-%m-%d"


def test_edit_field_type_and_required(tmp_path):
    schema = {"fields": {"f1": {"type": "string"}}}
    noun_file = tmp_path / "noun_types.json"
    noun_file.write_text(json.dumps({}))
    nt = NounType("Test", schema, noun_file)
    # Edit type
    nt.edit_field("f1", new_type="number", required=True)
    updated = nt.schema["fields"]["f1"]
    assert updated["type"] == "float"
    assert updated["required"] is True

    # Remove required
    nt.edit_field("f1", required=False)
    assert "required" not in nt.schema["fields"]["f1"]


def test_edit_field_keyerror(tmp_path):
    schema = {"fields": {}}
    noun_file = tmp_path / "noun_types.json"
    noun_file.write_text(json.dumps({}))
    nt = NounType("Test", schema, noun_file)
    with pytest.raises(KeyError):
        nt.edit_field("nope")


def test_delete_field(tmp_path):
    noun_file = tmp_path / "noun_types.json"
    noun_file.write_text(json.dumps({"Test": {"fields": {"f1": {"type": "string"}}}}))

    schema = {"fields": {"f1": {"type": "string"}}}
    nt = NounType("Test", schema, noun_file)
    nt.delete_field("f1")

    assert "f1" not in nt.schema["fields"]

### Tests for rename_field ###

def test_rename_field_updates_schema_and_pid(tmp_path):
    # Prepare schema and adjective_types.json
    schema = {"fields": {"old": {"type": "string"}}, "primary_id_field": "old"}
    project = tmp_path / "proj"
    project.mkdir()
    noun_file = project / "noun_types.json"
    noun_file.write_text(json.dumps({"Test": schema}))
    adj_file = project / "adjective_types.json"
    adj_data = [{"adjective": "old", "applies_to": ["Test"]}]
    adj_file.write_text(json.dumps(adj_data))

    nt = NounType("Test", schema, noun_file)
    nt.rename_field("old", "new")
    # check schema update
    assert "new" in nt.schema["fields"]
    assert nt.schema["primary_id_field"] == "new"
    # check adjective_types.json update
    with open(adj_file) as f:
        updated = json.load(f)
    assert updated[0]["adjective"] == "new"


def test_rename_field_errors(tmp_path):
    schema = {"fields": {"f1": {"type": "string"}}}
    nt = NounType("Test", schema, tmp_path / "noun_types.json")
    # missing old
    with pytest.raises(KeyError):
        nt.rename_field("nope", "new")
    # duplicate new
    nt.schema["fields"]["exists"] = {"type": "string"}
    with pytest.raises(KeyError):
        nt.rename_field("f1", "exists")

### Test for register_noun_type integration ###

def test_register_noun_type_creates_files(tmp_project):
    proj = tmp_project
    register_noun_type(proj, "Sample", BASIC_SCHEMA)
    noun_file = proj / "noun_types.json"
    assert "Sample" in json.loads(noun_file.read_text())
    # Check schema file
    schema_file = proj / "nouns" / "Sample" / "Sample.json"
    assert schema_file.exists()
    # Check items.jsonl exists and is empty
    items = proj / "nouns" / "Sample" / "items.jsonl"
    assert items.exists()
    assert items.read_text() == ""


def test_register_noun_type_duplicate(tmp_project):
    proj = tmp_project
    # First registration
    register_noun_type(proj, "X", BASIC_SCHEMA)
    # Duplicate should error
    with pytest.raises(ValueError):
        register_noun_type(proj, "X", BASIC_SCHEMA)

### Test configure_autogenerate_id ###

def test_configure_autogenerate_id_exit_quick(tmp_path, monkeypatch):
    schema = {}
    noun_file = tmp_path / "noun_types.json"
    noun_file.write_text(json.dumps({}))
    nt = NounType("T", schema, noun_file)
    # Patch menu_prompt to quit immediately
    monkeypatch.setattr('utils.interface.menu_prompt', lambda opts: 'q')
    configure_autogenerate_id(nt)
    assert "autogenerate_segments" in nt.schema
    assert nt.schema["autogenerate_segments"] == []

# ------------------- Tests for edit.py -------------------

def test_edit_no_noun_types(change_tmp_dir, capsys):
    # No projects/MyProj folder
    edit_noun_interactive("MyProj")
    captured = capsys.readouterr()
    assert "❌ No noun types found." in captured.out


def test_edit_empty_noun_file(change_tmp_dir, capsys, monkeypatch):
    # Create empty noun_types.json
    proj = change_tmp_dir / "projects" / "P"
    proj.mkdir(parents=True)
    noun_file = proj / "noun_types.json"
    noun_file.write_text("{}")
    # sem.load_json returns empty dict
    monkeypatch.setattr(sem, "load_json", lambda path: {})

    edit_noun_interactive("P")
    captured = capsys.readouterr()
    assert "❌ noun_types.json is empty." in captured.out


def test_edit_cancelled(change_tmp_dir, capsys, monkeypatch):
    # Create noun_types.json with one entry
    proj = change_tmp_dir / "projects" / "P"
    proj.mkdir(parents=True)
    noun_file = proj / "noun_types.json"
    noun_file.write_text(json.dumps({"Entity": {}}))
    monkeypatch.setattr(sem, "load_json", lambda path: {"Entity": {}})
    # Simulate user input 'q'
    monkeypatch.setattr(builtins, "input", lambda prompt="": "q")

    edit_noun_interactive("P")
    captured = capsys.readouterr()
    assert "❎ Cancelled." in captured.out


def test_edit_invalid_choice(change_tmp_dir, capsys, monkeypatch):
    # Setup
    proj = change_tmp_dir / "projects" / "P"
    proj.mkdir(parents=True)
    noun_file = proj / "noun_types.json"
    noun_file.write_text(json.dumps({"X": {}}))
    monkeypatch.setattr(sem, "load_json", lambda path: {"X": {}})
    # Simulate invalid input
    monkeypatch.setattr(builtins, "input", lambda prompt="": "foo")

    edit_noun_interactive("P")
    captured = capsys.readouterr()
    assert "❌ Invalid selection." in captured.out


def test_edit_success_invokes_interactive_edit(change_tmp_dir, capsys, monkeypatch):
    # Setup
    proj = change_tmp_dir / "projects" / "P"
    proj.mkdir(parents=True)
    noun_file = proj / "noun_types.json"
    noun_file.write_text(json.dumps({"Item": {}}))
    monkeypatch.setattr(sem, "load_json", lambda path: {"Item": {}})
    # First input selects index 0
    monkeypatch.setattr(builtins, "input", lambda prompt="": "0")
    # Stub out interactive_edit
    calls = []
    class FakeNoun(NounType):
        def interactive_edit(self):
            calls.append((self.name, self.schema))
    monkeypatch.setattr('tools.edit.NounType', FakeNoun)

    edit_noun_interactive("P")
    # Confirm our stub was called
    assert calls == [("Item", {})]

# ------------------- Tests for register.py -------------------


def test_register_blank_key(change_tmp_dir, capsys, monkeypatch):
    # Patch selector to return project name 'Proj'
    monkeypatch.setattr(sem, "generate_project_selector", lambda name: "Proj")
    # Patch get_input to return blank
    monkeypatch.setattr(register_module, "get_input", lambda prompt="": "")

    register_noun_interactive(None)
    captured = capsys.readouterr()
    assert "❌ Noun key cannot be blank." in captured.out


def test_register_success_calls_interactive(change_tmp_dir, capsys, monkeypatch):
    # Setup project dir via selector
    monkeypatch.setattr(sem, "generate_project_selector", lambda name: "Proj")
    monkeypatch.setattr(register_module, "get_input", lambda prompt="": "Sample")
    # Stub interactive_register_from_context
    called = []
    def fake_interactive(self, existing, alias_path):
        called.append((self.name, existing, alias_path))
        return True
    monkeypatch.setattr(NounType, "interactive_register_from_context", fake_interactive)

    register_noun_interactive(None)
    out = capsys.readouterr().out
    # Success message printed
    assert "🎉 Noun 'Sample' fully configured." in out
    # Stub was invoked with expected args
    # existing is empty dict, alias_path points under projects/Proj/aliases/nouns.json
    assert called, "interactive_register_from_context not called"
    name, existing, alias_path = called[0]
    assert name == "Sample"
    assert existing == {}
    assert alias_path == Path("projects") / "Proj" / "aliases" / "nouns.json"
