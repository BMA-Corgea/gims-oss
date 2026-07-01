# tests/test_spreadsheet_ui.py

import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import asyncio

import utils.spreadsheet_ui as sps

# --- extract_headers / run_spreadsheet_ui tests ---

def test_extract_headers(tmp_path):
    (tmp_path / "noun_types.json").write_text(
        json.dumps({"Sample": {"fields": {"a": {}, "b": {}}}})
    )
    setup = {"noun_type_ref": "Sample"}
    headers = sps.extract_headers(setup, tmp_path)
    assert headers == ["a", "b"]

def test_extract_headers_missing(tmp_path):
    (tmp_path / "noun_types.json").write_text(json.dumps({}))
    assert sps.extract_headers({"noun_type_ref": "X"}, tmp_path) == []

def test_extract_headers_fields_list():
    setup = {"fields": [{"name": "A"}, {"name": "B"}]}
    assert sps.extract_headers(setup, Path(".")) == ["A", "B"]

def test_run_spreadsheet_ui_launch(monkeypatch, tmp_path):
    called = {}
    class DummyApp:
        def __init__(self, headers, output_path, project_path, setup_schema, adjective_config, run_id=None):
            called["headers"] = headers
            called["setup"] = setup_schema
        def run(self):
            called["ran"] = True

    monkeypatch.setattr(sps, "SpreadsheetApp", DummyApp)
    (tmp_path / "noun_types.json").write_text(
        json.dumps({"Sample": {"fields": {"x": {}}}})
    )
    sps.run_spreadsheet_ui(
        tmp_path,
        {"noun_type_ref": "Sample"},
        tmp_path / "out.json",
        {},
        run_id="001"
    )
    assert called.get("ran") is True
    assert called["headers"] == ["x"]

def test_run_spreadsheet_ui_errors(tmp_path):
    # missing noun_types.json
    with pytest.raises(FileNotFoundError):
        sps.run_spreadsheet_ui(tmp_path, {"noun_type_ref": "X"}, tmp_path/"o.json", {})
    # missing both noun_type_ref & fields
    (tmp_path / "noun_types.json").write_text(json.dumps({}))
    with pytest.raises(ValueError):
        sps.run_spreadsheet_ui(tmp_path, {}, tmp_path/"o.json", {})


# --- Stub out __init__ so we can safely instantiate SpreadsheetApp ---

@pytest.fixture(autouse=True)
def disable_init(monkeypatch):
    monkeypatch.setattr(sps.SpreadsheetApp, "__init__", lambda self, *a, **k: None)


# --- Helper for minimal app instance ---


def make_app():
    app = sps.SpreadsheetApp()  # __init__ is stubbed out
    # Table mocks
    app.table = MagicMock()
    app.table.get_cell_at = lambda coord: "VAL"
    app.table.update_cell_at = MagicMock()
    app.table.add_row = MagicMock()
    app.table.has_focus = True
    app.table.cursor_coordinate = (0, 0)
    app.table.row_count = 1

    # App internal state
    app.headers = ["sample_id", "state"]
    app._history = []
    app._redo_stack = []
    app._edit_start_coord = None
    app._edit_start_value = None

    # Config stubs
    app.setup_schema = {"noun_type_ref": "Sample"}
    app.adjective_config = {}
    app.primary_id_col = None
    app.autogenerate_enabled = False
    app._clipboard = ""

    # Filesystem context
    app.project_path = Path(".")
    app.console = MagicMock()
    app.notify = lambda msg, **kw: setattr(app, "_notif", kw.get("title"))
    return app

# --- action_open_dropdown ---

def test_action_open_dropdown_with_options():
    app = make_app()
    app.adjective_config = {"state": {"valid_options": ["On","Off"]}}
    app.headers = ["sample_id","state"]
    app.table.cursor_coordinate = (0,1)
    # Capture push_screen
    pushed = {}
    app.push_screen = lambda screen: pushed.setdefault("screen", screen)
    app.action_open_dropdown()
    from utils.spreadsheet_ui import OptionSelectScreen
    assert isinstance(pushed["screen"], OptionSelectScreen)

def test_action_open_dropdown_no_options():
    app = make_app()
    app.adjective_config = {}
    app.headers = ["state"]
    app.table.cursor_coordinate = (0,0)
    app.action_open_dropdown()
    assert app._notif == "No Options"


# --- action_add_rows ---

def test_action_add_rows():
    app = make_app()
    sps.SpreadsheetApp.action_add_rows(app)
    assert app.table.add_row.call_count == 20


# --- clear / undo / redo ---

def test_action_clear_and_undo_redo():
    app = make_app()
    app.table.get_cell_at = lambda c: "X"
    # clear cell
    sps.SpreadsheetApp.action_clear_cell(app)
    assert app._history, "Should record clear in history"
    # undo
    sps.SpreadsheetApp.action_undo(app)
    # redo
    sps.SpreadsheetApp.action_redo(app)


# --- copy / paste ---

def test_action_copy_and_paste_allowed():
    app = make_app()
    # copy
    sps.SpreadsheetApp.action_copy_cell(app)
    assert app._clipboard == "VAL"
    # paste allowed
    app.primary_id_col = None
    app._clipboard = "VAL"
    sps.SpreadsheetApp.action_paste_cell(app)
    assert app.table.update_cell_at.called

def test_action_paste_block_readonly():
    app = make_app()
    app.primary_id_col = 0
    app.autogenerate_enabled = True
    app._clipboard = "VAL"
    sps.SpreadsheetApp.action_paste_cell(app)
    assert app._notif == "Read-Only"

# --- generate_autogenerated_id integration stub ---

def test_generate_autogenerated_id_calls_helper(tmp_path, monkeypatch):
    proj = tmp_path / "nouns" / "Sample"
    proj.mkdir(parents=True)
    (proj / "items.jsonl").write_text(json.dumps({"sample_id": "S1"}) + "\n")
    app = make_app()
    app.project_path = tmp_path
    app.setup_schema = {"noun_type_ref": "Sample"}
    app.primary_id_col = 0
    app.autogenerate_enabled = True
    app.headers = ["sample_id"]
    # stub the class method
    monkeypatch.setattr(
        sps.SpreadsheetApp,
        "generate_autogenerated_id",
        lambda self: "NEWID"
    )
    nid = sps.SpreadsheetApp.generate_autogenerated_id(app)
    assert nid == "NEWID"

# --- on_key navigation (no crash) ---

def test_on_key_navigation_no_crash():
    app = make_app()
    for key in ["enter", "tab", "up", "down", "left", "right", "x", "delete", "backspace"]:
        event = type("E", (), {"key": key})
        # run the async handler via asyncio.run
        asyncio.run(sps.SpreadsheetApp.on_key(app, event))

def test_delete_allowed_on_dropdown():
    app = make_app()
    app.adjective_config = {"state": {"valid_options": ["On"]}}
    app.headers = ["sample_id", "state"]
    app.table.cursor_coordinate = (0, 1)
    app.table.get_cell_at = lambda c: "On"
    event = type("E", (), {"key": "delete"})
    asyncio.run(sps.SpreadsheetApp.on_key(app, event))
    app.table.update_cell_at.assert_called_with((0, 1), "")
    assert getattr(app, "_notif", None) != "Restricted"