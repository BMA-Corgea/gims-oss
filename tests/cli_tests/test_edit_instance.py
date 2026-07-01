import json
import pytest
from pathlib import Path
import builtins

import tools.edit_instance as ei
from tools.edit_instance import (
    load_schema,
    load_items,
    save_items,
    edit_item
)

# Fixture to isolate filesystem
@pytest.fixture(autouse=True)
def change_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path

# ----------------------------
# Unit tests for load_schema
# ----------------------------

def test_load_schema_noun(tmp_path):
    proj = tmp_path / "projects" / "P"
    noun_dir = proj / "nouns" / "N"
    noun_dir.mkdir(parents=True)
    # Create noun_types.json
    schema = {"N": {"fields": {"f1": {"type": "string"}}, "primary_id_field": "f1"}}
    (proj / "noun_types.json").write_text(json.dumps(schema))
    # Ensure items file exists
    (noun_dir / "items.jsonl").write_text("")

    fields, data_path, pid, wtype = load_schema(proj, "N")
    assert fields == schema["N"]["fields"]
    assert data_path == noun_dir / "items.jsonl"
    assert pid == "f1"
    assert wtype == "noun"


def test_load_schema_verb(tmp_path):
    proj = tmp_path / "projects" / "P"
    cfg_dir = proj / "verbs" / "G"
    cfg_dir.mkdir(parents=True)
    # Create verb_types.json
    verb_defs = {"G": {"verb_group": "G"}}
    (proj / "verb_types.json").write_text(json.dumps(verb_defs))
    # Create log config
    config = {"fields": {"a": {"type": "string"}}, "primary_id": "a"}
    (cfg_dir / "G_log_config.json").write_text(json.dumps(config))
    # Ensure log file exists
    (cfg_dir / "G_log.jsonl").write_text("")

    fields, data_path, pid, wtype = load_schema(proj, "G")
    assert fields == config["fields"]
    assert data_path == cfg_dir / "G_log.jsonl"
    assert pid == "a"
    assert wtype == "verb"


def test_load_schema_not_found(tmp_path):
    proj = tmp_path / "projects" / "P"
    proj.mkdir(parents=True)
    with pytest.raises(ValueError):
        load_schema(proj, "X")

# ----------------------------
# Unit tests for load_items and save_items
# ----------------------------

def test_load_items_empty(tmp_path):
    data_path = tmp_path / "data.jsonl"
    # no file -> empty list
    loaded = load_items(data_path)
    assert loaded == []


def test_load_items_and_save(tmp_path):
    data_path = tmp_path / "data.jsonl"
    # write some JSON lines
    lines = [{"id": 1}, {"id": 2}]
    data_path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    loaded = load_items(data_path)
    assert loaded == lines

    # Modify and save
    loaded[0]["id"] = 10
    save_items(data_path, loaded)
    reloaded = load_items(data_path)
    assert reloaded[0]["id"] == 10

# ----------------------------
# Unit tests for edit_item
# ----------------------------

def test_edit_item_quit_immediately(monkeypatch):
    schema = {"a": {"type": "string"}}
    item = {"a": "orig"}
    # simulate 'q' to quit
    monkeypatch.setattr(builtins, "input", lambda prompt="": "q")
    updated = edit_item(schema, item.copy(), Path("."), "noun", "N")
    assert updated == item


def test_edit_item_plain_field(monkeypatch):
    schema = {"a": {"type": "string"}, "b": {"type": "string", "required": True}}
    item = {"a": "1", "b": "2"}
    # inputs: select 0, set 'X', then quit
    inputs = iter(["0", "X", "q"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(inputs))
    updated = edit_item(schema, item.copy(), Path("."), "noun", "N")
    assert updated["a"] == "X"
    assert updated["b"] == "2"


def test_edit_item_adjective_no_config(monkeypatch, tmp_path, capsys):
    schema = {"adj": {"type": "adjective"}}
    item = {"adj": "old"}
    # no adjective_types.json
    inputs = iter(["0", "new", "q"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(inputs))
    updated = edit_item(schema, item.copy(), tmp_path, "noun", "N")
    out = capsys.readouterr().out
    assert "Treating as plain text" in out
    assert updated["adj"] == "new"


def test_edit_item_adjective_with_config(monkeypatch, tmp_path):
    schema = {"adj": {"type": "adjective"}}
    item = {"adj": "old"}
    # create adjective_types.json
    adj_list = [{"adjective": "adj", "adjective_class": "Fake"}]
    (tmp_path / "adjective_types.json").write_text(json.dumps(adj_list))
    # stub verb_types.json
    (tmp_path / "verb_types.json").write_text(json.dumps({}))
    # fake handler
    class Fake:
        def __init__(self, *a, **k): pass
        def prompt_instance_edit(self, f, c): return "handled"
    monkeypatch.setattr(ei, "get_adjective_class_handler", lambda cls: Fake)
    inputs = iter(["0", "q"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(inputs))
    updated = edit_item(schema, item.copy(), tmp_path, "noun", "N")
    assert updated["adj"] == "handled"
