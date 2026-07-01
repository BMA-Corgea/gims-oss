import json
from pathlib import Path
from utils import status
from utils import status_ui
from unittest.mock import patch

DUMMY_NOUN_SCHEMA = {}
DUMMY_RAW_INPUTS = []
DUMMY_ADVERB_SCHEMA = {}

def setup_dummy_verb_types(tmp_path):
    vt = tmp_path / "verb_types.json"
    vt.write_text(json.dumps({
        "DUMMY_VERB": {
            "data_entry_schema": {
                "interpretation": {
                    "tabs": ["interpretation"],
                    "method": "parsed"
                }
            }
        }
    }))

def test_get_status_breakdown_missing(tmp_path):
    setup_dummy_verb_types(tmp_path)
    res = status.get_status_breakdown(
        tmp_path, DUMMY_NOUN_SCHEMA, DUMMY_RAW_INPUTS, DUMMY_ADVERB_SCHEMA, project_path=tmp_path
    )
    assert res["raw_data"] == "Not Uploaded"
    assert res["data_entry"] == "Pending"
    assert res["interpretation"] == "Pending"


def test_render_status_bar():
    bar = status.render_status_bar({"a": "Complete", "b": "Pending"}, blocks_per_zone=2)
    assert "Progress:" in bar


def test_print_colored_status(capsys):
    status_ui.print_colored_status({"zone": "Pending"})
    out = capsys.readouterr().out
    assert "Zone" in out


def test_get_status_breakdown_raw_uploaded(tmp_path):
    setup_dummy_verb_types(tmp_path)
    pocket = tmp_path / "raw"
    pocket.mkdir()
    (pocket / "f.csv").write_text("a")
    res = status.get_status_breakdown(
        tmp_path, DUMMY_NOUN_SCHEMA, ["raw"], DUMMY_ADVERB_SCHEMA, project_path=tmp_path
    )
    assert res["raw_data"] == "Uploaded"


def test_get_status_breakdown_data_entry_complete(tmp_path):
    setup_dummy_verb_types(tmp_path)
    data = [{"id": "1"}]
    (tmp_path / "DataEntry.json").write_text(json.dumps(data))
    schema = {"fields": {"id": {"required": True}}, "primary_id_field": "id"}
    res = status.get_status_breakdown(
        tmp_path, schema, DUMMY_RAW_INPUTS, DUMMY_ADVERB_SCHEMA, project_path=tmp_path
    )
    assert res["data_entry"] == "Complete"


def test_get_status_breakdown_manual(tmp_path):
    setup_dummy_verb_types(tmp_path)
    (tmp_path / "Status.json").write_text(json.dumps({"interpretation": {"manual_approval": True}}))
    res = status.get_status_breakdown(
        tmp_path, DUMMY_NOUN_SCHEMA, DUMMY_RAW_INPUTS, DUMMY_ADVERB_SCHEMA, project_path=tmp_path
    )
    assert res["interpretation"] == "Manually Completed"


def test_get_status_breakdown_parsed(tmp_path):
    setup_dummy_verb_types(tmp_path)
    (tmp_path / "interpretation.csv").write_text("data")
    res = status.get_status_breakdown(
        tmp_path, DUMMY_NOUN_SCHEMA, DUMMY_RAW_INPUTS, DUMMY_ADVERB_SCHEMA,
        verb_key="DUMMY_VERB",
        project_path=tmp_path
    )
    assert res["interpretation"] == "Parsed"


def test_render_status_bar_full():
    bar = status.render_status_bar({"a": "Complete"})
    assert "100%" in bar


def test_print_colored_status_unknown(capsys):
    status_ui.print_colored_status({"zone": "Mystery"})
    assert "Mystery" in capsys.readouterr().out


def test_get_status_breakdown_writes_file(tmp_path):
    setup_dummy_verb_types(tmp_path)
    res = status.get_status_breakdown(
        tmp_path, DUMMY_NOUN_SCHEMA, DUMMY_RAW_INPUTS, DUMMY_ADVERB_SCHEMA, project_path=tmp_path
    )
    saved = json.loads((tmp_path / "Status.json").read_text())
    assert saved["breakdown"] == res


def test_render_status_bar_percent():
    bar = status.render_status_bar({"a": "Complete", "b": "Pending"})
    assert "50%" in bar


def test_data_entry_empty_without_schema(tmp_path):
    setup_dummy_verb_types(tmp_path)
    (tmp_path / "DataEntry.json").write_text("[]")
    res = status.get_status_breakdown(
        tmp_path, DUMMY_NOUN_SCHEMA, DUMMY_RAW_INPUTS, DUMMY_ADVERB_SCHEMA, project_path=tmp_path
    )
    assert res["data_entry"] == "Missing Required Fields"


def test_data_entry_invalid_json(tmp_path):
    setup_dummy_verb_types(tmp_path)
    (tmp_path / "DataEntry.json").write_text("{bad json")
    res = status.get_status_breakdown(
        tmp_path, DUMMY_NOUN_SCHEMA, DUMMY_RAW_INPUTS, DUMMY_ADVERB_SCHEMA, project_path=tmp_path
    )
    assert res["data_entry"] == "Missing Required Fields"


def test_data_entry_duplicate_ids(tmp_path):
    setup_dummy_verb_types(tmp_path)
    data = [{"id": "A"}, {"id": "A"}]
    (tmp_path / "DataEntry.json").write_text(json.dumps(data))
    schema = {"fields": {"id": {"required": True}}, "primary_id_field": "id"}
    res = status.get_status_breakdown(
        tmp_path, schema, DUMMY_RAW_INPUTS, DUMMY_ADVERB_SCHEMA, project_path=tmp_path
    )
    assert res["data_entry"] == "Missing Required Fields"


def test_data_entry_partial_missing_required(tmp_path):
    setup_dummy_verb_types(tmp_path)
    data = [{"id": "A"}, {"id": ""}]
    (tmp_path / "DataEntry.json").write_text(json.dumps(data))
    schema = {"fields": {"id": {"required": True}}, "primary_id_field": "id"}
    res = status.get_status_breakdown(
        tmp_path, schema, DUMMY_RAW_INPUTS, DUMMY_ADVERB_SCHEMA, project_path=tmp_path
    )
    assert res["data_entry"] == "Complete"


def test_status_json_invalid_doesnt_crash(tmp_path):
    setup_dummy_verb_types(tmp_path)
    (tmp_path / "Status.json").write_text("{invalid json")
    res = status.get_status_breakdown(
        tmp_path, DUMMY_NOUN_SCHEMA, DUMMY_RAW_INPUTS, DUMMY_ADVERB_SCHEMA, project_path=tmp_path
    )
    assert res["interpretation"] == "Pending"


def test_write_status_json_failure_logs(tmp_path):
    setup_dummy_verb_types(tmp_path)
    with patch("pathlib.Path.write_text", side_effect=OSError("Disk full")):
        breakdown = status.get_status_breakdown(
            tmp_path, DUMMY_NOUN_SCHEMA, DUMMY_RAW_INPUTS, DUMMY_ADVERB_SCHEMA, project_path=tmp_path
        )
        assert breakdown["data_entry"] == "Pending"  # still proceeds


def test_colored_status_handles_unknown_status(capsys):
    status_ui.print_colored_status({"odd_zone": "Glimmering"})
    out = capsys.readouterr().out
    assert "Glimmering" in out


def test_raw_data_missing_csv(tmp_path):
    setup_dummy_verb_types(tmp_path)
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "note.txt").write_text("no data here")
    res = status.get_status_breakdown(
        tmp_path, DUMMY_NOUN_SCHEMA, ["raw"], DUMMY_ADVERB_SCHEMA, project_path=tmp_path
    )
    assert res["raw_data"].startswith("Missing →")
