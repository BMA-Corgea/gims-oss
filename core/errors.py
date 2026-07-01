"""Central error contract: one ``AppError`` + one set of FastAPI exception handlers.

Historically the API spoke two error languages — ``raise HTTPException`` (→ ``{"detail": ...}``,
386 sites) and ad-hoc ``return {"error": ...}`` dicts (151 sites) — and unhandled exceptions
leaked a bare 500. This module renders every error into ONE envelope.

The envelope is intentionally **backward-compatible**: it carries the new structured fields
(``error_code``, ``details``) AND mirrors the legacy fields the current frontend reads
(``error``, ``message``, ``detail``). That lets us unify the *response* shape for all existing
``HTTPException`` sites centrally — without editing 386 call sites and without breaking the UI.
New code should ``raise AppError(...)``; legacy ``HTTPException`` raises keep working and are
normalised by the handler. (Migrating each raise/dict to ``AppError`` and dropping the legacy
mirrors is a later, frontend-coordinated step.)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from utils.logger import get_logger

log = get_logger(__name__)


class AppError(HTTPException):
    """A domain error with a stable code, HTTP status, message, and structured details.

    Subclasses FastAPI's ``HTTPException`` so it degrades gracefully: even in an app/test that did
    not call :func:`register_error_handlers`, the framework's built-in HTTPException
    handler still renders it as a proper HTTP response (``{"detail": message}`` at the
    right status) rather than a 500/unhandled error. When the handlers below ARE
    registered, the richer envelope (``error_code``/``details`` + compat mirrors) applies.
    Being an HTTPException also means existing ``except HTTPException`` blocks catch it.
    """

    def __init__(self, code: str, message: str, status: int = 400,
                 details: Optional[Dict[str, Any]] = None):
        super().__init__(status_code=status, detail=message)
        self.code = code
        self.message = message
        self.status = status
        self.details: Dict[str, Any] = details or {}


def error_body(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The single error envelope (new structured fields + legacy compat mirrors)."""
    return {
        # New structured contract:
        "error_code": code,
        "message": message,
        "details": details or {},
        # Backward-compat mirrors (current frontend reads .error / .message / .detail):
        "error": message,
        "detail": message,
    }


def register_error_handlers(app: FastAPI) -> None:
    """Install the one set of exception handlers on the app."""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status >= 500:
            log.error("AppError", exc.code, exc.message, {"path": request.url.path}, exc_info=True)
        else:
            log.debug("AppError", exc.code, exc.message, {"path": request.url.path})
        return JSONResponse(status_code=exc.status, content=error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        message = detail if isinstance(detail, str) else (
            detail.get("message") if isinstance(detail, dict) else str(detail)
        )
        body = error_body(f"HTTP_{exc.status_code}", message or "",
                          details=(detail if isinstance(detail, dict) else {}))
        body["detail"] = detail  # preserve the exact original detail (some clients read it)
        return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(body),
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        body = error_body("VALIDATION_ERROR", "Request validation failed",
                          details={"errors": exc.errors()})
        body["detail"] = exc.errors()  # FastAPI's default 422 shape preserved
        return JSONResponse(status_code=422, content=jsonable_encoder(body))

    # Domain exceptions → the contract. Imported lazily (handlers run after startup) to
    # avoid import cycles with the heavy modules that define them.
    try:
        from core.run_custom import RunError, ContextError  # noqa: F401

        @app.exception_handler(RunError)
        async def _handle_run_error(request: Request, exc: RunError) -> JSONResponse:
            code = "CONTEXT_ERROR" if exc.__class__.__name__ == "ContextError" else "RUN_ERROR"
            log.warning(code, str(exc), {"path": request.url.path})
            return JSONResponse(status_code=400, content=error_body(code, str(exc) or code))
    except Exception:  # pragma: no cover - defensive: never block startup over a handler
        log.debug("errors: RunError handler not registered", exc_info=True)

    try:
        from core.camera import CameraValidationError

        @app.exception_handler(CameraValidationError)
        async def _handle_camera(request: Request, exc: CameraValidationError) -> JSONResponse:
            return JSONResponse(status_code=400, content=error_body("CAMERA_VALIDATION", str(exc) or "Invalid image"))
    except Exception:  # pragma: no cover
        log.debug("errors: CameraValidationError handler not registered", exc_info=True)

    @app.exception_handler(Exception)
    async def _handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Clean, logged 500 instead of leaking a stack trace to the client.
        log.error("Unhandled exception", repr(exc), {"path": request.url.path}, exc_info=True)
        return JSONResponse(status_code=500, content=error_body("INTERNAL_ERROR", "Internal server error"))
