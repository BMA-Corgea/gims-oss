"""Phase 2: the central error contract renders one envelope for every error path."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from core.errors import AppError, register_error_handlers


@pytest.fixture()
def client():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/app-error")
    def _app_error():
        raise AppError("NOUN_ITEM_NOT_FOUND", "Noun item not found", status=404,
                       details={"item_id": "S-1", "project": "LIMS-System"})

    @app.get("/http-error")
    def _http_error():
        raise HTTPException(status_code=403, detail="Missing permission")

    @app.get("/boom")
    def _boom():
        raise RuntimeError("unexpected kaboom")

    return TestClient(app, raise_server_exceptions=False)


def test_app_error_envelope(client):
    r = client.get("/app-error")
    assert r.status_code == 404
    body = r.json()
    assert body["error_code"] == "NOUN_ITEM_NOT_FOUND"
    assert body["message"] == "Noun item not found"
    assert body["details"] == {"item_id": "S-1", "project": "LIMS-System"}
    # backward-compat mirrors the current frontend reads
    assert body["error"] == "Noun item not found"
    assert body["detail"] == "Noun item not found"


def test_http_exception_is_normalized_but_keeps_detail(client):
    r = client.get("/http-error")
    assert r.status_code == 403
    body = r.json()
    assert body["error_code"] == "HTTP_403"
    assert body["message"] == "Missing permission"
    assert body["detail"] == "Missing permission"   # legacy .detail preserved
    assert body["error"] == "Missing permission"


def test_unhandled_exception_is_clean_500_not_a_stack_trace(client):
    r = client.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert body["message"] == "Internal server error"
    # the raw exception text must NOT leak to the client
    assert "kaboom" not in r.text


def test_app_error_subclasses_httpexception_for_graceful_degradation():
    """AppError must be an HTTPException so it renders correctly even where the custom
    handlers aren't registered (degrade-gracefully), while still carrying the rich fields."""
    from fastapi import HTTPException

    e = AppError("TEAPOT", "I am a teapot", status=418, details={"a": 1})
    assert isinstance(e, HTTPException)
    assert e.status_code == 418 and e.detail == "I am a teapot"   # built-in-handler fields
    assert e.code == "TEAPOT" and e.status == 418 and e.details == {"a": 1}  # rich fields
