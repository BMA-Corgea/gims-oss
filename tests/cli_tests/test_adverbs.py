import json
from pathlib import Path
from utils.handlers.adverb import (
    TagAdverb,
    ReferenceAdverb,
    AttributeAdverb,
    load_adverb_handler,
    BaseAdverb,
)
from utils.status import get_status_breakdown


def test_tagadverb_prompt(monkeypatch):
    adv = TagAdverb("project", "purpose", {"valid_options": [{"value": "A"}, {"value": "B"}]})
    monkeypatch.setattr("utils.handlers.adverb.indexed_choice", lambda opts, msg: 1)
    val = adv.prompt_for_value(Path("."))
    assert val == "B"
    assert adv.validate(val, Path("."))


def test_referenceadverb_validate(tmp_path):
    proj = tmp_path
    items = proj / "nouns" / "Instrument"
    items.mkdir(parents=True)
    (items / "items.jsonl").write_text(json.dumps({"id": "HPLC-01"}) + "\n")
    adv = ReferenceAdverb("instrument", {"reference_noun": "Instrument"})
    assert adv.validate("HPLC-01", proj)
    assert not adv.validate("BAD", proj)


def test_adverbs_json_roundtrip(tmp_path):
    adv_path = tmp_path / "adverbs.json"
    data = {"purpose": "Client"}
    adv_path.write_text(json.dumps(data))
    loaded = json.load(open(adv_path))
    assert loaded == data


def test_status_adverbs_required(tmp_path):
    run = tmp_path
    run.mkdir(parents=True, exist_ok=True)
    schema = {"note": {"adverb_class": "Attribute", "required": True}}

    # Dummy noun schema and raw_inputs for the updated function
    dummy_noun_schema = {}
    dummy_raw_inputs = []

    # Create a dummy verb_types.json file to satisfy project_path usage
    (tmp_path / "verb_types.json").write_text(json.dumps({}))

    # Without file should be pending
    res = get_status_breakdown(
        run,
        noun_schema=dummy_noun_schema,
        raw_inputs=dummy_raw_inputs,
        adverb_schema=schema,
        project_path=tmp_path
    )
    assert res["adverb_info"] == "Pending"

    # With file and value -> complete
    (run / "adverbs.json").write_text(json.dumps({"note": "x"}))
    res = get_status_breakdown(
        run,
        noun_schema=dummy_noun_schema,
        raw_inputs=dummy_raw_inputs,
        adverb_schema=schema,
        project_path=tmp_path
    )
    assert res["adverb_info"] == "Complete"


def test_handle_adverb_zone_writes(tmp_path, monkeypatch):
    from utils.data_dump import handle_adverb_zone

    class Dummy(AttributeAdverb):
        def prompt_for_value(self, project_path):
            return "val"

    monkeypatch.setattr(
        "utils.data_dump.load_adverb_handler",
        lambda p, v, a: Dummy(a, {})
    )
    choices = iter([0, None])
    monkeypatch.setattr(
        "utils.data_dump.indexed_choice",
        lambda opts, msg: next(choices)
    )
    run = tmp_path / "run"
    run.mkdir()
    schema = {"note": {"adverb_class": "Attribute"}}
    handle_adverb_zone(tmp_path, run, "Verb", schema)
    data = json.loads((run / "adverbs.json").read_text())
    assert data["note"] == "val"

def test_reference_adverb_configure(monkeypatch, tmp_path):
    proj = "P"
    proj_path = tmp_path / "projects" / proj
    noun_file = proj_path / "noun_types.json"
    noun_file.parent.mkdir(parents=True, exist_ok=True)
    noun_file.write_text(json.dumps({"Sample": {"fields": {}}}))

    monkeypatch.setattr("utils.handlers.adverb.indexed_choice", lambda opts, msg: 0)
    monkeypatch.setattr("utils.handlers.adverb.BaseAdverb.prompt_filters", lambda self, n: {"x": "1"})
    monkeypatch.setattr("utils.handlers.adverb.Path", lambda *p: tmp_path.joinpath(*p))

    ra = ReferenceAdverb(proj, "sample")
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    ra.interactive_configure()
    assert ra.config["reference_noun"] == "Sample"
    assert ra.config["filters"] == {"x": "1"}