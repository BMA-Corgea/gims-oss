"""Behaviour-golden harness for the adjective + adverb descriptor routers.

These two POS write-routers had NO endpoint-level tests (only the route-set baselines). Phase 3c
collapses their bodies onto a shared generic descriptor handler, keeping every route declaration
(path/method/order/name) byte-identical. This harness pins the OBSERVABLE behaviour of every
endpoint — status codes, response bodies, and on-disk cascade side effects — so the collapse can be
proven behaviour-neutral. It drives the real routers through a TestClient against an isolated,
uniquely-named temp project (created under the real projects dir, torn down after).
"""
from __future__ import annotations

import json
import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.manifest.resolver import resolve_path
from api.routers import adjective as adjective_module
from api.routers import adverb as adverb_module
from api import json_proxy

PROJECT = "ZZGoldenDescriptorTest"


@pytest.fixture()
def client(monkeypatch):
    # local-only mode (no S3) so i_o.* hits the temp project on disk
    monkeypatch.setattr(json_proxy, "_is_s3_path", lambda path: False)

    proj = resolve_path(__import__("pathlib").Path(), "project_root") / PROJECT
    if proj.exists():
        shutil.rmtree(proj)
    proj.mkdir(parents=True)

    (proj / "noun_types.json").write_text(json.dumps({
        "Sample": {
            "primary_id_field": "id",
            "fields": {
                "id": {"type": "string"},
                "color": {"type": "adjective", "adjective_class": "Tag"},
                "plain": {"type": "string", "required": True},
            },
        },
    }))
    (proj / "verb_types.json").write_text(json.dumps({
        "Plate": {
            "data_entry_schema": {},
            "adverb_schema": {"weather": {"adverb_class": "Attribute", "field_type": "string"}},
        },
    }))
    # descriptor type files in the legacy LIST shape (load_schema normalizes either shape)
    (proj / "adjective_types.json").write_text(json.dumps([
        {"adjective": "color", "applies_to": ["Sample"], "adjective_class": "Tag",
         "valid_options": [{"value": "red"}, {"value": "blue"}]},
    ]))
    (proj / "adverb_types.json").write_text(json.dumps([
        {"adverb": "weather", "verb": "Plate", "adverb_class": "Attribute", "field_type": "string"},
    ]))

    app = FastAPI()
    app.include_router(adjective_module.router)
    app.include_router(adverb_module.router)
    try:
        yield TestClient(app)
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def _types(client, kind):
    """Read a descriptor type file back through the running app's local i_o."""
    from api import i_o
    return i_o.load_schema(resolve_path(__import__("pathlib").Path(), "project_root") / PROJECT, kind)


# ── adjective reads ──────────────────────────────────────────────────────────────────────────
def test_adjective_classes_exact(client):
    r = client.get("/adjective/classes")
    assert r.status_code == 200
    assert r.json() == ["ActionRequirement", "Tag", "Reference", "ReferenceList", "Picture", "Duration"]


def test_adjective_projects_and_nouns(client):
    r = client.get("/adjective/projects")
    assert r.status_code == 200 and PROJECT in r.json()
    r = client.get(f"/adjective/nouns/{PROJECT}")
    assert r.status_code == 200 and "Sample" in r.json()


def test_adjective_list_and_configure(client):
    r = client.get(f"/adjective/list/{PROJECT}/Sample")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) and len(body) == 1 and body[0]["adjective"] == "color"

    r = client.get(f"/adjective/configure/{PROJECT}/Sample/color")
    assert r.status_code == 200 and r.json()["adjective"] == "color"

    r = client.get(f"/adjective/configure/{PROJECT}/Sample/missing")
    assert r.status_code == 404


def test_adjective_options_and_logic(client):
    assert client.get(f"/adjective/options/{PROJECT}/Sample/color").status_code == 200
    assert client.get(f"/adjective/logic/{PROJECT}/Sample/color").status_code == 200
    # instance route, non-ActionRequirement -> pass-through use_logic
    assert client.get(f"/adjective/logic/{PROJECT}/Sample/color/INST1").status_code == 200


# ── adjective writes (with cascade verification) ────────────────────────────────────────────────
def test_adjective_update_and_404(client):
    r = client.post(f"/adjective/update/{PROJECT}/Sample/color",
                    json={"adjective": "color", "applies_to": ["Sample"],
                          "adjective_class": "Tag", "valid_options": [{"value": "green"}]})
    assert r.status_code == 200 and r.json() == {"status": "updated"}
    entry = next(e for e in _types(client, "adjective") if e["adjective"] == "color")
    assert entry["valid_options"] == [{"value": "green"}]

    r = client.post(f"/adjective/update/{PROJECT}/Sample/missing", json={"adjective": "missing"})
    assert r.status_code == 404


def test_adjective_promote_marks_noun_field(client):
    r = client.post(f"/adjective/promote/{PROJECT}/Sample",
                    json={"adjective": "plain", "applies_to": ["Sample"], "adjective_class": "Tag"})
    assert r.status_code == 200 and r.json() == {"status": "promoted"}
    nouns = _types(client, "noun")
    fld = nouns["Sample"]["fields"]["plain"]
    assert fld["type"] == "adjective" and fld["adjective_class"] == "Tag"
    assert fld.get("required") is True  # preserved from the prior string field
    assert any(e["adjective"] == "plain" for e in _types(client, "adjective"))


def test_adjective_promote_primary_id_rejected(client):
    r = client.post(f"/adjective/promote/{PROJECT}/Sample",
                    json={"adjective": "id", "adjective_class": "Tag"})
    assert r.status_code == 400


def test_adjective_demote_downgrades_field(client):
    r = client.post(f"/adjective/demote/{PROJECT}/Sample/color")
    assert r.status_code == 200 and r.json() == {"status": "demoted"}
    fld = _types(client, "noun")["Sample"]["fields"]["color"]
    assert fld["type"] == "string" and "adjective_class" not in fld
    assert not any(e["adjective"] == "color" for e in _types(client, "adjective"))


# ── adverb reads ─────────────────────────────────────────────────────────────────────────────
def test_adverb_classes_exact(client):
    r = client.get("/adverb/classes")
    assert r.status_code == 200
    assert r.json() == ["Tag", "Reference", "ReferenceList", "Picture", "Attribute"]


def test_adverb_projects_nouns_and_verbs(client):
    assert PROJECT in client.get("/adverb/projects").json()
    assert "Sample" in client.get(f"/adverb/nouns/{PROJECT}").json()
    assert "Plate" in client.get(f"/adverb/list/{PROJECT}").json()


def test_adverb_list_and_configure(client):
    r = client.get(f"/adverb/list/{PROJECT}/Plate")
    assert r.status_code == 200 and "weather" in r.json()

    r = client.get(f"/adverb/configure/{PROJECT}/Plate/weather")
    assert r.status_code == 200 and r.json()["adverb_class"] == "Attribute"

    assert client.get(f"/adverb/configure/{PROJECT}/Plate/missing").status_code == 404


def test_adverb_options_and_logic(client):
    assert client.get(f"/adverb/options/{PROJECT}/Plate/weather").status_code == 200
    assert client.get(f"/adverb/logic/{PROJECT}/Plate/weather").status_code == 200


# ── adverb writes (with cascade verification) ────────────────────────────────────────────────────
def test_adverb_update_cascades_to_verb_schema(client):
    r = client.post(f"/adverb/update/{PROJECT}/Plate/weather",
                    json={"adverb": "weather", "adverb_class": "Attribute", "field_type": "number"})
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "updated" and j["renamed"] is False
    # cascade into verb_types[Plate].adverb_schema
    verbs = _types(client, "verb")
    assert verbs["Plate"]["adverb_schema"]["weather"]["field_type"] == "number"

    assert client.post(f"/adverb/update/{PROJECT}/Plate/missing",
                       json={"adverb": "missing"}).status_code == 404


def test_adverb_update_rename(client):
    r = client.post(f"/adverb/update/{PROJECT}/Plate/weather",
                    json={"adverb": "climate", "adverb_class": "Attribute", "field_type": "string"})
    assert r.status_code == 200
    j = r.json()
    assert j["renamed"] is True and j["old_adverb"] == "weather" and j["new_adverb"] == "climate"
    adv_schema = _types(client, "verb")["Plate"]["adverb_schema"]
    assert "climate" in adv_schema and "weather" not in adv_schema


def test_adverb_promote_and_demote(client):
    r = client.post(f"/adverb/promote/{PROJECT}/Plate",
                    json={"adverb": "humidity", "verb": "Plate", "adverb_class": "Attribute",
                          "field_type": "number"})
    assert r.status_code == 200 and r.json() == {"status": "promoted"}
    assert "humidity" in _types(client, "verb")["Plate"]["adverb_schema"]
    assert any(e["adverb"] == "humidity" for e in _types(client, "adverb"))

    r = client.post(f"/adverb/demote/{PROJECT}/Plate/weather")
    assert r.status_code == 200 and r.json() == {"status": "demoted"}
    assert "weather" not in _types(client, "verb")["Plate"]["adverb_schema"]
    assert not any(e["adverb"] == "weather" for e in _types(client, "adverb"))
