# api/routers/template.py

from __future__ import annotations

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

from core.errors import AppError
from pydantic import BaseModel, Field, constr
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
from datetime import datetime
import base64
import hashlib
import os
import json
import sqlite3
import re
import contextlib

# -------------------------
# Debug block
# -------------------------
from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()

router = APIRouter(prefix="/api/project_template", tags=["Project Template"])
log.debug("Router initialized at /api/project_template")

# Optional Postgres client for RDS
try:
    import psycopg  # psycopg v3
    _PSYCOPG_AVAILABLE = True
except Exception as e:
    _PSYCOPG_AVAILABLE = False
    log.debug("psycopg not available:", repr(e))

# Reuse existing project/helpers
from api.i_o import (
    load_local_layout_map,
    list_verb_groups,
    get_verb_group_log_config,
    io_list_projects,
)

# Resolver gives us the same RDS-awareness as account_roles_gui
from api.manifest.resolver import resolve_path, get_db_uri
from api.storage_aws import normalize_pg_dsn as _normalize_for_psycopg

# ---------- Internal helpers ----------

def _api_dir() -> Path:
    p = Path(__file__).resolve().parent
    log.debug("_api_dir:", p.as_posix())
    return p

def _repo_root() -> Path:
    from utils.paths import repo_root
    r = repo_root()
    log.debug("_repo_root:", r.as_posix())
    return r

def _projects_root() -> Path:
    layout = load_local_layout_map(_api_dir())  # finds manifest relative to this file
    root_name = layout.get("project_root", "projects")
    root = _repo_root() / root_name
    log.debug("_projects_root:", root.as_posix())
    return root

def _project_path(project_name: str) -> Path:
    p = _projects_root() / project_name
    log.debug("_project_path:", project_name, "->", p.as_posix())
    return p

def _list_projects() -> List[str]:
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception:
        # Optional: return empty list on failure instead of 500
        log.warning("_list_projects: io_list_projects failed", exc_info=True)
        return []

def _load_if_exists(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.debug("_load_if_exists: failed to load", path.as_posix(), repr(e))
        return None

def _sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256(); h.update(b); return h.hexdigest()

def _collect_custom_dir(project_path: Path, rel_dir: str = "custom") -> dict:
    bundle = {"files": []}
    custom_root = project_path / rel_dir
    if not custom_root.exists() or not custom_root.is_dir():
        return bundle

    for root, _, files in os.walk(custom_root):
        if "__pycache__" in root or "/." in root:
            continue
        for fname in files:
            fp = Path(root) / fname
            relp = fp.relative_to(project_path).as_posix()
            try:
                raw = fp.read_bytes()
            except Exception:
                log.warning("_collect_custom_dir: skipping unreadable file %s", fp.as_posix(), exc_info=True)
                continue
            content_b64 = base64.b64encode(raw).decode("ascii")
            st = fp.stat()
            mode = oct(st.st_mode & 0o777)
            bundle["files"].append({
                "path": relp,
                "mode": mode,
                "size": st.st_size,
                "sha256": _sha256_bytes(raw),
                "content_b64": content_b64,
            })
    return bundle

def _write_b64_file(project_root: Path, rel_path: str, content_b64: str, mode: Optional[str] = None):
    out_path = project_root / rel_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(content_b64.encode("ascii"))
    out_path.write_bytes(data)
    if mode:
        try:
            os.chmod(out_path, int(mode, 8))
        except Exception:
            pass
    return out_path

def _scour_project(project_name: str) -> dict:
    proj_path = _project_path(project_name)
    if not proj_path.exists():
        raise AppError("PROJECT_NOT_FOUND", f"Project '{project_name}' not found",
                       status=404, details={"project": project_name})

    project_config = _load_if_exists(proj_path / "config.json")

    noun_types = _load_if_exists(proj_path / "noun_types.json")
    verb_types = _load_if_exists(proj_path / "verb_types.json")
    adjective_types = _load_if_exists(proj_path / "adjective_types.json")
    adverb_types = _load_if_exists(proj_path / "adverb_types.json")
    archive_policy = _load_if_exists(proj_path / "archive_policy.json")
    roles = _load_if_exists(proj_path / "roles.json")

    try:
        groups = list_verb_groups(proj_path)
    except Exception:
        log.warning("_scour_project: list_verb_groups failed for project %s", project_name, exc_info=True)
        groups = []

    vg_payload = []
    for g in groups:
        try:
            cfg = get_verb_group_log_config(proj_path, g)
        except Exception:
            log.warning("_scour_project: get_verb_group_log_config failed for project %s group %s",
                        project_name, g, exc_info=True)
            cfg = None
        vg_payload.append({"name": g, "log_config": cfg})

    manifest_path = _api_dir() / "manifest" / "local_layout_map.json"
    manifest_text = manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else "{}"
    layout_fingerprint = {
        "sha256": _sha256_bytes(manifest_text.encode("utf-8")),
        "keys": list(json.loads(manifest_text).keys()) if manifest_text.strip() else []
    }

    template = {
        "template_version": "1.1",
        "captured_at": datetime.utcnow().isoformat() + "Z",
        "project_meta": { "source_project": project_name },
        "layout_map_fingerprint": layout_fingerprint,
        "project_config": project_config,
        "schemas": {
            "noun_types": noun_types,
            "verb_types": verb_types,
            "adjective_types": adjective_types,
            "adverb_types": adverb_types,
            "roles": roles,
        },
        "archive_policy": archive_policy,
        "verbs": { "groups": vg_payload },
        "custom": _collect_custom_dir(proj_path, rel_dir="custom"),
    }
    return template

def _validate_new_name(name: str) -> str:
    log.debug("_validate_new_name:", name)
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in ("-", "_", " ", ".")).strip()
    if not cleaned:
        raise AppError("INVALID_PROJECT_NAME", "Invalid project name", status=400)
    return cleaned

# NOTE: underscore removed from the allowed set (no underscores)
_PROJECT_CODE_RE = re.compile(r"^[A-Za-z0-9.-]{2,64}$")

def _validate_project_code(code: str) -> str:
    log.debug("_validate_project_code:", code)
    if not code or not _PROJECT_CODE_RE.match(code):
        raise AppError(
            "INVALID_PROJECT_CODE",
            "Invalid project_code. Use 2–64 characters: letters, numbers, dot, or hyphen (no underscores).",
            status=400,
        )
    return code

def _ensure_groups_from_template(project_root: Path, verbs_section: dict):
    groups = verbs_section.get("groups") or []
    for g in groups:
        name = g.get("name")
        if not name:
            continue
        group_dir = project_root / "verbs" / name
        group_dir.mkdir(parents=True, exist_ok=True)
        cfg = g.get("log_config")
        if cfg is not None:
            (group_dir / f"{name}_log_config.json").write_text(
                json.dumps(cfg, indent=2), encoding="utf-8"
            )

# ---------- DB selection (resolver-first like account_roles_gui) ----------

def _logins_db_path() -> Path:
    p = resolve_path(Path(), "logins_db")
    log.debug("_logins_db_path:", p.as_posix())
    return p

def _env_dsn() -> Optional[str]:
    # Allow explicit overrides first
    dsn = os.getenv("GIMS_RDS_URL") or os.getenv("DATABASE_URL")
    log.debug("_env_dsn present?", bool(dsn))
    if dsn:
        return dsn

    # Assemble from PG* if available
    host = os.getenv("PGHOST") or os.getenv("POSTGRES_HOST")
    db   = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB")
    user = os.getenv("PGUSER") or os.getenv("POSTGRES_USER")
    pwd  = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD")
    port = os.getenv("PGPORT") or os.getenv("POSTGRES_PORT") or "5432"
    if host and db and user and pwd:
        return f"postgresql://{user}:{pwd}@{host}:{port}/{db}"
    return None

def _effective_logins_dsn() -> Tuple[str, str]:
    """
    Resolve the effective connection target (Postgres DSN or SQLite path),
    using the same resolver flow `account_roles_gui` uses.

    Returns (backend_kind, value)
      - ("postgres", DSN)  or
      - ("sqlite", /abs/path/to/logins.db)
    """
    # 1) Explicit env (highest priority)
    dsn = _env_dsn()
    if dsn:
        log.debug("_effective_logins_dsn: using env DSN")
        return ("postgres", _normalize_for_psycopg(dsn))

    # 2) Resolver (same behavior as account_roles_gui)
    try:
        uri = get_db_uri("logins_db")  # may be postgresql+asyncpg://... or sqlite+aiosqlite:///...
        log.debug("_effective_logins_dsn: resolver returned:", uri)
    except Exception as e:
        log.debug("_effective_logins_dsn: resolver failed:", repr(e))
        uri = None

    if uri:
        if uri.startswith("postgresql+"):
            return ("postgres", _normalize_for_psycopg(uri))
        if uri.startswith("postgresql://"):
            return ("postgres", uri)
        if uri.startswith("sqlite"):
            # Fall through to SQLite with manifest path
            pass

    # 3) Fallback to local SQLite (manifest-resolved)
    p = _logins_db_path()
    return ("sqlite", p.as_posix())

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
    kind, target = _effective_logins_dsn()

    if kind == "postgres" and _PSYCOPG_AVAILABLE:
        # psycopg requires real DSN; normalize userinfo in case of special chars:
        # If credentials include special characters, urlencode them safely.
        # (Handle only the authority if needed.)
        log.debug("_open_db: connecting to PostgreSQL:", target)
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

    # SQLite fallback
    if kind == "postgres" and not _PSYCOPG_AVAILABLE:
        log.debug("_open_db: psycopg not available, falling back to SQLite")

    db_path = _logins_db_path()
    log.debug("_open_db: connecting to SQLite at", db_path.as_posix())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path.as_posix())
    try:
        conn.execute("PRAGMA foreign_keys=ON;")
        yield _DBHandle("sqlite", conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def _exec(db: _DBHandle, sql: str, params: Tuple[Any, ...] = ()) -> None:
    log.debug("_exec:", {"kind": db.kind, "sql": sql[:80] + ("..." if len(sql) > 80 else ""), "params": params})
    if db.kind == "pg":
        with db.conn.cursor() as cur:
            cur.execute(sql, params)
    else:
        db.conn.execute(sql.replace("%s", "?"), params)

def _fetchone(db: _DBHandle, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Tuple[Any, ...]]:
    log.debug("_fetchone:", {"kind": db.kind, "sql": sql[:80] + ("..." if len(sql) > 80 else ""), "params": params})
    if db.kind == "pg":
        with db.conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            log.debug("_fetchone ->", row)
            return row
    else:
        cur = db.conn.execute(sql.replace("%s", "?"), params)
        row = cur.fetchone()
        log.debug("_fetchone ->", row)
        return row

def _ensure_projects_table(db: _DBHandle) -> None:
    log.debug("_ensure_projects_table:", db.kind)
    if db.kind == "pg":
        _exec(db, """
        CREATE TABLE IF NOT EXISTS projects (
          id TEXT PRIMARY KEY,
          name TEXT UNIQUE,
          project_code TEXT UNIQUE NOT NULL,
          description TEXT,
          created_at TIMESTAMPTZ
        );
        """)
    else:
        _exec(db, """
        CREATE TABLE IF NOT EXISTS projects (
          id TEXT PRIMARY KEY,
          name TEXT UNIQUE,
          project_code TEXT UNIQUE NOT NULL,
          description TEXT,
          created_at TEXT
        );
        """)

def _now_utc_iso() -> str:
    ts = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
    log.debug("_now_utc_iso:", ts)
    return ts

def _upsert_project_in_logins(project_name: str, project_code: str, description: Optional[str] = None) -> None:
    log.debug("_upsert_project_in_logins: begin", {"name": project_name, "code": project_code, "desc": description})
    with _open_db() as db:
        _ensure_projects_table(db)

        row = _fetchone(
            db,
            "SELECT project_code FROM projects WHERE name = %s LIMIT 1",
            (project_name,)
        )
        if row and row[0] and row[0] != project_code:
            raise AppError(
                "PROJECT_CODE_CONFLICT",
                f"Project name '{project_name}' already exists with project_code '{row[0]}'.",
                status=409,
                details={"project": project_name, "existing_project_code": row[0],
                         "requested_project_code": project_code},
            )

        if db.kind == "pg":
            log.debug("  upsert (pg) by PRIMARY KEY id=project_code")
            _exec(db, """
                INSERT INTO projects (id, name, project_code, description, created_at)
                VALUES (%s, %s, %s, %s, NOW() AT TIME ZONE 'UTC')
                ON CONFLICT (id) DO UPDATE SET
                  name = EXCLUDED.name,
                  description = EXCLUDED.description
            """, (project_code, project_name, project_code, description))
        else:
            log.debug("  upsert (sqlite) by project_code")
            _exec(db, """
                INSERT INTO projects (id, name, project_code, description, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT(project_code) DO UPDATE SET
                  name=excluded.name,
                  description=excluded.description
            """, (project_code, project_name, project_code, description, _now_utc_iso()))
    log.debug("_upsert_project_in_logins: done")

# ---------- Pydantic models ----------

class NewProjectConfig(BaseModel):
    name: str = Field(..., description="New project directory name under projects/")
    project_code: constr(strip_whitespace=True, min_length=2, max_length=64) = Field(
        ..., description="Short unique code used as the project's ID in logins.db"
    )
    description: Optional[str] = Field(None, description="Any description string")
    version: Optional[str] = Field(None, description="Optional version string, preserved in config.json")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra fields")

class ImportTemplateRequest(BaseModel):
    template: Optional[Dict[str, Any]] = Field(None, description="Template JSON. Omit to create a new, empty project.")
    config: NewProjectConfig
    overwrite: bool = False
    copy_custom: bool = True
    dry_run: bool = False

# ---------- Routes ----------

@router.get("/projects")
def list_projects():
    log.debug("GET /projects")
    return _list_projects()

@router.get("/{project}/export")
def export_project_template(project: str, download: bool = Query(True)):
    log.debug("GET /{project}/export:", {"project": project, "download": download})
    tmpl = _scour_project(project)
    data = json.dumps(tmpl, indent=2)
    headers = {}
    if download:
        fname = f"project_template__{project}__{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
        headers["Content-Disposition"] = f'attachment; filename="{fname}"'
    return JSONResponse(content=json.loads(data), headers=headers)

@router.post("/import")
def import_project_template(req: ImportTemplateRequest = Body(...)):
    log.debug("POST /import: begin")
    new_name = _validate_new_name(req.config.name)
    project_code = _validate_project_code(req.config.project_code)

    target = _project_path(new_name)
    log.debug("  target path:", target.as_posix())
    if not req.dry_run and target.exists():
        raise AppError("PROJECT_ALREADY_EXISTS", f"Project '{new_name}' already exists",
                       status=409, details={"project": new_name})

    # Upsert in DB FIRST (if not a dry run) to enforce uniqueness (RDS if available)
    if not req.dry_run:
        log.debug("  performing DB upsert (not dry-run)")
        _upsert_project_in_logins(new_name, project_code, req.config.description)

    is_new_project_from_scratch = req.template is None
    template = req.template or {}

    # Build write plan
    plan: List[Dict[str, Any]] = []
    plan.append({"op": "mkdir", "path": target.as_posix()})
    plan.append({"op": "mkdir", "path": (target / "verbs").as_posix()})
    plan.append({"op": "mkdir", "path": (target / "nouns").as_posix()})
    plan.append({"op": "mkdir", "path": (target / "custom").as_posix()})
    plan.append({"op": "mkdir", "path": (target / "project_nodes").as_posix()})
    plan.append({"op": "touch", "path": (target / "project_nodes" / "nodes.db").as_posix()})

    tmpl_cfg = template.get("project_config") or {}
    cfg_out = dict(tmpl_cfg)
    cfg_out["name"] = new_name
    cfg_out["project_code"] = project_code
    if req.config.description is not None:
        cfg_out["description"] = req.config.description
    if req.config.version is not None:
        cfg_out["version"] = req.config.version
    for k, v in (req.config.extra or {}).items():
        cfg_out[k] = v

    plan.append({"op": "write_json", "path": (target / "config.json").as_posix(), "data": cfg_out})

    schemas = template.get("schemas") or {}
    schema_map = {
        "noun_types": "noun_types.json",
        "verb_types": "verb_types.json",
        "adjective_types": "adjective_types.json",
        "adverb_types": "adverb_types.json",
        "roles": "roles.json",
    }
    for key, filename in schema_map.items():
        schema_data = schemas.get(key)
        if schema_data is not None:
            plan.append({"op": "write_json", "path": (target / filename).as_posix(), "data": schema_data})
        elif is_new_project_from_scratch and key in ["noun_types", "verb_types", "adjective_types", "adverb_types"]:
            plan.append({"op": "write_json", "path": (target / filename).as_posix(), "data": {}})

    if template.get("archive_policy") is not None:
        plan.append({"op": "write_json", "path": (target / "archive_policy.json").as_posix(), "data": template["archive_policy"]})

    verbs_section = template.get("verbs") or {}
    for g in verbs_section.get("groups", []):
        gname = g.get("name")
        if not gname:
            continue
        gdir = target / "verbs" / gname
        plan.append({"op": "mkdir", "path": gdir.as_posix()})
        if g.get("log_config") is not None:
            plan.append({"op": "write_json", "path": (gdir / f"{gname}_log_config.json").as_posix(), "data": g["log_config"]})

    if req.copy_custom and not is_new_project_from_scratch:
        custom = template.get("custom") or {}
        for f in custom.get("files", []):
            plan.append({
                "op": "write_b64",
                "path": (target / f["path"]).as_posix(),
                "mode": f.get("mode"),
                "content_b64": f.get("content_b64", "")
            })

    if req.dry_run:
        kind, where = _effective_logins_dsn()
        return {
            "dry_run": True,
            "target": target.as_posix(),
            "plan": plan,
            "db_backend": "postgres" if kind == "postgres" else "sqlite",
            "db_url_or_path": where,
            "project_code": project_code,
        }

    log.debug("POST /import: executing plan with", len(plan), "steps")
    for step in plan:
        op = step["op"]
        p = Path(step["path"])
        log.debug("  exec:", op, "->", p.as_posix())
        if op == "mkdir":
            p.mkdir(parents=True, exist_ok=True)
        elif op == "write_json":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(step["data"], indent=2), encoding="utf-8")
        elif op == "write_b64":
            _write_b64_file(_repo_root(), p.relative_to(_repo_root()).as_posix(),
                            step["content_b64"], mode=step.get("mode"))
        elif op == "touch":
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_bytes(b"")

    kind, where = _effective_logins_dsn()
    log.debug("POST /import: done")
    return {
        "ok": True,
        "created": target.as_posix(),
        "project_code": project_code,
        "db_backend": "postgres" if kind == "postgres" else "sqlite",
        "db_url_or_path": where,
    }
