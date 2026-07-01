import json
import pytest
import builtins
from pathlib import Path

import tools.create_run as cr
from tools.create_run import (
    list_available_verbs,
    load_verb_metadata,
    load_log_config,
    save_log_config,
    prompt_for_log_fields,
    add_log_entry
)

# Fixture to isolate filesystem and working dir
@pytest.fixture(autouse=True)
def change_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path

# ----------------------------
# Unit tests for list/load/save functions
# ----------------------------

def test_list_available_verbs_no_file(capsys):
    # Should return empty and print an error if verb_types.json missing
    res = list_available_verbs(Path("./nope"))
    assert res == []
    assert "❌ No verb_types.json found." in capsys.readouterr().out


def test_list_available_verbs_success(tmp_path):
    proj = tmp_path / "projects" / "P"
    proj.mkdir(parents=True)
    data = {"v1": {}, "v2": {}}
    (proj / "verb_types.json").write_text(json.dumps(data))
    res = list_available_verbs(proj)
    assert set(res) == {"v1", "v2"}


def test_load_verb_metadata(tmp_path):
    proj = tmp_path / "projects" / "P"
    proj.mkdir(parents=True)
    data = {"abc": {"verb_group": "G"}}
    (proj / "verb_types.json").write_text(json.dumps(data))
    meta = load_verb_metadata(proj, "abc")
    assert meta == {"verb_group": "G"}
    # Missing verb yields empty dict
    assert load_verb_metadata(proj, "zzz") == {}


def test_load_log_config_success(tmp_path):
    proj = tmp_path / "projects" / "P"
    cfg_dir = proj / "verbs" / "G"
    cfg_dir.mkdir(parents=True)
    config = {"fields": {}}
    (cfg_dir / "G_log_config.json").write_text(json.dumps(config))
    loaded = load_log_config(proj, "G")
    assert loaded == config


def test_load_log_config_missing(tmp_path):
    proj = tmp_path / "projects" / "P"
    proj.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        load_log_config(proj, "G")


def test_save_log_config(tmp_path):
    proj = tmp_path / "projects" / "P"
    cfg_dir = proj / "verbs" / "G"
    cfg_dir.mkdir(parents=True)
    config = {"fields": {"x": {"type": "int"}}}
    save_log_config(proj, "G", config)
    path = cfg_dir / "G_log_config.json"
    assert path.exists()
    assert json.loads(path.read_text()) == config

# ----------------------------
# Unit tests for prompt_for_log_fields
# ----------------------------

def test_prompt_for_log_fields_success(monkeypatch):
    log_cfg = {"fields": {"a": {"type": "string", "required": True},
                              "b": {"type": "string", "required": False},
                              "test_type": {"type": "string"}}}
    # stub input for a and b
    inputs = iter(["valA", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    result = prompt_for_log_fields(log_cfg)
    assert result == {"a": "valA", "b": ""}


def test_prompt_for_log_fields_missing_required(monkeypatch):
    log_cfg = {"fields": {"a": {"type": "string", "required": True}}}
    # user enters empty
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    with pytest.raises(ValueError) as ei:
        prompt_for_log_fields(log_cfg)
    assert "Field 'a' is required" in str(ei.value)

# ----------------------------
# Integration tests for add_log_entry
# ----------------------------

def test_add_log_entry_no_group(tmp_path):
    # verb meta missing verb_group
    proj = tmp_path / "projects" / "P"
    (proj).mkdir(parents=True)
    (proj / "verb_types.json").write_text(json.dumps({"V": {}}))
    with pytest.raises(ValueError) as ei:
        add_log_entry(proj, "V")
    assert "does not have an associated verb group" in str(ei.value)


def test_add_log_entry_missing_config(tmp_path):
    proj = tmp_path / "projects" / "P"
    (proj).mkdir(parents=True)
    (proj / "verb_types.json").write_text(json.dumps({"V": {"verb_group": "G"}}))
    with pytest.raises(FileNotFoundError):
        add_log_entry(proj, "V")


def test_add_log_entry_missing_primary_after_test_type(tmp_path, monkeypatch, capsys):
    # config without primary_id
    proj = tmp_path / "projects" / "P"
    cfg_dir = proj / "verbs" / "G"
    cfg_dir.mkdir(parents=True)
    (proj / "verb_types.json").write_text(json.dumps({"V": {"verb_group": "G"}}))
    (cfg_dir / "G_log_config.json").write_text(json.dumps({"fields": {}}))
    # stub prompt_for_log_fields so not to loop
    monkeypatch.setattr(cr, "prompt_for_log_fields", lambda cfg: {})
    # auto-add test_type then missing primary_id triggers
    with pytest.raises(ValueError) as ei:
        add_log_entry(proj, "V")
    assert "'primary_id' not set" in str(ei.value)


def test_add_log_entry_full_flow(tmp_path, monkeypatch, capsys):
    # setup project, verb_types, log_config with primary_id
    proj = tmp_path / "projects" / "P"
    cfg_dir = proj / "verbs" / "G"
    proj.mkdir(parents=True)
    cfg_dir.mkdir(parents=True)
    # verb_types.json with data_entry_schema
    verb_meta = {"V": {"verb_group": "G", "data_entry_schema": {"instructions": ["step1"],
                                                                  "raw_data_inputs": ["A", "B"],
                                                                  "interpretation": {"tabs": ["X"]}}}}
    (proj / "verb_types.json").write_text(json.dumps(verb_meta))
    # initial log_config
    init_cfg = {"fields": {"id": {"type": "string", "required": True}}, "primary_id": "id"}
    (cfg_dir / "G_log_config.json").write_text(json.dumps(init_cfg))
    # stub user input for prompt_for_log_fields
    monkeypatch.setattr(cr, "prompt_for_log_fields", lambda cfg: {"id": "run123"})

    add_log_entry(proj, "V")

    # check that test_type was added to config file
    saved_cfg = json.loads((cfg_dir / "G_log_config.json").read_text())
    assert "test_type" in saved_cfg["fields"]

    # check log entry written
    log_file = cfg_dir / "G_log.jsonl"
    lines = log_file.read_text().splitlines()
    entry = json.loads(lines[-1])
    assert entry["id"] == "run123"
    assert entry["test_type"] == "V"

    # check data dump
    dump_root = cfg_dir / "data_dumps" / "run123"
    assert (dump_root / "Instructions.md").exists()
    assert (dump_root / "A").is_dir()
    assert (dump_root / "B").is_dir()
    assert (dump_root / "DataEntry.json").exists()
    assert (dump_root / "X.csv").exists()
    status = json.loads((dump_root / "Status.json").read_text())
    assert status["interpretation"]["manual_approval"] is False
