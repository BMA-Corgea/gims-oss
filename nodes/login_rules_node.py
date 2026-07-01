# OPEN-CORE STUB — auto-written by tools/oss_export. The GIMS public build ships without
# the login/auth, roles, and 21 CFR Part 11 compliance layers; this file preserves the
# import surface the rest of the app needs, implemented as an OPEN single-user no-op.
# Do NOT deploy the open-core build as a shared/multi-user service.
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.orchestration.node import Node, NodeKind

router = APIRouter()


@router.post("/orchestrate/rules/pre")
async def _rules_pre(request: Request):
    try:
        await request.json()
    except Exception:
        pass
    return JSONResponse({"effect": "allow"})


@router.post("/login-rules/orchestrate/rules/pre")
async def _rules_pre_alias(request: Request):
    try:
        await request.json()
    except Exception:
        pass
    return JSONResponse({"effect": "allow"})


@router.post("/orchestrate/rules/post")
async def _rules_post(request: Request):
    try:
        envelope = await request.json()
    except Exception:
        envelope = {}
    # Pass the response body through untouched (no role-based filtering in the open build).
    return JSONResponse({"body": envelope.get("body")})


async def require_gate_signoff():
    """Open build: gate sign-off carries no permission check — gates complete freely."""
    return None


login_rules_node = Node(
    name="Rules: Feature Tags (Nouns, Verb Groups, Gates, Projects)",
    kind=NodeKind.RULES,
    router=router,
    route_prefix="",
    meta={"enforces": [], "open_core": True},
)
