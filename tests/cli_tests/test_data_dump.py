# tests/test_data_dump.py

import json
import shutil
from pathlib import Path
from utils.adjective_ui_loader import load_adjective_field_config
import utils.data_dump as data_dump


def test_load_adjective_field_config_reference(tmp_path):
    # Create fake project path and necessary files
    proj = tmp_path / "projects" / "P"
    proj.mkdir(parents=True)

    # Create noun_types.json
    noun_types = {
        "Sample": {
            "primary_id_field": "sample_id",
            "fields": {
                "tester": {
                    "type": "adjective",
                    "adjective_class": "Reference"
                }
            }
        },
        "User": {
            "primary_id_field": "user_id",
            "fields": {}
        }
    }
    (proj / "noun_types.json").write_text(json.dumps(noun_types))

    # Create adjective_types.json
    adjective_types = [
        {
            "adjective": "tester",
            "adjective_class": "Reference",
            "reference_noun": "User"
        }
    ]
    (proj / "adjective_types.json").write_text(json.dumps(adjective_types))

    # Create items for referenced noun (User)
    user_dir = proj / "nouns" / "User"
    user_dir.mkdir(parents=True)
    (user_dir / "items.jsonl").write_text(
        json.dumps({"user_id": "u1"}) + "\n" + json.dumps({"user_id": "u2"})
    )

    # Run test
    config = load_adjective_field_config(proj, "Sample")

    assert "tester" in config
    assert config["tester"]["adjective_class"] == "Reference"
    assert config["tester"]["reference_noun"] == "User"
    assert sorted(config["tester"]["valid_options"]) == ["u1", "u2"]


def test_load_adjective_field_config_tag(tmp_path):
    proj = tmp_path / "projects" / "P"
    proj.mkdir(parents=True)

    noun_types = {
        "Sample": {
            "primary_id_field": "id",
            "fields": {
                "status": {
                    "type": "adjective",
                    "adjective_class": "Tag",
                }
            }
        }
    }
    (proj / "noun_types.json").write_text(json.dumps(noun_types))

    adjective_types = [
        {
            "adjective": "status",
            "adjective_class": "Tag",
            "valid_options": [
                {"value": "A", "explanation": "", "display_in_id": True},
                {"value": "B", "explanation": "", "display_in_id": False},
            ],
        }
    ]
    (proj / "adjective_types.json").write_text(json.dumps(adjective_types))

    config = load_adjective_field_config(proj, "Sample")

    assert config["status"]["adjective_class"] == "Tag"
    assert config["status"]["valid_options"] == ["A", "B"]


def test_load_verb_schema(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    verb_path = proj / "verb_types.json"
    verb_path.write_text(json.dumps({"Do": {"data_entry_schema": {"fields": []}}}))
    schema = data_dump.load_verb_schema(proj, "Do")
    assert schema == {"fields": []}


def test_toggle_interpretation_approval(tmp_path, monkeypatch):
    status_path = tmp_path / "Status.json"
    status_path.write_text(json.dumps({"interpretation": {"manual_approval": False}}))
    monkeypatch.setattr(data_dump, "menu_prompt", lambda opts: "y")
    data_dump.toggle_interpretation_approval(status_path)
    updated = json.loads(status_path.read_text())
    assert updated["interpretation"]["manual_approval"] is True


def test_show_status_calls_helpers(tmp_path, monkeypatch):
    proj = tmp_path
    run = {"verb": "test"}
    dump_root = tmp_path
    called = {}
    monkeypatch.setattr(data_dump, "get_status_breakdown", lambda *a, **k: {"a": 1})
    monkeypatch.setattr(data_dump, "render_status_bar", lambda b: called.setdefault("bar", True) or "")
    monkeypatch.setattr(data_dump, "print_colored_status", lambda b: called.setdefault("color", True))
    verb_types = {"test": {"data_entry_schema": {"set_up_inputs": {}}}}
    (proj / "verb_types.json").write_text(json.dumps(verb_types))

    # stub indexed_choice: pick "View Status Breakdown" once, then quit
    seq = iter([0, None])
    monkeypatch.setattr(data_dump, "indexed_choice", lambda opts, prompt="": next(seq))

    data_dump.show_status_menu(proj, run, dump_root, raw_inputs=[])
    assert called.get("bar") and called.get("color")


def test_print_csv_empty(tmp_path, capsys):
    path = tmp_path / "f.csv"
    path.write_text("")
    data_dump._print_csv(path)
    assert "Empty CSV" in capsys.readouterr().out


def test_print_csv_missing(tmp_path, capsys):
    data_dump._print_csv(tmp_path / "none.csv")
    assert "not found" in capsys.readouterr().out.lower()


def test_print_json_missing(tmp_path, capsys):
    data_dump._print_json(tmp_path / "x.json")
    assert "not found" in capsys.readouterr().out.lower()


def test_print_json_invalid(tmp_path, capsys):
    f = tmp_path / "bad.json"
    f.write_text("{")
    data_dump._print_json(f)
    assert "corrupt" in capsys.readouterr().out.lower()


def test_open_data_dump_no_primary(capsys, tmp_path):
    data_dump.open_data_dump(tmp_path, "g", {})
    assert "No primary ID" in capsys.readouterr().out


def test_handle_raw_data_zone_flow(tmp_path, monkeypatch, capsys):
    zone = tmp_path / "zone"
    zone.mkdir()
    inputs = iter(["v", "u", str(tmp_path / "f.csv"), "v", "d", "q"])
    (tmp_path / "f.csv").write_text("a,b")
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    # stub upload_file_to_folder to copy and print "Uploaded"
    def stub_upload(folder):
        src = tmp_path / "f.csv"
        dest = folder / src.name
        shutil.copy(src, dest)
        print("Uploaded")
        return dest

    monkeypatch.setattr(data_dump, "upload_file_to_folder", stub_upload)

    data_dump.handle_raw_data_zone("zone", tmp_path)
    out = capsys.readouterr().out
    assert "Uploaded" in out and "File deleted" in out


def test_print_csv_normal(tmp_path, capsys):
    path = tmp_path / "file.csv"
    path.write_text("a,b\n1,2")
    data_dump._print_csv(path)
    out = capsys.readouterr().out
    assert "┌" in out and "1" in out


def test_print_json_valid(tmp_path, capsys):
    f = tmp_path / "data.json"
    f.write_text(json.dumps({"a": 1}))
    data_dump._print_json(f)
    assert "a: 1" in capsys.readouterr().out


def test_toggle_interpretation_approval_new(tmp_path, monkeypatch):
    status_path = tmp_path / "Status.json"
    monkeypatch.setattr(data_dump, "menu_prompt", lambda opts: "y")
    data_dump.toggle_interpretation_approval(status_path)
    updated = json.loads(status_path.read_text())
    assert updated["interpretation"]["manual_approval"] is True


def test_open_data_dump_creates_files(tmp_path, monkeypatch):
    proj = tmp_path
    verb_types = {"Test": {"data_entry_schema": {
        "instructions": ["step"],
        "raw_data_inputs": ["raw"],
        "interpretation": {"tabs": ["tab"]}
    }}}
    (proj / "verb_types.json").write_text(json.dumps(verb_types))
    run_entry = {"run_ID": "1", "verb": "Test"}
    monkeypatch.setattr(data_dump, "indexed_choice", lambda *a, **k: len(a[0]) - 1)
    monkeypatch.setattr(data_dump, "load_adjective_field_config", lambda *a, **k: {})
    monkeypatch.setattr(data_dump, "run_spreadsheet_ui", lambda *a, **k: None)
    data_dump.open_data_dump(proj, "g", run_entry)
    run_root = proj / "verbs" / "g" / "data_dumps" / "1"
    assert (run_root / "DataEntry.json").exists()
    assert (run_root / "Status.json").exists()
    assert (run_root / "Instructions.md").exists()
    assert (run_root / "raw").is_dir()
    assert (run_root / "tab.csv").exists()


def test_handle_raw_data_zone_invalid_upload(tmp_path, monkeypatch, capsys):
    inputs = iter(["u", str(tmp_path / "bad.txt"), "q"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    monkeypatch.setattr(data_dump, "upload_file_to_folder", lambda folder: None)  # prevents file dialog

    (tmp_path / "bad.txt").write_text("x")
    data_dump.handle_raw_data_zone("zone", tmp_path)
    out = capsys.readouterr().out.lower()
    assert "invalid" in out


def test_load_verb_schema_missing_key(tmp_path):
    (tmp_path / "verb_types.json").write_text(json.dumps({"Other": {}}))
    schema = data_dump.load_verb_schema(tmp_path, "Do")
    assert schema == {}


def test_print_csv_uneven_rows(tmp_path, capsys):
    path = tmp_path / "bad.csv"
    path.write_text("a,b\n1\n2,3,4")
    data_dump._print_csv(path)
    out = capsys.readouterr().out
    assert "│" in out


def test_print_json_empty(tmp_path, capsys):
    f = tmp_path / "data.json"
    f.write_text("{}")
    data_dump._print_json(f)
    assert "Empty" in capsys.readouterr().out


def test_print_json_list_root(tmp_path, capsys):
    f = tmp_path / "data.json"
    f.write_text(json.dumps([{"a": 1}]))
    data_dump._print_json(f)
    out = capsys.readouterr().out
    assert "JSON List with 1 items" in out
    assert "[0]" in out
    assert "{'a': 1}" in out


def test_open_data_dump_missing_verb(tmp_path, capsys):
    data_dump.open_data_dump(tmp_path, "group", {"run_ID": "123"})
    out = capsys.readouterr().out
    assert "no ‘test_type’ or ‘verb’" in out.lower()


def test_handle_raw_data_zone_xlsx(tmp_path, monkeypatch, capsys):
    # prepare inputs
    inputs = iter(["u", str(tmp_path / "file.xlsx"), "v", "q"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))
    (tmp_path / "file.xlsx").write_text("dummy excel data")

    # stub upload to copy and print "Uploaded"
    def stub_upload_xlsx(folder):
        src = tmp_path / "file.xlsx"
        dest = folder / src.name
        shutil.copy(src, dest)
        print("Uploaded")
        return dest

    monkeypatch.setattr(data_dump, "upload_file_to_folder", stub_upload_xlsx)
    # stub out the view step so pandas ExcelFile is never invoked
    monkeypatch.setattr(data_dump, "_print_csv_or_xlsx", lambda path: None)

    data_dump.handle_raw_data_zone("zone", tmp_path)
    out = capsys.readouterr().out
    assert "Uploaded" in out


def test_open_data_dump_invalid_action(tmp_path, monkeypatch):
    (tmp_path / "verb_types.json").write_text(json.dumps({
        "Test": {"data_entry_schema": {"instructions": []}}
    }))
    run = {"run_ID": "X", "verb": "Test"}
    # First we return an invalid index to trigger the exception handler,
    # then return None so open_data_dump will break out of its loop.
    choices = iter([999, None])
    monkeypatch.setattr(data_dump, "indexed_choice",
                        lambda opts, prompt_msg="": next(choices))
    monkeypatch.setattr(data_dump, "load_adjective_field_config",
                        lambda *a, **k: {})
    monkeypatch.setattr(data_dump, "run_spreadsheet_ui",
                        lambda *a, **k: None)
    # Should not hang now
    data_dump.open_data_dump(tmp_path, "verbs", run)


def test_toggle_interpretation_approval_corrupt(tmp_path, monkeypatch):
    path = tmp_path / "Status.json"
    path.write_text("{bad json")
    monkeypatch.setattr(data_dump, "menu_prompt", lambda opts: "y")
    data_dump.toggle_interpretation_approval(path)
    updated = json.loads(path.read_text())
    assert updated["interpretation"]["manual_approval"] is True


def test_show_status_missing_verb_config(tmp_path, monkeypatch):
    run = {"verb": "X"}
    dump = tmp_path / "run"
    dump.mkdir()

    # stub indexed_choice to quit immediately
    monkeypatch.setattr(data_dump, "indexed_choice", lambda opts, prompt="": None)
    # should exit cleanly without raising
    data_dump.show_status_menu(tmp_path, run, dump)
    # if we reach here, the test passes
    assert True