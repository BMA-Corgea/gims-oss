import json
import re
import pytest
import builtins
from pathlib import Path

import tools.view as view
from tools.view import (
    load_items,
    apply_filter,
    apply_exclude,
    apply_sort,
    format_table,
    prompt_field_choice,
    interactive_loop,
    enter_investigate_mode,
    parse_args
)

# Fixture to isolate filesystem
@pytest.fixture(autouse=True)
def change_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path

# ----------------------------
# Tests for load_items
# ----------------------------

def test_load_items_no_file(capsys):
    items = load_items("N", "Proj")
    assert items == []
    out = capsys.readouterr().out
    assert "❌ No data found" in out


def test_load_items_success(tmp_path):
    items_dir = tmp_path / "projects" / "Proj" / "nouns" / "N"
    items_dir.mkdir(parents=True)
    data = [{"a":1}, {"a":2}]
    file = items_dir / "items.jsonl"
    file.write_text("".join(json.dumps(d)+"\n" for d in data))
    items = load_items("N", "Proj")
    assert items == data

# ----------------------------
# Tests for apply_filter/exclude/sort
# ----------------------------

def test_apply_filter_and_exclude_and_sort():
    items = [{"x":"foo"}, {"x":"bar"}, {"x":"foobar"}]
    assert apply_filter(items, "x", "foo") == [{"x":"foo"}, {"x":"foobar"}]
    assert apply_exclude(items, "x", "foo") == [{"x":"bar"}]
    sorted_items = apply_sort(items, "x")
    assert [i["x"] for i in sorted_items] == ["bar","foo","foobar"]

# ----------------------------
# Tests for format_table
# ----------------------------

def test_format_table_empty():
    assert format_table([], "N") == "⚠️ No entries found."


def test_format_table_basic():
    items = [{"n_id":"1","v":10}, {"n_id":"2","v":5}]
    tbl = format_table(items, "N")
    lines = tbl.splitlines()
    # header row contains n_id and v
    assert "n_id" in lines[0] and "v" in lines[0]
    # two data rows
    data_rows = lines[2:]
    assert len(data_rows) == 2


def test_format_table_complex():
    items = [{"n_id":"1","list":[1,2],"map":{"k":"v"}}]
    tbl = format_table(items, "N")
    assert '"k": "v"' in tbl
    assert '[1, 2]' in tbl

# ----------------------------
# Tests for prompt_field_choice
# ----------------------------

def test_prompt_field_choice_quit(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", lambda prompt="": "q")
    res = prompt_field_choice(["a","b"])
    assert res is None


def test_prompt_field_choice_invalid(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", lambda prompt="": "x")
    res = prompt_field_choice(["a","b"])
    assert res is None
    out = capsys.readouterr().out
    assert "Invalid choice" in out


def test_prompt_field_choice_valid(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt="": "1")
    res = prompt_field_choice(["a","b"])
    assert res == "b"

# ----------------------------
# Tests for parse_args
# ----------------------------

def test_parse_args_various():
    opts = parse_args(["--sort","f","--filter","a:1","--exclude","b:2"])
    assert opts["sort"] == "f"
    assert opts["filter"] == [("a","1")]
    assert opts["exclude"] == [("b","2")]
    opts2 = parse_args(["foo","--filter","x:val"])
    assert opts2["filter"] == [("x","val")]

# ----------------------------
# Tests for interactive_loop
# ----------------------------

def test_interactive_loop_quit(monkeypatch):
    items = [{"a":1},{"a":2}]
    monkeypatch.setattr(builtins, "input", lambda prompt="": "q")
    res = interactive_loop(items, "N", "Proj")
    assert res == items

# ----------------------------
# Tests for enter_investigate_mode
# ----------------------------

def test_enter_investigate_mode_quit(monkeypatch, tmp_path, capsys):
    proj = tmp_path / "projects" / "P"
    noun_dir = proj / "nouns" / "N"
    proj.mkdir(parents=True)
    noun_dir.mkdir(parents=True)
    items = [{"n_id":"1"}]
    monkeypatch.setattr(builtins, "input", lambda prompt="": "q")
    enter_investigate_mode("P","N",items)
    out = capsys.readouterr().out
    assert "|" in out


def test_enter_investigate_mode_no_action_req(monkeypatch, tmp_path, capsys):
    proj = tmp_path / "projects" / "P"
    noun_dir = proj / "nouns" / "N"
    proj.mkdir(parents=True)
    noun_dir.mkdir(parents=True)
    schema = {"N": {"fields": {"a": {"type": "string"}}}}
    (proj / "noun_types.json").write_text(json.dumps(schema))
    items = [{"n_id": "1", "a": "x"}]
    monkeypatch.setattr(builtins, "input", lambda prompt="": "0")
    enter_investigate_mode("P", "N", items)
    out = capsys.readouterr().out
    assert "No ActionRequirement defined" in out


def test_enter_investigate_mode_missing_adj_config(monkeypatch, tmp_path, capsys):
    proj = tmp_path / "projects" / "P"
    noun_dir = proj / "nouns" / "N"
    proj.mkdir(parents=True)
    noun_dir.mkdir(parents=True)
    schema = {"N": {"fields": {"req": {"type": "adjective", "adjective_class": "ActionRequirement"}}, "primary_id_field": "req"}}
    (proj / "noun_types.json").write_text(json.dumps(schema))
    # Create empty adjective_types.json to avoid FileNotFoundError
    (proj / "adjective_types.json").write_text(json.dumps([]))
    items = [{"n_id": "1", "req": "val"}]
    monkeypatch.setattr(builtins, "input", lambda prompt="": "0")
    enter_investigate_mode("P", "N", items)
    out = capsys.readouterr().out
    assert "Configuration for adjective" in out
    
def test_enter_investigate_mode_full_flow(monkeypatch, tmp_path, capsys):
    proj = tmp_path / "projects" / "P"
    noun_dir = proj / "nouns" / "N"
    proj.mkdir(parents=True)
    noun_dir.mkdir(parents=True)
    schema = {"N": {"fields": {"n_id": {"type": "string"}, "req": {"type": "adjective", "adjective_class": "ActionRequirement"}}, "primary_id_field": "n_id"}}
    (proj / "noun_types.json").write_text(json.dumps(schema))
    adj_list = [{"adjective": "req", "adjective_class": "ActionRequirement", "request_options": {"val": ["verb1"]}}]
    (proj / "adjective_types.json").write_text(json.dumps(adj_list))
    verb_meta = {"verb1": {"verb_group": "G", "data_entry_schema": {"raw_data_inputs": []}}}
    (proj / "verb_types.json").write_text(json.dumps(verb_meta))
    items = [{"n_id": "ID1", "req": "val"}]
    monkeypatch.setattr("utils.monitoring.check_next_step", lambda project_path, source_noun_type, source_id, required_verb: [{"linked_id": "L1", "run_id": "R1"}])
    calls = []
    monkeypatch.setattr(
        "utils.monitoring.evaluate_condition",
        lambda project_path, run_id, verb_group, noun_schema, raw_inputs: calls.append((run_id, verb_group)),
    )
    inputs = iter(["0", "q", ""])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(inputs))
    enter_investigate_mode("P", "N", items)
    out = capsys.readouterr().out
    assert "requests: val" in out
    assert "🔎 Verb: verb1" in out
    assert calls == [("R1", "G")]

def test_enter_investigate_mode_run_jump(monkeypatch, tmp_path):
    proj = tmp_path / "projects" / "P"
    noun_dir = proj / "nouns" / "N"
    proj.mkdir(parents=True)
    noun_dir.mkdir(parents=True)
    schema = {
        "N": {
            "fields": {
                "n_id": {"type": "string"},
                "req": {"type": "adjective", "adjective_class": "ActionRequirement"},
            },
            "primary_id_field": "n_id",
        }
    }
    (proj / "noun_types.json").write_text(json.dumps(schema))
    adj_list = [{"adjective": "req", "adjective_class": "ActionRequirement", "request_options": {"val": ["verb1"]}}]
    (proj / "adjective_types.json").write_text(json.dumps(adj_list))
    verb_meta = {"verb1": {"verb_group": "G", "data_entry_schema": {"raw_data_inputs": []}}}
    (proj / "verb_types.json").write_text(json.dumps(verb_meta))
    items = [{"n_id": "ID1", "req": "val"}]

    monkeypatch.setattr(
        "utils.monitoring.check_next_step",
        lambda *a, **k: [{"linked_id": "L1", "run_id": "RUN1"}],
    )
    monkeypatch.setattr("utils.monitoring.evaluate_condition", lambda *a, **k: None)

    called = []
    monkeypatch.setattr("tools.view.open_data_dump", lambda p, g, e: called.append((p, g, e)))

    inputs = iter(["0", "v", "0", "q", ""])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(inputs))

    enter_investigate_mode("P", "N", items)

    assert called
    path_arg, group_arg, entry_arg = called[0]
    assert str(path_arg).endswith("projects/P")
    assert group_arg == "G"
    assert entry_arg == {"run_ID": "RUN1", "verb": "verb1"}