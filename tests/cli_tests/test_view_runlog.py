import json
import pytest
import builtins
from pathlib import Path

import tools.view_runlog as vr
from tools.view_runlog import (
    load_verb_log_items,
    derive_status,
    format_verb_table,
    view_runlog_main
)

# Fixture to isolate filesystem and cwd
@pytest.fixture(autouse=True)
def change_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path

# ----------------------------
# Unit tests for load_verb_log_items
# ----------------------------

def test_load_verb_log_items_no_file(tmp_path, capsys):
    # No log file
    items = load_verb_log_items(Path("Proj"), "G")
    assert items == []
    out = capsys.readouterr().out
    assert "⚠️ Log file not found" in out


def test_load_verb_log_items_mixed_lines(tmp_path, capsys):
    # Create project and log with valid, invalid, non-object, and blank lines
    proj = tmp_path / "projects" / "Proj"
    log_dir = proj / "verbs" / "G"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "G_log.jsonl"
    with open(log_file, "w") as f:
        f.write("{\"a\":1}\n")       # valid
        f.write("[1,2,3]\n")         # non-object
        f.write("{bad json}\n")      # malformed
        f.write("\n")                # blank
    items = load_verb_log_items(Path("Proj"), "G")
    out = capsys.readouterr().out
    assert items == [{"a": 1}]
    # Warnings should be printed
    assert any("Not a JSON object" in line for line in out.splitlines())
    assert any("malformed" in line.lower() for line in out.splitlines())

# ----------------------------
# Unit tests for derive_status
# ----------------------------

def test_derive_status_all_done():
    entry = {"x": "1", "y": "2"}
    assert derive_status(entry, ["x", "y"]) == "✅ Done"


def test_derive_status_in_progress():
    entry = {"x": "", "y": "2"}
    assert derive_status(entry, ["x", "y"]) == "🔄 In Progress"


def test_derive_status_pending():
    entry = {"x": "", "y": ""}
    assert derive_status(entry, ["x", "y"]) == "⏳ Pending"

# ----------------------------
# Unit tests for format_verb_table
# ----------------------------

def test_format_verb_table_empty():
    assert format_verb_table([], "G", ["a"]) == "⚠️ No entries found."


def test_format_verb_table_basic():
    items = [
        {"a": "1", "b": "2"},
        {"a": "", "b": "3"}
    ]
    table = format_verb_table(items, "G", ["a", "b"])
    lines = table.splitlines()
    # Header row
    assert lines[0].startswith("| #")
    assert "a" in lines[0] and "b" in lines[0] and "__status" in lines[0]
    # Two data rows
    data_rows = [l for l in lines[2:] if l.startswith("| ")]
    assert len(data_rows) == 2

# ----------------------------
# Integration tests for view_runlog_main
# ----------------------------

def test_view_runlog_main_view(tmp_path, monkeypatch, capsys):
    proj = tmp_path / "projects" / "P"
    cfg_dir = proj / "verbs" / "G"
    cfg_dir.mkdir(parents=True)
    # verb_types.json not used here
    (proj / "verb_types.json").write_text(json.dumps({}))
    config = {"fields": {"id": {"type": "string", "required": True}}}
    (cfg_dir / "G_log_config.json").write_text(json.dumps(config))
    (cfg_dir / "G_log.jsonl").write_text(json.dumps({"id": "run1", "x": "y"}) + "\n")

    # Stub open_data_dump
    called = []
    monkeypatch.setattr(vr, "open_data_dump", lambda p, g, item: called.append(item))

    # Inputs: invalid, view, select 0, quit
    inputs = iter(["x", "v", "0", "q"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(inputs))

    view_runlog_main("P", "G")
    out = capsys.readouterr().out
    assert "Invalid" in out
    assert '"id": "run1"' in out
    assert called == []


def test_view_runlog_main_data_dump(tmp_path, monkeypatch):
    proj = tmp_path / "projects" / "P"
    cfg_dir = proj / "verbs" / "G"
    cfg_dir.mkdir(parents=True)
    (proj / "verb_types.json").write_text(json.dumps({}))
    config = {"fields": {"id": {"type": "string", "required": True}}}
    (cfg_dir / "G_log_config.json").write_text(json.dumps(config))
    (cfg_dir / "G_log.jsonl").write_text(json.dumps({"id": "run1"}) + "\n")

    called = []
    monkeypatch.setattr(vr, "open_data_dump", lambda p, g, item: called.append((p, g, item)))

    inputs = iter(["d", "0", "q"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(inputs))

    view_runlog_main("P", "G")
    assert len(called) == 1
    proj_arg, group_arg, entry_arg = called[0]
    assert str(proj_arg).endswith("projects/P")
    assert group_arg == "G"
    assert entry_arg["id"] == "run1"
