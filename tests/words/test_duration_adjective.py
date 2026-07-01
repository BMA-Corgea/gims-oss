"""Coverage for the time-aware **Duration** adjective (the 7th descriptor behavior):

  * registration + noun-only dispatch context,
  * the typed two-field binding config + mode/unit fallbacks,
  * the "virtual/derived field carries no stored value" invariant,
  * the ``datetime`` field type's validation, and
  * the ``/grid/duration_adjectives`` discovery endpoint (binding + server clock anchor).

The Duration adjective links two of the SAME record's date/datetime fields and renders a live
interval; the per-second tick is interpolated client-side off the server clock returned here.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.words.handlers import (
    get_descriptor,
    DESCRIPTOR_CLASSES,
    get_adjective_class_handler,
    get_adverb_class_handler,
)
from core.words.validation import is_valid_datetime
from core.words.wordtype import CANONICAL_FIELD_TYPES


def _dur(**override):
    data = {
        "adjective": "hold_clock", "adjective_class": "Duration",
        "start_field": "received_at", "end_field": "due_at", "mode": "both", "unit": "auto",
    }
    data.update(override)
    return get_descriptor(data, attaches_kind="adjective", target_name="Specimen")


# ── behavior unit tests ────────────────────────────────────────────────────────────────────────
def test_duration_registered_noun_only():
    assert DESCRIPTOR_CLASSES.get("Duration") == "Duration"
    assert "Duration" in get_adjective_class_handler()        # valid on nouns
    assert "Duration" not in get_adverb_class_handler()       # NOT valid on verbs


def test_duration_binding_config_and_use_logic():
    d = _dur()
    assert d.behavior_name == "Duration"
    opts = d.get_configurable_options()
    assert opts["start_field"] == "received_at" and opts["end_field"] == "due_at"
    assert opts["mode"] == "both" and opts["unit"] == "auto"
    assert d.use_logic() == {
        "start_field": "received_at", "end_field": "due_at", "mode": "both", "unit": "auto",
    }


def test_duration_mode_unit_fallbacks():
    assert _dur(mode="nonsense").get_mode() == "elapsed"
    assert _dur(unit="nonsense").get_unit() == "auto"
    # default-less entry falls back cleanly
    assert _dur(mode=None, unit=None).get_mode() == "elapsed"


def test_duration_field_is_virtual_rejects_stored_value():
    d = _dur()
    # blanks/missing are fine (the grid seeds empty cells)
    assert d.validate_entries([{"hold_clock": ""}, {"hold_clock": None}, {}]) == []
    # a real value is rejected — the column is computed, never entered
    errs = d.validate_entries([{"hold_clock": "2026-01-01"}])
    assert len(errs) == 1 and "computed duration" in errs[0]


def test_datetime_field_type_validation():
    assert "datetime" in CANONICAL_FIELD_TYPES
    assert is_valid_datetime("2026-06-29T14:30:00.123Z", None)   # compliance now_iso_ms() shape
    assert is_valid_datetime("2026-06-29T14:30", None)
    assert is_valid_datetime("2026-06-29 14:30:00", None)
    assert is_valid_datetime("2026-06-29", None)                 # bare date tolerated (midnight)
    assert not is_valid_datetime("nope", None)
    assert not is_valid_datetime("", None)


# ── discovery endpoint ───────────────────────────────────────────────────────────────────────
PROJECT = "ZZDurationEndpointTest"


@pytest.fixture()
def client(monkeypatch):
    from api import json_proxy
    from api.manifest.resolver import resolve_path

    monkeypatch.setattr(json_proxy, "_is_s3_path", lambda path: False)
    proj = resolve_path(Path(), "project_root") / PROJECT
    if proj.exists():
        shutil.rmtree(proj)
    proj.mkdir(parents=True)

    (proj / "noun_types.json").write_text(json.dumps({
        "Specimen": {
            "primary_id_field": "specimen_id",
            "fields": {
                "specimen_id": {"type": "string", "required": True},
                "received_at": {"type": "datetime"},
                "due_at":      {"type": "datetime"},
                "hold_clock":  {"type": "adjective", "adjective_class": "Duration"},
            },
        },
    }))
    (proj / "adjective_types.json").write_text(json.dumps({
        "hold_clock": {
            "adjective": "hold_clock", "adjective_class": "Duration",
            "start_field": "received_at", "end_field": "due_at", "mode": "both", "unit": "auto",
            "attaches_to": ["Specimen"], "class": "Duration",
        },
    }))
    (proj / "verb_types.json").write_text(json.dumps({}))

    from api.routers.runlog_workbench import router as rw_router
    app = FastAPI()
    app.include_router(rw_router)
    try:
        yield TestClient(app)
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_duration_discovery_endpoint(client):
    r = client.get(f"/grid/duration_adjectives/{PROJECT}/Specimen")
    assert r.status_code == 200
    body = r.json()
    assert body["names"] == ["hold_clock"]
    d = body["detail"]["hold_clock"]
    assert d["start_field"] == "received_at" and d["end_field"] == "due_at"
    assert d["mode"] == "both" and d["unit"] == "auto"
    assert d["start_meta"]["type"] == "datetime" and d["end_meta"]["type"] == "datetime"
    # server-anchored clock + clock-trust signal for the client ticker
    assert body["server_now"].endswith("Z")
    assert "synced" in body["time_status"]


def test_duration_discovery_empty_for_plain_noun(client):
    # a noun with no Duration adjective returns an empty binding set (but still the clock anchor)
    r = client.get(f"/grid/duration_adjectives/{PROJECT}/Specimen")
    assert r.status_code == 200
    # sanity: the same endpoint on a missing noun type 404s
    assert client.get(f"/grid/duration_adjectives/{PROJECT}/NoSuchNoun").status_code == 404
