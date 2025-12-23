# login_rules_node.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, Set, List
from pathlib import Path
import re
from urllib.parse import unquote
import os
import json
import base64
import sqlite3
import contextlib

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import JSONResponse

from core.orchestration.node import Node, NodeKind

# Project helpers
from api.manifest.resolver import resolve_path, get_db_uri
from api.i_o import (
    load_schema,  # kept available; not used here directly
    resolve_verb_group_from_test_type,
    resolve_run_id_to_test_type,
    load_data,  # used to read roles.json
)

# -----------------------------
# Debug controls
# -----------------------------
DEBUG_ENABLED = False
def debug(*args, **kwargs):
    if DEBUG_ENABLED:
        print("[login_rules_node]", *args, **kwargs)

router = APIRouter()

# Try Postgres client
try:
    import psycopg  # psycopg v3
    _PSYCOPG_AVAILABLE = True
except Exception as _e:
    _PSYCOPG_AVAILABLE = False
    debug("psycopg not available:", repr(_e))

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_ROLES_PROJECT = os.environ.get("ROLES_CANONICAL_PROJECT", "LIMS-System")
COOKIE_NAME = os.environ.get("GIMS_COOKIE_NAME", "gims_session")

# ──────────────────────────────────────────────────────────────────────────────
# Helpers: normalization
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_path(p: str) -> str:
    s = str(p or "")
    q = s.find("?")
    return s if q == -1 else s[:q]

# ──────────────────────────────────────────────────────────────────────────────
# DB resolver + open helpers (RDS-first, SQLite fallback)
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_for_psycopg(url: str) -> str:
    """
    Convert SQLAlchemy/asyncpg URLs & params to psycopg-compatible:
      - 'postgresql+asyncpg://' -> 'postgresql://'
      - '?ssl=require'          -> '?sslmode=require'
    Also guards against accidental 'postgresql://asyncpg://' concatenation.
    """
    if not isinstance(url, str):
        return url
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("postgresql+", 1)[1]
    # If resolver emitted asyncpg prefix twice
    url = url.replace("postgresql://asyncpg://", "postgresql://")
    # psycopg expects sslmode
    if "?ssl=require" in url:
        url = url.replace("?ssl=require", "?sslmode=require")
    return url

def _effective_logins_dsn() -> Optional[str]:
    """
    Ask the resolver for the 'logins_db' connection string (RDS/PG URI).
    """
    try:
        url = get_db_uri("logins_db")
        if url:
            debug("_effective_logins_dsn: resolver returned:", url)
        return url
    except Exception as e:
        debug("_effective_logins_dsn: resolver failed:", repr(e))
        return None

def _logins_db_path() -> Path:
    # Resolve via resolver/local_layout_map for SQLite fallback
    p = resolve_path(Path(), "logins_db")
    debug("_logins_db_path:", p.as_posix())
    return p

class _DBHandle:
    def __init__(self, kind: str, conn):
        self.kind = kind  # "pg" or "sqlite"
        self.conn = conn
    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

@contextlib.contextmanager
def _open_db() -> _DBHandle:
    """
    Open a DB connection:
      - Prefer psycopg to Postgres using resolver DSN.
      - Fallback to SQLite using resolver path.
    """
    dsn = _effective_logins_dsn()
    if dsn and dsn.startswith("postgresql") and _PSYCOPG_AVAILABLE:
        target = _normalize_for_psycopg(dsn)
        debug("_open_db: connecting to PostgreSQL:", target)
        conn = psycopg.connect(target, autocommit=False)
        try:
            yield _DBHandle("pg", conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    # Fallback to SQLite
    db_path = _logins_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    debug("_open_db: connecting to SQLite at", db_path.as_posix())
    conn = sqlite3.connect(db_path.as_posix())
    try:
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.row_factory = sqlite3.Row
        yield _DBHandle("sqlite", conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# ──────────────────────────────────────────────────────────────────────────────
# Utilities: JWT → user_id, Tags, Projects, Extraction
# ──────────────────────────────────────────────────────────────────────────────

def _b64url_decode(s: str) -> bytes:
    # add required padding for base64url
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("utf-8"))

def _bearer_from_headers(request: Request) -> Optional[str]:
    for name in ("authorization", "x-forwarded-authorization"):
        v = request.headers.get(name)
        if not v:
            continue
        v = v.strip()
        if v.lower().startswith("bearer "):
            return v.split(" ", 1)[1]
        return v
    return None

def _extract_user_id_from_token(tok: Optional[str]) -> Optional[str]:
    """
    Parse JWT payload WITHOUT verifying the signature (read-only identity hint).
    Expected keys: 'sub' or 'user_id'. Returns None if anything looks off.
    """
    if not tok:
        return None
    parts = tok.split(".")
    if len(parts) != 3:
        return None
    try:
        payload_raw = _b64url_decode(parts[1]).decode("utf-8")
        payload = json.loads(payload_raw)
        sub = payload.get("sub") or payload.get("user_id")
        return str(sub) if sub else None
    except Exception:
        return None

def _allowed_projects_for_user(user_id: Optional[str]) -> Set[str]:
    """
    Look up the set of project NAMES the user belongs to by joining:
      accounts_projects.user_id -> projects.id (via accounts_projects.project_id)
    Fail-closed: returns an EMPTY SET on any problem (no user_id, DB error).
    """
    if not user_id:
        return set()

    sql_pg = """
        SELECT p.name
        FROM accounts_projects ap
        JOIN projects p ON p.id = ap.project_id
        WHERE ap.user_id = %s
    """
    sql_sqlite = """
        SELECT p.name
        FROM accounts_projects ap
        JOIN projects p ON p.id = ap.project_id
        WHERE ap.user_id = ?
    """

    try:
        with _open_db() as db:
            if db.kind == "pg":
                with db.conn.cursor() as cur:
                    cur.execute(sql_pg, (user_id,))
                    rows = cur.fetchall() or []
                    names = {str(r[0]).strip() for r in rows if r and r[0]}
                    debug("_allowed_projects_for_user (pg):", list(names))
                    return names
            else:
                cur = db.conn.execute(sql_sqlite, (user_id,))
                rows = cur.fetchall() or []
                # row_factory is Row, but we selected only name; index 0 is fine
                names = {str(r[0]).strip() for r in rows if r and r[0]}
                debug("_allowed_projects_for_user (sqlite):", list(names))
                return names
    except Exception as e:
        debug("allowed_projects query failed:", repr(e))
        return set()

def _allowed_projects_for_request_user(request: Request) -> Set[str]:
    """
    Resolve user_id from Authorization or cookie JWT, then fetch allowed projects.
    Fail-closed: if we cannot resolve a user_id, return EMPTY SET (no access).
    """
    tok = _bearer_from_headers(request)
    if not tok:
        cookie_jwt = request.cookies.get(COOKIE_NAME)
        tok = cookie_jwt or None
    uid = _extract_user_id_from_token(tok)
    return _allowed_projects_for_user(uid)

_SIGNOFF_SPLIT_RE = re.compile(r"^\s*signoff:\s*(.+?)\s*(?::\s*(.+?)\s*)?$", re.IGNORECASE)

def _norm_pair(verb: str, gate: str) -> str:
    return f"{str(verb).strip()}::{str(gate).strip()}"

def _tokenize_feature_tag_header(header_val: Optional[str]) -> Set[str]:
    if not header_val:
        return set()
    return {t.strip().lower() for t in header_val.split(",") if t.strip()}

def _parse_feature_tags(header_val: Optional[str]) -> Set[str]:
    # kept for compatibility (not used directly)
    return _tokenize_feature_tag_header(header_val)

def _parse_feature_tags(header_val: Optional[str]) -> Tuple[Set[str], Set[str], Set[str], Set[str]]:
    modules: Set[str] = set()
    nouns: Set[str] = set()
    verbs: Set[str] = set()
    signoffs: Set[str] = set()
    if not header_val:
        return modules, nouns, verbs, signoffs

    tags = [t.strip() for t in header_val.split(",") if t.strip()]
    for t in tags:
        tl = t.lower()
        if tl.startswith("module:"):
            modules.add(t.split(":", 1)[1].strip())
        elif tl.startswith("noun:"):
            nouns.add(t.split(":", 1)[1].strip())
        elif tl.startswith("verb:"):
            verbs.add(t.split(":", 1)[1].strip())
        elif tl.startswith("signoff:"):
            m = _SIGNOFF_SPLIT_RE.match(t)
            if not m:
                continue
            verb_part = (m.group(1) or "").strip()
            gate_part = (m.group(2) or "").strip()
            if verb_part == "*":
                signoffs.add("*::*")
            elif gate_part == "" or gate_part == "*":
                signoffs.add(_norm_pair(verb_part, "*"))
            else:
                signoffs.add(_norm_pair(verb_part, gate_part))
        else:
            # legacy loose tokens
            nouns.add(t); verbs.add(t)
    return modules, nouns, verbs, signoffs

def _parse_projects_from_tags(header_val: Optional[str]) -> Set[str]:
    out: Set[str] = set()
    if not header_val:
        return out
    for raw in header_val.split(","):
        t = raw.strip()
        if t.lower().startswith("project:"):
            out.add(t.split(":", 1)[1].strip())
    return out

def _parse_projects_header(header_val: Optional[str]) -> Set[str]:
    if not header_val:
        return set()
    return {t.strip() for t in header_val.split(",") if t.strip()}

_PROJECT_SCOPED_PATTERNS = [
    re.compile(r"^/project/(?P<project>[^/]+)/"),
    re.compile(r"^/runlog/(?P<project>[^/]+)/"),
    re.compile(r"^/verb/(?P<project>[^/]+)/"),
    re.compile(r"^/noun/(?:types|describe|edit|register)/(?P<project>[^/]+)"),
    re.compile(r"^/noun/(?P<project>[^/]+)/"),
    # account-roles module endpoints carry the project in the path
    re.compile(r"^/api/account_roles/(?P<project>[^/]+)/"),
]

def _extract_project_from_path(path: str) -> Optional[str]:
    norm = _normalize_path(path)
    for pat in _PROJECT_SCOPED_PATTERNS:
        m = pat.search(norm)
        if m:
            return m.group("project")
    return None

PROJECT_FILTER_PATHS: List[re.Pattern[str]] = [
    re.compile(r"^/projects\b", re.IGNORECASE),
    re.compile(r"^/[^/]+/projects\b", re.IGNORECASE),
    re.compile(r"^/[^/]+/[^/]+/projects\b", re.IGNORECASE),
]

def _is_project_list_endpoint(path: str) -> bool:
    norm = _normalize_path(path)
    return any(p.search(norm) for p in PROJECT_FILTER_PATHS)

_GATE_COMPLETE_RE = re.compile(
    r"/runlog/(?P<project>[^/]+)/(?P<group>[^/]+)/(?P<run_id>[^/]+)/gate/(?P<step_id>[^/]+)/complete\b",
    re.IGNORECASE,
)

def _project_path(project: str) -> Path:
    projects_root = resolve_path(Path(), "project_root")
    return projects_root / project

# ──────────────────────────────────────────────────────────────────────────────
# Roles loader (from roles.json via resolver map)
# ──────────────────────────────────────────────────────────────────────────────

def _roles_path_for_project(project: str) -> Optional[Path]:
    try:
        projp = _project_path(project)
        return resolve_path(projp, "roles_schema")
    except Exception as e:
        debug("roles path resolve failed:", e)
        return None

def _load_roles_index(project: Optional[str]) -> Dict[str, dict]:
    if not project:
        return {}
    path = _roles_path_for_project(project)
    if not path or not path.exists():
        debug("roles.json missing for project:", project, "path:", path)
        return {}
    try:
        data = load_data(path) or {}
    except Exception as e:
        debug("roles.json load error:", e)
        return {}

    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, list):
        return {}

    idx: Dict[str, dict] = {}
    for entry in roles:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            idx[entry["name"].strip().lower()] = entry
    return idx

# ──────────────────────────────────────────────────────────────────────────────
# Role → Tags resolution (unchanged); PROJECTS now come from logins.db per-user
# ──────────────────────────────────────────────────────────────────────────────

def _collect_from_roles_header(
    roles_hdr: Optional[str],
    roles_host_project: Optional[str],
) -> Tuple[Set[str], Set[str], Set[str], Set[str], Optional[Set[str]], List[str]]:
    """
    Collect feature tags from roles.json for any roles listed in X-Account-Roles/X-Roles.
    NOTE: We NO LONGER derive allowed projects from roles.json here; that now
    comes from logins.db per-user membership.
    """
    if not roles_hdr:
        return set(), set(), set(), set(), None, []

    role_names = [r.strip() for r in roles_hdr.split(",") if r.strip()]
    if not role_names:
        return set(), set(), set(), set(), None, []

    roles_idx = _load_roles_index(roles_host_project)
    if not roles_idx:
        return set(), set(), set(), set(), None, []

    modules: Set[str] = set()
    nouns: Set[str] = set()
    verbs: Set[str] = set()
    signoffs: Set[str] = set()
    resolved: List[str] = []

    tag_strs: List[str] = []

    for rn in role_names:
        key = rn.strip().lower()
        role = roles_idx.get(key)
        if not role:
            continue
        resolved.append(rn)
        ft = role.get("feature_tags") or []
        if isinstance(ft, list):
            tag_strs.extend([t for t in ft if isinstance(t, str)])

    m, n, v, s = _parse_feature_tags(",".join(tag_strs) if tag_strs else "")
    modules |= m; nouns |= n; verbs |= v; signoffs |= s

    # allowed_projects now determined elsewhere; return None from this collector
    return modules, nouns, verbs, signoffs, None, resolved

def _effective_feature_sets(
    request: Request,
    path: Optional[str],
) -> Tuple[Set[str], Set[str], Set[str], Set[str], Optional[Set[str]], List[str]]:
    hdr_tags = request.headers.get("X-Feature-Tags")
    roles_hdr = request.headers.get("X-Account-Roles") or request.headers.get("X-Roles")

    # 🧩 NEW LOGIC: if no roles header present, auto-fetch user's roles from logins.db
    if not roles_hdr:
        try:
            tok = _bearer_from_headers(request) or request.cookies.get(COOKIE_NAME)
            user_id = _extract_user_id_from_token(tok)
            if user_id:
                with _open_db() as db:
                    if db.kind == "pg":
                        try:
                            q = "SELECT role_name FROM accounts_projects WHERE user_id = %s"
                            with db.conn.cursor() as cur:
                                cur.execute(q, (user_id,))
                                roles = [r[0] for r in cur.fetchall() if r and r[0]]
                        except Exception:
                            # fallback for older schema
                            q = "SELECT role FROM accounts_projects WHERE user_id = %s"
                            with db.conn.cursor() as cur:
                                cur.execute(q, (user_id,))
                                roles = [r[0] for r in cur.fetchall() if r and r[0]]
                    else:
                        try:
                            q = "SELECT role_name FROM accounts_projects WHERE user_id = ?"
                            cur = db.conn.execute(q, (user_id,))
                        except sqlite3.OperationalError:
                            # fallback for older schema
                            q = "SELECT role FROM accounts_projects WHERE user_id = ?"
                            cur = db.conn.execute(q, (user_id,))
                        roles = [r[0] for r in cur.fetchall() if r and r[0]]
                roles_hdr = ",".join(roles)
                debug("_effective_feature_sets: auto-resolved roles_hdr =", roles_hdr)
        except Exception as e:
            debug("_effective_feature_sets: failed auto-fetch of roles", repr(e))
    # We intentionally ignore X-Projects now; projects are from logins.db

    if _is_project_list_endpoint(path or ""):
        roles_host_project = DEFAULT_ROLES_PROJECT
    else:
        roles_host_project = _extract_project_from_path(path or "")

    # Feature tags from headers and roles.json
    m1, n1, v1, s1 = _parse_feature_tags(hdr_tags)
    m2, n2, v2, s2, _ignored_projects, resolved_roles = _collect_from_roles_header(roles_hdr, roles_host_project)

    # Compute allowed projects by *user* (logins.db)
    allowed_projects_user = _allowed_projects_for_request_user(request)

    modules = m1 | m2
    nouns   = n1 | n2
    verbs   = v1 | v2
    signoffs = s1 | s2

    if DEBUG_ENABLED:
        debug("effective tags", {
            "roles_hdr": roles_hdr or "",
            "roles_resolved": resolved_roles,
            "counts": {
                "modules": len(modules),
                "nouns": len(nouns),
                "verbs": len(verbs),
                "signoffs": len(signoffs),
                "projects": (len(allowed_projects_user) if isinstance(allowed_projects_user, set) else "N/A"),
            },
            "roles_host_project": roles_host_project or "(none)"
        })
    return modules, nouns, verbs, signoffs, allowed_projects_user, resolved_roles

def _signoff_allowed(verb_name: Optional[str], gate_id: Optional[str], allowed_signoffs: Set[str]) -> bool:
    if not allowed_signoffs:
        return False
    if "*::*" in allowed_signoffs:
        return True
    if not verb_name or not gate_id:
        return False
    if _norm_pair(verb_name, "*") in allowed_signoffs:
        return True
    if _norm_pair(verb_name, gate_id) in allowed_signoffs:
        return True
    return False

def _extract_payload_nouns(payload: Any) -> Set[str]:
    nouns: Set[str] = set()
    if not isinstance(payload, (dict, list)):
        return nouns

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = str(k).lower()
                if kl in ("noun", "noun_type"):
                    if isinstance(v, str):
                        nouns.add(v)
                if kl in ("nouns", "noun_types") and isinstance(v, list):
                    for x in v:
                        if isinstance(x, str):
                            nouns.add(x)
                walk(v)
        elif isinstance(obj, list):
            for it in obj:
                walk(it)
    walk(obj=payload)
    return nouns

def _extract_payload_verb_groups(payload: Any, project_path: Optional[Path]) -> Set[str]:
    groups: Set[str] = set()
    if not isinstance(payload, (dict, list)):
        return groups

    def maybe_resolve_test_type(tt: str) -> Optional[str]:
        if not project_path:
            return None
        try:
            return resolve_verb_group_from_test_type(project_path, tt)
        except Exception:
            return None

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = str(k).lower()
                if kl in ("verb_group", "verbgroup"):
                    if isinstance(v, str):
                        groups.add(v)
                elif kl == "verb_groups" and isinstance(v, list):
                    for x in v:
                        if isinstance(x, str):
                            groups.add(x)
                elif kl in ("test_type", "testtype"):
                    if isinstance(v, str):
                        g = maybe_resolve_test_type(v)
                        if g:
                            groups.add(g)
                walk(v)
        elif isinstance(obj, list):
            for it in obj:
                walk(it)
    walk(obj=payload)
    return groups

# Conservative: only filter lists or row-objects; never arbitrary dicts.
def _filter_json_by_nouns(body: Any, allowed_nouns: Set[str]) -> Any:
    try:
        if isinstance(body, list) and all(isinstance(x, str) for x in body):
            return [x for x in body if x in allowed_nouns]
        if isinstance(body, list) and all(isinstance(x, dict) for x in body):
            out: List[dict] = []
            for row in body:
                noun = row.get("noun") or row.get("noun_type")
                if noun is None or noun in allowed_nouns:
                    out.append(row)
            return out
        if isinstance(body, dict):
            shaped = dict(body)
            for key in ("nouns", "noun_types"):
                if isinstance(shaped.get(key), list) and all(isinstance(x, str) for x in shaped[key]):
                    shaped[key] = [x for x in shaped[key] if x in allowed_nouns]
            return shaped
    except Exception as e:
        debug("filter nouns error:", repr(e))
    return body

def _filter_json_by_verbgroups(body: Any, allowed_groups: Set[str]) -> Any:
    try:
        if not allowed_groups:
            return body
        if isinstance(body, list) and all(isinstance(x, str) for x in body):
            return [x for x in body if x in allowed_groups]
        if isinstance(body, list) and all(isinstance(x, dict) for x in body):
            out: List[dict] = []
            for row in body:
                g = row.get("verb_group") or row.get("group") or row.get("verbgroup")
                if g is None or g in allowed_groups:
                    out.append(row)
            return out
        if isinstance(body, dict) and 'runs' in body and isinstance(body['runs'], list):
            # avoid over-filtering complex run objects
            return body
        if isinstance(body, dict):
            shaped = dict(body)
            if isinstance(shaped.get("verb_groups"), list) and all(isinstance(x, str) for x in shaped["verb_groups"]):
                shaped["verb_groups"] = [x for x in shaped["verb_groups"] if x in allowed_groups]
            return shaped
    except Exception as e:
        debug("filter verbgroups error:", repr(e))
    return body

def _filter_json_by_projects(body: Any, allowed_projects: Optional[Set[str]]) -> Any:
    if allowed_projects is None:
        return body
    try:
        if isinstance(body, list) and all(isinstance(x, str) for x in body):
            return [x for x in body if x in allowed_projects]

        if isinstance(body, list) and all(isinstance(x, dict) for x in body):
            out: List[dict] = []
            for row in body:
                p = row.get("project")
                if p is None or p in allowed_projects:
                    out.append(row)
            return out

        if isinstance(body, dict):
            shaped = dict(body)
            if isinstance(shaped.get("projects"), list) and all(isinstance(x, str) for x in shaped["projects"]):
                shaped["projects"] = [x for x in shaped["projects"] if x in allowed_projects]
            if isinstance(shaped.get("project"), str) and shaped["project"] not in allowed_projects:
                return {}
            return shaped
    except Exception as e:
        debug("filter projects error:", repr(e))
    return body

# ──────────────────────────────────────────────────────────────────────────────
# PRE hook (core)
# ──────────────────────────────────────────────────────────────────────────────

def _rules_pre_core(request: Request, envelope: Dict[str, Any]) -> JSONResponse:
    method = str(envelope.get("method", "GET")).upper()
    path   = str(envelope.get("path", "/"))
    payload = envelope.get("payload", None)

    norm_path = _normalize_path(path)

    modules, allowed_nouns, allowed_verbs, allowed_signoffs, allowed_projects, resolved_roles = \
        _effective_feature_sets(request, norm_path)

    # Project-list endpoints: allow here, shape later
    if _is_project_list_endpoint(norm_path):
        debug("rules_pre: project-list endpoint -> allow (filter will occur in post)")
        return JSONResponse({"effect": "allow"})

    # Enforce project scoping when clearly present in the URL
    req_project = _extract_project_from_path(norm_path)
    if allowed_projects is not None and req_project:
        if req_project not in allowed_projects:
            debug("rules_pre: DENY project", {"requested": req_project, "allowed": list(allowed_projects)})
            raise HTTPException(status_code=403, detail=f"Access to project '{req_project}' is not permitted.")

    # If no tags at all, deny gate signoff; otherwise allow passthrough
    if not (modules or allowed_nouns or allowed_verbs or allowed_signoffs):
        if method == "POST" and _GATE_COMPLETE_RE.search(norm_path):
            debug("rules_pre: DENY gate signoff (no tags)")
            raise HTTPException(status_code=403, detail="Not signed in: cannot sign off.")
        debug("rules_pre: no feature tags -> allow passthrough")
        return JSONResponse({"effect": "allow"})

    # Gate signoff enforcement
    if method == "POST":
        m = _GATE_COMPLETE_RE.search(norm_path)
        if m:
            project = m.group("project")
            run_id  = unquote(m.group("run_id"))
            step_id = m.group("step_id")

            pp = _project_path(project) if project else None
            try:
                verb = resolve_run_id_to_test_type(pp, run_id) if (pp and run_id) else None
            except Exception:
                verb = None

            if not allowed_signoffs:
                detail = "Sign-off not permitted: no roles / feature tags present."
                debug("rules_pre: DENY gate signoff (unauthenticated)", {"verb": verb, "step": step_id})
                raise HTTPException(status_code=403, detail=detail)

            if not _signoff_allowed(verb, step_id, allowed_signoffs):
                detail = f"Sign-off not permitted for gate '{step_id}'" + (f" on verb '{verb}'." if verb else ".")
                debug("rules_pre: DENY gate signoff", {"verb": verb, "step": step_id})
                raise HTTPException(status_code=403, detail=detail)

            debug("rules_pre: ALLOW gate signoff", {
                "roles": resolved_roles, "verb": verb, "gate": step_id,
                "run_id": run_id, "project": project
            })

    # Best-effort payload checks
    proj = _extract_project_from_path(norm_path)
    proj_path = _project_path(proj) if proj else None

    nouns_in_payload = _extract_payload_nouns(payload)
    verbs_in_payload = _extract_payload_verb_groups(payload, proj_path)
    debug("rules_pre: found", {"nouns": list(nouns_in_payload), "verb_groups": list(verbs_in_payload)})

    if nouns_in_payload:
        disallowed = {n for n in nouns_in_payload if n not in allowed_nouns}
        if disallowed:
            mutated = False
            if isinstance(payload, dict):
                for key in ("nouns", "noun_types"):
                    if key in payload and isinstance(payload[key], list):
                        before = list(payload[key])
                        payload[key] = [x for x in payload[key] if x in allowed_nouns]
                        mutated |= (payload[key] != before)
                for key in ("noun", "noun_type"):
                    if key in payload and isinstance(payload[key], str) and payload[key] not in allowed_nouns:
                        payload[key] = None
                        mutated = True
            if mutated:
                debug("rules_pre: mutated payload to strip disallowed nouns", {"disallowed": list(disallowed)})
                return JSONResponse({"effect": "mutate", "payload": payload})
            else:
                debug("rules_pre: deny due to disallowed nouns", {"disallowed": list(disallowed)})
                raise HTTPException(status_code=403, detail=f"Access to noun(s) {sorted(disallowed)} is not permitted.")

    if verbs_in_payload:
        disallowed = {g for g in verbs_in_payload if g not in allowed_verbs}
        if disallowed:
            mutated = False
            if isinstance(payload, dict):
                for key in ("verb_groups",):
                    if key in payload and isinstance(payload[key], list):
                        before = list(payload[key])
                        payload[key] = [x for x in payload[key] if x in allowed_verbs]
                        mutated |= (payload[key] != before)
                for key in ("verb_group", "group"):
                    if key in payload and isinstance(payload.get(key), str) and payload[key] not in allowed_verbs:
                        payload[key] = None
                        mutated = True
            if mutated:
                debug("rules_pre: mutated payload to strip disallowed verb groups", {"disallowed": list(disallowed)})
                return JSONResponse({"effect": "mutate", "payload": payload})
            else:
                debug("rules_pre: deny due to disallowed verb groups", {"disallowed": list(disallowed)})
                raise HTTPException(status_code=403, detail=f"Access to verb group(s) {sorted(disallowed)} is not permitted.")

    debug("rules_pre: allow")
    return JSONResponse({"effect": "allow"})

@router.post("/orchestrate/rules/pre")
async def rules_pre(request: Request):
    debug("rules_pre: begin")
    try:
        envelope = await request.json()
        debug("rules_pre: envelope", envelope if DEBUG_ENABLED else "{hidden}")
    except Exception as e:
        debug("rules_pre: invalid JSON", repr(e))
        raise HTTPException(status_code=400, detail="Invalid JSON envelope")
    return _rules_pre_core(request, envelope)

# Alias path so the orchestrator can explicitly target this node’s pre-hook.
@router.post("/login-rules/orchestrate/rules/pre")
async def rules_pre_alias(request: Request):
    debug("rules_pre_alias: begin")
    try:
        envelope = await request.json()
        debug("rules_pre_alias: envelope", envelope if DEBUG_ENABLED else "{hidden}")
    except Exception as e:
        debug("rules_pre_alias: invalid JSON", repr(e))
        raise HTTPException(status_code=400, detail="Invalid JSON envelope")
    return _rules_pre_core(request, envelope)

# ──────────────────────────────────────────────────────────────────────────────
# POST hook
# ──────────────────────────────────────────────────────────────────────────────

SAFE_JSON_PASSTHROUGH: List[re.Pattern[str]] = [
    re.compile(r"^/schema/verb/", re.IGNORECASE),
    re.compile(r"^/schema/noun/", re.IGNORECASE),
    re.compile(r"^/schema/adverb/", re.IGNORECASE),
    # ── Account Roles module: never filter anything here ───────────────────────
    re.compile(r"^/api/account_roles(?:/|$)", re.IGNORECASE),
]

VERB_FILTER_PATHS: List[re.Pattern[str]] = [
    re.compile(r"^/runlog_data_dump/verb_groups\b", re.IGNORECASE),
    re.compile(r"^/runlog/[^/]+/[^/]+/[^/]+/status/linear\b", re.IGNORECASE),
    re.compile(r"^/runlog/[^/]+/[^/]+/[^/]+/gate/(list|status)\b", re.IGNORECASE),
]

NOUN_FILTER_PATHS: List[re.Pattern[str]] = [
    # noun list & metadata used by the UI
    re.compile(r"^/noun/types/[^/]+$", re.IGNORECASE),
    re.compile(r"^/noun/[^/]+/catalog\b", re.IGNORECASE),
    re.compile(r"^/noun/[^/]+/describe\b", re.IGNORECASE),
    re.compile(r"^/noun/[^/]+/register\b", re.IGNORECASE),
    # NOTE: intentionally NOT filtering any /api/account_roles/... endpoints
]

def _path_matches(path: str, patterns: List[re.Pattern[str]]) -> bool:
    norm = _normalize_path(path)
    for p in patterns:
        if p.search(norm):
            return True
    return False

@router.post("/orchestrate/rules/post")
async def rules_post(request: Request):
    debug("rules_post: begin")
    try:
        envelope = await request.json()
        debug("rules_post: envelope", envelope if DEBUG_ENABLED else "{hidden}")
    except Exception as e:
        debug("rules_post: invalid JSON", repr(e))
        raise HTTPException(status_code=400, detail="Invalid JSON envelope")

    path   = str(envelope.get("path", "/"))
    status = int(envelope.get("status", 200))
    body   = envelope.get("body", None)

    norm_path = _normalize_path(path)

    modules, allowed_nouns, allowed_verbs, allowed_signoffs, allowed_projects, resolved_roles = \
        _effective_feature_sets(request, norm_path)

    if not (modules or allowed_nouns or allowed_verbs or allowed_signoffs) and allowed_projects is None:
        debug("rules_post: no feature tags and no project constraints -> passthrough")
        return JSONResponse({"body": body})

    if status < 200 or status >= 300 or not isinstance(body, (dict, list)):
        return JSONResponse({"body": body})

    # Absolute passthroughs (including the account-roles module)
    for pat in SAFE_JSON_PASSTHROUGH:
        if pat.search(norm_path):
            debug("rules_post: SAFE passthrough", {"path": norm_path})
            return JSONResponse({"body": body})

    filtered = body
    try:
        if _path_matches(norm_path, PROJECT_FILTER_PATHS):
            filtered = _filter_json_by_projects(filtered, allowed_projects)

        if _path_matches(norm_path, NOUN_FILTER_PATHS):
            filtered = _filter_json_by_nouns(filtered, allowed_nouns)

        if _path_matches(norm_path, VERB_FILTER_PATHS):
            filtered = _filter_json_by_verbgroups(filtered, allowed_verbs)

        # Shape visible gates by signoff permissions (if present)
        if isinstance(filtered, dict) and "gates" in filtered and isinstance(filtered["gates"], list):
            verb_name = str(filtered.get("verb") or "").strip() or None
            if verb_name:
                new_gates = []
                for g in filtered["gates"]:
                    if not isinstance(g, dict):
                        continue
                    gate_id = str(g.get("id") or "").strip()
                    if _signoff_allowed(verb_name, gate_id, allowed_signoffs):
                        new_gates.append(g)
                filtered = dict(filtered)
                filtered["gates"] = new_gates
                debug("rules_post: shaped gates by signoff permissions", {
                    "roles": resolved_roles, "verb": verb_name,
                    "kept": [g.get("id") for g in new_gates]
                })
    except Exception as e:
        debug("rules_post: filter error", repr(e))

    return JSONResponse({"body": filtered})

# -----------------------------------------------------------------------------
# Export node
# -----------------------------------------------------------------------------
login_rules_node = Node(
    name="Rules: Feature Tags (Nouns, Verb Groups, Gates, Projects)",
    kind=NodeKind.RULES,
    router=router,
    route_prefix="",
    meta={"enforces": ["noun", "verb_group", "signoff_gate", "project"], "debug": True},
)
