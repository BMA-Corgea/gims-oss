import json
import pytest
from pathlib import Path
from utils.handlers import conjunction

@pytest.fixture
def tmp_project(tmp_path):
    """Creates a dummy project structure with noun_types.json"""
    proj = tmp_path / "projects" / "MyProj"
    proj.mkdir(parents=True)
    (proj / "noun_types.json").write_text(json.dumps({
        "Sample": {
            "fields": {
                "id": {"required": True}
            },
            "primary_id_field": "id"
        }
    }))
    return proj

def test_prompt_status_overrides_add_and_finish(monkeypatch, tmp_project):
    # Prepare inputs for adding an override and finishing
    inputs = iter([
        'a',             # Add override
        'Quarantine',    # Override type
        '0',             # Status (Error)
        'q',             # Done with required fields editing
        'q'              # Finish menu_prompt loop
    ])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("utils.interface.menu_prompt", lambda opts: next(inputs))
    monkeypatch.setattr("utils.interface.indexed_choice", lambda opts, msg: 0)

    overrides = conjunction.prompt_status_overrides(project_name="MyProj")
    assert overrides, "Overrides list should not be empty after adding one"
    assert overrides[0]['name'] == 'Quarantine'
    assert overrides[0]['status'] == 'Error'

def test_prompt_status_overrides_single(monkeypatch, tmp_project):
    existing = {
        "name": "Quarantine",
        "status": "Error",
        "fields": ["note"]
    }
    inputs = iter([
        '',    # keep name
        '0',   # change status to Error
        'q'    # finish field editing
    ])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("utils.interface.menu_prompt", lambda opts: next(inputs))
    monkeypatch.setattr("utils.interface.indexed_choice", lambda opts, msg: 0)

    updated = conjunction.prompt_status_overrides_single(existing, project_name="MyProj")
    assert updated['name'] == 'Quarantine'
    assert updated['status'] == 'Error'

def test_get_and_delete_conjunction(tmp_path):
    run_path = tmp_path / "run"
    run_path.mkdir()
    status_file = run_path / "Status.json"
    status_file.write_text(json.dumps({
        "conjunction": {"override_type": "Quarantine"}
    }))

    conj = conjunction.get_conjunction(run_path)
    assert conj is not None
    assert conj["override_type"] == "Quarantine"

    conjunction.delete_conjunction(run_path)
    data = json.loads(status_file.read_text())
    assert "conjunction" not in data

def test_save_conjunction(tmp_path, monkeypatch):
    run_path = tmp_path / "run"
    run_path.mkdir()
    status_file = run_path / "Status.json"
    status_file.write_text(json.dumps({}))

    # Prepare inputs
    inputs = iter([
        'Quarantine',  # override_type
        'AB',          # initials
        '2025-07-09',  # date
        'Note here',   # note
        ''             # linked_run
    ])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("utils.semantics.is_valid_date", lambda v, fmt: True)

    project_path = tmp_path / "projects" / "MyProj"
    project_path.mkdir(parents=True)
    conjunction.save_conjunction(run_path, project_path, "run123", "Tests")

    saved = json.loads(status_file.read_text())
    assert saved.get("conjunction", {}).get("override_type") == "Quarantine"

def test_manage_conjunctions_add_and_delete(monkeypatch, tmp_path):
    project_path = tmp_path / "projects" / "MyProj"
    project_path.mkdir(parents=True)
    verb_types = {
        "TestVerb": {
            "status_values": [
                {"name": "Quarantine", "status": "Error", "fields": ["note"]}
            ]
        }
    }
    (project_path / "verb_types.json").write_text(json.dumps(verb_types))

    dump_root = tmp_path / "run"
    dump_root.mkdir()
    status_file = dump_root / "Status.json"
    status_file.write_text(json.dumps({
        "conjunctions": []
    }))

    run_entry = {"test_type": "TestVerb"}

    inputs = iter([
        'a',          # choose 'add override' action
        '0',          # select Quarantine override (indexed_choice)
        'note_value', # enter note field value
        'q'           # exit menu
    ])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    monkeypatch.setattr("utils.interface.menu_prompt", lambda opts: next(inputs))
    monkeypatch.setattr("utils.interface.indexed_choice", lambda opts, msg: 0)

    conjunction.manage_conjunctions(project_path, dump_root, run_entry)

    # Confirm override saved
    data = json.loads(status_file.read_text())
    assert data["conjunctions"], "At least one conjunction should be added"