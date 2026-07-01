import json
from utils import semantics
from pathlib import Path


def test_infer_type():
    assert semantics.infer_type("10") == 10
    assert semantics.infer_type("10.5") == 10.5
    assert semantics.infer_type("true") is True
    assert semantics.infer_type("abc") == "abc"


def test_is_valid_date():
    assert semantics.is_valid_date("2023-01-02", "yyyy-mm-dd")
    assert not semantics.is_valid_date("2023-13-01", "yyyy-mm-dd")


def test_check_if_word_exists(tmp_path, monkeypatch):
    proj = tmp_path / "projects" / "P"
    proj.mkdir(parents=True)
    (proj / "noun_types.json").write_text(json.dumps({"N": {}}))
    monkeypatch.chdir(tmp_path)
    assert semantics.check_if_word_exists("P", "noun", "N")
    assert not semantics.check_if_word_exists("P", "verb", "V")


def test_get_display_name_alias():
    cfg = {"aliases": {"nouns": {"S": "Sample"}}}
    assert semantics.get_display_name("S", "nouns", cfg) == "Sample"


def test_get_display_name_default():
    cfg = {"aliases": {"nouns": {}}}
    assert semantics.get_display_name("S", "nouns", cfg) == "S"


def test_load_and_save_json(tmp_path):
    path = tmp_path / "data.json"
    data = [1,2]
    semantics.save_json(path, data)
    assert semantics.load_json(path) == data


def test_get_input_with_default(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert semantics.get_input("Q", default="d") == "d"


def test_confirm_list(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "a,b,, c ")
    assert semantics.confirm_list("Enter") == ["a","b","c"]


def test_generate_project_selector(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    (projects / "P1").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a: "0")
    assert semantics.generate_project_selector(None) == "P1"



def test_validate_item_against_schema(tmp_path, monkeypatch):
    from utils.handlers.noun import NounType
    schema = {"fields": {"f": {"type":"string", "required": True}}, "primary_id_field":"f"}
    noun_file = tmp_path / "noun_types.json"
    noun_file.write_text(json.dumps({"N": schema}))
    nt = NounType("N", schema, noun_file)
    nt.project_path = tmp_path
    errors = semantics.validate_item_against_schema({"f":""}, nt)
    assert errors and "required" in errors[0]