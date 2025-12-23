# nodes/auth_guard_node.py
# -----------------------------------------------------------------------------
# Auth Guard (Modules) — page-level authorization gate for orchestrated fetches
#
# New reality (global logins.db):
#   • We call /login/{ROLES_CANONICAL_PROJECT}/auth/me and forward auth headers.
#   • We DO NOT trust client X-Feature-Tags / X-Roles. We only trust server
#     feature_tags returned by /auth/me.
#   • We enforce membership: unless user is superuser, deny if the user isn't
#     mapped to the ROLES_CANONICAL_PROJECT in accounts_projects (reflected as
#     the project name in me.projects).
#   • Module allow/deny is decided by canonical "module:*" tags coming from
#     /auth/me for that roles host project.
# -----------------------------------------------------------------------------

from __future__ import annotations

from typing import Any, Dict, Optional, Set, List
from pathlib import Path
import os
import re
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import JSONResponse
import httpx

from core.orchestration.node import Node, NodeKind

# Project helpers
from api.manifest.resolver import resolve_path
from api.i_o import load_data

# ──────────────────────────────────────────────────────────────────────────────
# Debug control
# ──────────────────────────────────────────────────────────────────────────────
DEBUG_ENABLED = False

def debug(*args, **kwargs):
    if DEBUG_ENABLED:
        print("[auth-guard]", *args, **kwargs, flush=True)

def _srepr(obj: Any, maxlen: int = 600) -> str:
    try:
        s = repr(obj)
    except Exception:
        s = f"<unrepr:{type(obj).__name__}>"
    return s if len(s) <= maxlen else s[:maxlen] + "…"

debug("init: module import")

router = APIRouter()

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
DEFAULT_ROLES_PROJECT = os.environ.get("ROLES_CANONICAL_PROJECT", "LIMS-System")
debug("config: DEFAULT_ROLES_PROJECT =", DEFAULT_ROLES_PROJECT)

PASS_PATH_PREFIXES = ("/orchestrate", "/login", "/static", "/schema", "/events", "/stream", "/api")
debug("config: PASS_PATH_PREFIXES =", PASS_PATH_PREFIXES)

# ──────────────────────────────────────────────────────────────────────────────
# Normalization helpers
# ──────────────────────────────────────────────────────────────────────────────
_ALPHA = re.compile(r"[^A-Za-z]+")

def norm_key(s: str) -> str:
    if not s:
        return ""
    s0 = s
    s = s.strip().lower()
    s = _ALPHA.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    debug("norm_key:", _srepr(s0), "→", _srepr(s))
    return s

# ──────────────────────────────────────────────────────────────────────────────
# Roles / modules discovery (from roles_schema in ROLES project)
# ──────────────────────────────────────────────────────────────────────────────
def _roles_path_for_project(project: str) -> Optional[Path]:
    try:
        proj_root = resolve_path(Path(), "project_root")
        proj_path = Path(proj_root) / project
        roles_path = resolve_path(proj_path, "roles_schema")
        return roles_path
    except Exception as e:
        debug("_roles_path_for_project: resolve failed:", repr(e))
        return None

def _load_roles_index(project: Optional[str]) -> Dict[str, dict]:
    if not project:
        return {}
    rp = _roles_path_for_project(project)
    if not rp or not rp.exists():
        debug("_load_roles_index: roles.json missing at", rp)
        return {}
    try:
        data = load_data(rp) or {}
    except Exception as e:
        debug("_load_roles_index: load error:", repr(e))
        return {}
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, list):
        return {}
    idx: Dict[str, dict] = {}
    for entry in roles:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            idx[entry["name"].strip().lower()] = entry
    debug("_load_roles_index: index size =", len(idx))
    return idx

@lru_cache(maxsize=16)
def _module_key_index(project: str) -> Dict[str, str]:
    """
    Build alias→canonical map for module tags:
      - canonical: norm_key('backup-manager') → 'backup_manager'
      - alias:    first token before '_' → 'backup'
    Allows '/backup' to map to 'module:backup-manager' etc.
    """
    idx = _load_roles_index(project)
    alias_to_canon: Dict[str, str] = {}
    for _role_name, role in idx.items():
        tags = role.get("feature_tags") or []
        for tag in tags:
            if not isinstance(tag, str):
                continue
            if tag.lower().startswith("module:"):
                mod = tag.split(":", 1)[1].strip()
                if not mod:
                    continue
                canon = norm_key(mod)              # e.g. 'backup_manager'
                alias_to_canon[canon] = canon      # self alias
                head = canon.split("_", 1)[0]      # e.g. 'backup'
                alias_to_canon.setdefault(head, canon)
    debug("_module_key_index: alias count =", len(alias_to_canon))
    return alias_to_canon

@lru_cache(maxsize=16)
def _all_module_keys(project: str) -> Set[str]:
    canons = {v for v in _module_key_index(project).values()}
    debug("_all_module_keys: discovered =", len(canons))
    return canons

# ──────────────────────────────────────────────────────────────────────────────
# Request parsing
# ──────────────────────────────────────────────────────────────────────────────
def _first_segment(path: str) -> str:
    if not path:
        return ""
    path_no_q = path.split("?", 1)[0]
    segs = [s for s in path_no_q.split("/") if s]
    return segs[0] if segs else ""

def _is_skipped(path: str) -> bool:
    return any(path.startswith(p) for p in PASS_PATH_PREFIXES)

# ──────────────────────────────────────────────────────────────────────────────
# Tag helpers
# ──────────────────────────────────────────────────────────────────────────────
def _parse_feature_tags_list(tags: Optional[List[str]]) -> Set[str]:
    """Normalize a list of feature tag strings; keep modules as normalized keys."""
    out: Set[str] = set()
    if not tags:
        return out
    for raw in tags:
        if not isinstance(raw, str):
            continue
        lo = raw.strip().lower()
        if lo.startswith("module:"):
            tag = raw.split(":", 1)[1].strip()
            if tag:
                out.add(norm_key(tag))
        else:
            out.add(norm_key(raw))
    return out

# ──────────────────────────────────────────────────────────────────────────────
# Authentication via /auth/me
# ──────────────────────────────────────────────────────────────────────────────
async def _fetch_me_for_roles_project(request: Request, roles_project: str) -> Optional[dict]:
    """
    Call /login/{roles_project}/auth/me forwarding Authorization and cookies.
    We intentionally do NOT allow callers to override the project — the tags
    must be evaluated for the roles host project we guard.
    """
    base = f"{request.url.scheme}://{request.url.netloc}"
    url = f"{base}/login/{roles_project}/auth/me"

    headers: Dict[str, str] = {}
    for h in ("authorization", "x-forwarded-authorization", "cookie"):
        v = request.headers.get(h)
        if v:
            headers[h] = v

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            r = await client.get(url, headers=headers)
            debug(f"_fetch_me_for_roles_project: {url} → {r.status_code}")
            if r.status_code != 200:
                return None
            return r.json()
    except Exception as e:
        debug("_fetch_me_for_roles_project: error:", repr(e))
        return None

# ──────────────────────────────────────────────────────────────────────────────
# PRE hook — allow/deny module pages
# ──────────────────────────────────────────────────────────────────────────────
@router.post("/orchestrate/rules/pre")
async def rules_pre(request: Request):
    try:
        envelope = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON envelope")

    method = str(envelope.get("method", "GET")).upper()
    path   = str(envelope.get("path", "/"))

    debug("rules_pre: method =", method, "path =", path)

    if method != "GET":
        return JSONResponse({"effect": "allow"})

    if _is_skipped(path):
        debug("rules_pre: skipped prefix → allow")
        return JSONResponse({"effect": "allow"})

    seg_raw = _first_segment(path)
    seg_key = norm_key(seg_raw)
    roles_host_project = DEFAULT_ROLES_PROJECT

    alias_map = _module_key_index(roles_host_project)  # alias->canonical
    canonical_for_path = alias_map.get(seg_key)

    if not canonical_for_path:
        # Not a module page based on our roles schema → allow
        debug("rules_pre: not a module page (no alias match) → allow")
        return JSONResponse({"effect": "allow"})

    # Authenticate and evaluate RBAC against the ROLES project
    me = await _fetch_me_for_roles_project(request, roles_host_project)
    if not me:
        # STRICT: deny when not authenticated or /auth/me fails
        debug("rules_pre: DENY — /auth/me unavailable or unauthorized")
        return JSONResponse(
            {"effect": "deny", "reason": "Not signed in: authentication required."},
            status_code=401,
        )

    user = (me.get("user") or {}) if isinstance(me, dict) else {}
    is_super = bool(user.get("is_superuser"))
    projects = me.get("projects") or []  # human names from logins.db
    feature_tags = me.get("feature_tags") or []

    # Enforce membership: user must be mapped to the roles host project,
    # unless superuser (which bypasses).
    if not is_super and roles_host_project not in projects:
        debug("rules_pre: DENY — user not a member of roles project:", roles_host_project)
        return JSONResponse(
            {"effect": "deny", "reason": f"Access denied: no membership in project '{roles_host_project}'."},
            status_code=403,
        )

    # Authorize using server-trusted tags from /auth/me
    grants_norm = _parse_feature_tags_list(feature_tags)

    # Canonicalize grants via alias map (map aliases to canonicals)
    effective_canon: Set[str] = set(alias_map.get(g, g) for g in grants_norm)

    if is_super or (canonical_for_path in effective_canon):
        debug("rules_pre: ALLOW module_key =", canonical_for_path, "is_super =", is_super)
        return JSONResponse({"effect": "allow"})

    roles_readable = ", ".join(me.get("roles") or [])
    reason = (
        f"Access denied: missing feature tag for module '{seg_raw}' "
        f"(normalized '{seg_key}', canonical '{canonical_for_path}'). "
        f"Your roles: {roles_readable or '(none)'}."
    )
    debug("rules_pre: DENY module_key =", canonical_for_path, "reason =", reason)
    return JSONResponse({"effect": "deny", "reason": reason}, status_code=403)

# -----------------------------------------------------------------------------
# Export node
# -----------------------------------------------------------------------------
auth_guard_node = Node(
    name="Rules: Auth Guard (Modules)",
    kind=NodeKind.RULES,
    router=router,
    route_prefix="",
    meta={"enforces": ["module-page"], "debug": True},
)

debug("export: auth_guard_node mounted")
