"""Phase 3.6: the generic /pos read surface (one shape for all word kinds)."""
import json

from fastapi.testclient import TestClient

from api.routers.pos import make_word_router
from fastapi import FastAPI


def _client(tmp_path, monkeypatch):
    # The router resolves a project NAME under projects_dir(); point that at our tmp dir.
    import api.routers.pos as wr
    monkeypatch.setattr(wr, "projects_dir", lambda: tmp_path)
    proj_dir = tmp_path / "P"
    proj_dir.mkdir()
    (proj_dir / "noun_types.json").write_text(json.dumps({
        "Sample": {"primary_id_field": "sample_id", "fields": {"sample_id": {"type": "string"}}},
        "Submission": {"primary_id_field": "submission_id", "fields": {}},
    }))
    # adjective in legacy LIST shape — normalized on read
    (proj_dir / "adjective_types.json").write_text(json.dumps([
        {"adjective": "status", "adjective_class": "Tag", "applies_to": ["Submission"]},
    ]))
    app = FastAPI()
    app.include_router(make_word_router())
    return TestClient(app), "P"


def test_list_nouns(tmp_path, monkeypatch):
    client, proj = _client(tmp_path, monkeypatch)
    r = client.get(f"/pos/noun/{proj}")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert set(body["types"]) == {"Sample", "Submission"}


def test_list_adjectives_normalizes_list_shape(tmp_path, monkeypatch):
    client, proj = _client(tmp_path, monkeypatch)
    r = client.get(f"/pos/adjective/{proj}")
    assert r.status_code == 200
    assert "status" in r.json()["types"]


def test_get_one_type(tmp_path, monkeypatch):
    client, proj = _client(tmp_path, monkeypatch)
    r = client.get(f"/pos/noun/{proj}/Sample")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Sample"
    assert body["definition"]["primary_id_field"] == "sample_id"
    assert isinstance(body["dependents"], list)


def test_unknown_name_404(tmp_path, monkeypatch):
    client, proj = _client(tmp_path, monkeypatch)
    r = client.get(f"/pos/noun/{proj}/Ghost")
    assert r.status_code == 404


def test_invalid_kind_422(tmp_path, monkeypatch):
    client, proj = _client(tmp_path, monkeypatch)
    # 'sandwich' is not a WordKind -> FastAPI Enum validation rejects it.
    assert client.get(f"/pos/sandwich/{proj}").status_code == 422
