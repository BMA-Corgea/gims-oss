# OPEN-CORE STUB — auto-written by tools/oss_export. The GIMS public build ships without
# the login/auth, roles, and 21 CFR Part 11 compliance layers; this file preserves the
# import surface the rest of the app needs, implemented as an OPEN single-user no-op.
# Do NOT deploy the open-core build as a shared/multi-user service.
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

from core.orchestration.node import Node, NodeKind

# The single fully-authorized local identity for the open build.
_OPEN_ME = {
    "user": {"id": "local", "email": "local@localhost", "is_superuser": True,
             "is_verified": True, "is_active": True},
    "roles": ["owner"],
    "scopes": ["*"],
    "feature_tags": ["module:*", "noun:*", "verb:*", "signoff:*"],
    "projects": [],
    "project_codes": [],
}

_INJECT_JS = '(function(){\n  if (!window.GIMS) window.GIMS = {};\n  var G = window.GIMS;\n  // Open-core single-user build: no login. Present one fully-authorized local user so the\n  // UI renders every feature and never prompts to sign in.\n  var ME = {\n    user: { id: "local", email: "local@localhost", is_superuser: true, is_verified: true, is_active: true },\n    roles: ["owner"], scopes: ["*"],\n    feature_tags: ["module:*","noun:*","verb:*","signoff:*"],\n    projects: [], project_codes: []\n  };\n  if (!G.authReady) { G.authReady = Promise.resolve(); }\n  G.csrfToken = null;\n  G.getCsrfHeaders = function(){ return {}; };\n  G.__authState = { authed: true, me: ME };\n  G.__applyAuthMe = function(){ /* open build: already authed */ };\n  G.authRefresh = async function(){ return; };\n  G.logout = async function(){ return; };\n  try { localStorage.setItem("gims_feature_tags", JSON.stringify(ME.feature_tags)); } catch (e) {}\n  function apply(){\n    try { document.body.classList.add("is-authed"); document.body.classList.remove("is-anon"); } catch (e) {}\n    try { document.dispatchEvent(new CustomEvent("gims:authapplied", { detail: { authed: true, me: ME } })); } catch (e) {}\n  }\n  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);\n  else apply();\n})();\n'

router = APIRouter(prefix="/login", tags=["Login (open-core stub)"])


@router.get("/csrf")
async def _csrf():
    return JSONResponse({"csrf": "local"})


# Global /login/auth/me is registered BEFORE the project-scoped route so it wins the match.
@router.get("/auth/me")
async def _me_global():
    return JSONResponse(_OPEN_ME)


@router.get("/{project}/auth/me")
async def _me_project(project: str):
    me = dict(_OPEN_ME)
    me["projects"] = [project]
    return JSONResponse(me)


@router.get("/inject.js")
async def _inject_js():
    return PlainTextResponse(_INJECT_JS, media_type="application/javascript")


@router.get("/state-tab.js")
async def _state_tab_js():
    return PlainTextResponse("/* open-core: login state tab disabled */",
                             media_type="application/javascript")


# ── Open no-op authorization helpers (kept for any importer that still references them) ──
def require_scopes(*_a, **_k):
    async def _dep():
        return None
    return _dep


def require_login(*_a, **_k):
    async def _dep():
        return None
    return _dep


def require_feature_tags(*_a, **_k):
    async def _dep():
        return None
    return _dep


async def initialize_login_system():
    return None


login_node = Node(
    name="Login (FastAPI Users)",
    kind=NodeKind.LOGIN,
    router=router,
    meta={
        "entry_path": "/login/state-tab.js",
        "provides_inject": ["/login/state-tab.js", "/login/inject.js"],
        "icon": "\U0001f513",
        "label": "Login",
    },
)
