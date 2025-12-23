# gui/backup_gui.py
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query, Path as FPath
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any, List, Tuple
from pathlib import Path
from datetime import datetime, timedelta, timezone
import zipfile
import hashlib
import sqlite3
import shutil
import os
import json
import uuid
import io
import tempfile
import contextlib

# ──────────────────────────────────────────────────────────────────────────────
# Debug utilities
# ──────────────────────────────────────────────────────────────────────────────
DEBUG_ENABLED = False  # Change to True to enable debug logs

def debug(*args, **kwargs):
    """Debug print that respects DEBUG_ENABLED flag."""
    if DEBUG_ENABLED:
        print("[backup_gui]", *args, **kwargs)

# Optional Postgres client for RDS
try:
    import psycopg  # psycopg v3
    _PSYCOPG_AVAILABLE = True
except Exception as e:
    _PSYCOPG_AVAILABLE = False
    debug("psycopg not available:", repr(e))

# ──────────────────────────────────────────────────────────────────────────────
# GIMS helpers (resolver + S3-aware I/O)
# ──────────────────────────────────────────────────────────────────────────────
from api.i_o import load_local_layout_map
from api.manifest.resolver import resolve_path, get_db_uri  # RDS-aware resolver
# Import i_o to leverage its S3-aware JSON helpers (save_json/read_json) when available.
from api import i_o

router = APIRouter(prefix="/api/storage", tags=["Storage/Backup"])

# ──────────────────────────────────────────────────────────────────────────────
# Path helpers
# ──────────────────────────────────────────────────────────────────────────────
def _api_dir() -> Path:
    return Path(__file__).resolve().parent

def _repo_root() -> Path:
    return _api_dir().parent

def _layout_name() -> str:
    layout = load_local_layout_map(_api_dir())
    return layout.get("project_root", "projects")

def _projects_root() -> Path:
    # Prefer resolver if it provides a dedicated key; else fallback to local convention.
    try:
        return resolve_path(Path(), "project_root")
    except Exception:
        return _repo_root() / _layout_name()

def _project_path(project_name: str) -> Path:
    return _projects_root() / project_name

def _backups_root() -> Path:
    """
    Backups live beside /projects at repo root (avoid recursion). If the resolver
    supports a 'backups_root' key, prefer it to enable S3-backed layouts.
    """
    try:
        return resolve_path(Path(), "backups_root")
    except Exception:
        return _repo_root() / "backups"

def _cfg_root() -> Path:
    # Simple JSON-backed config store (used for schedules)
    return _ensure_dir(_backups_root() / "_config")

def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _now_iso() -> str:
    return _now_utc().replace(microsecond=0).isoformat()

def _new_id(prefix: str = "") -> str:
    return (prefix + uuid.uuid4().hex[:12]).lower()

def _dated_backup_dir(project: str, backup_id: str, base: Optional[Path] = None) -> Path:
    base = base or _backups_root()
    d = _now_utc()
    return _ensure_dir(base / project / f"{d:%Y-%m-%d}" / backup_id)

# ──────────────────────────────────────────────────────────────────────────────
# DSN helpers (RDS)
# ──────────────────────────────────────────────────────────────────────────────
def _normalize_for_psycopg(url: str) -> str:
    """
    Convert SQLAlchemy/async URLs to psycopg-compatible.
      'postgresql+asyncpg://' → 'postgresql://'
      '?ssl=require'         → '?sslmode=require'
    """
    if not url:
        return url
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("postgresql+", 1)[1]
    if "?ssl=require" in url:
        url = url.replace("?ssl=require", "?sslmode=require")
    url = url.replace("postgresql://asyncpg://", "postgresql://")
    return url

@contextlib.contextmanager
def _pg_conn(dsn: str):
    if not _PSYCOPG_AVAILABLE:
        raise RuntimeError("psycopg not available")
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("SET search_path TO public;")
            except Exception:
                pass
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass

# ──────────────────────────────────────────────────────────────────────────────
# Hashing & local fs utils
# (Note: JSON I/O is S3-aware via helpers further below)
# ──────────────────────────────────────────────────────────────────────────────
def _sha256_file(path: Path) -> str:
    debug("hashing:", path)
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _write_text(path: Path, text: str, encoding="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)

# Legacy JSON helpers (kept for fallback) — will be bypassed by S3-aware wrappers
def _write_json(path: Path, obj: Any):
    _write_text(path, json.dumps(obj, indent=2))

def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception as e:
        debug("read_json error:", path, e)
        return default

# ──────────────────────────────────────────────────────────────────────────────
# S3-aware JSON wrappers
# (Mirror the approach from adjective/adverb GUIs: lean on api.i_o when present)
# ──────────────────────────────────────────────────────────────────────────────
def _save_json_s3(path: Path, obj: Any):
    """
    S3-aware JSON write. Uses i_o.save_json if available (which routes to S3 when
    the path is under a managed root, e.g., projects/ or backups/), falling back
    to local write otherwise.
    """
    try:
        if hasattr(i_o, "save_json"):
            debug("[json][save] via i_o.save_json →", path)
            i_o.save_json(path, obj)
        else:
            debug("[json][save] fallback local →", path)
            _write_json(path, obj)
    except Exception as e:
        debug("[json][save][error]", path, e)
        raise

def _read_json_s3(path: Path, default: Any = None) -> Any:
    """
    S3-aware JSON read. Uses i_o.read_json if available, else falls back to
    local read. If default is provided and the file is missing, returns default.
    """
    try:
        if hasattr(i_o, "read_json"):
            debug("[json][read] via i_o.read_json ←", path)
            return i_o.read_json(path, default=default) if "default" in i_o.read_json.__code__.co_varnames else i_o.read_json(path)
        # Fallback to local handling
        if default is not None:
            return _read_json(path, default)
        return json.loads(path.read_text())
    except FileNotFoundError:
        if default is not None:
            return default
        raise
    except Exception as e:
        debug("[json][read][error]", path, e)
        if default is not None:
            return default
        raise

# ──────────────────────────────────────────────────────────────────────────────
# ZIP creation (project tree)
# ──────────────────────────────────────────────────────────────────────────────
DEFAULT_EXCLUDES = {
    "__pycache__",
    ".venv",
    ".git",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".DS_Store",
    ".idea",
    ".vscode",
    "backups",  # extra safety if someone put backups/ inside a project by mistake
}

def _should_exclude(rel_parts: Tuple[str, ...]) -> bool:
    return any(seg in DEFAULT_EXCLUDES for seg in rel_parts)

def _zip_project_tree(project_path: Path, out_zip: Path) -> dict:
    debug("zipping project:", project_path, "->", out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    cnt = 0
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if not _should_exclude(Path(root, d).relative_to(project_path).parts)]
            for fname in files:
                fpath = Path(root) / fname
                rel = fpath.relative_to(project_path)
                if _should_exclude(rel.parts):
                    continue
                z.write(fpath, arcname=str(rel).replace("\\", "/"))
                cnt += 1
    size = out_zip.stat().st_size
    sha = _sha256_file(out_zip)
    debug(f"zip done: {cnt} files, size={size}, sha256={sha}")
    return {"path": out_zip.name, "sha256": sha, "size": size, "files": cnt}

# ──────────────────────────────────────────────────────────────────────────────
# SQLite hot copy (legacy/offline)
# ──────────────────────────────────────────────────────────────────────────────
KNOWN_DB_KEYS = [
    "object_sql_db",   # nouns/objects database
    "archive_sql_db",  # archive database
    "nodes_db",        # audit/compliance logs
    "logins_db",       # accounts & projects
]

def _sqlite_hot_copy(src: Path, dest: Path) -> dict:
    debug("sqlite hot copy:", src, "->", dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ok = False
    try:
        src_conn = sqlite3.connect(str(src))
        try:
            dest_conn = sqlite3.connect(str(dest))
            with dest_conn:
                src_conn.backup(dest_conn)
            dest_conn.close()
            ok = True
        finally:
            src_conn.close()
    except Exception as e:
        debug("sqlite backup() failed, falling back to copy2:", e)
    if not ok:
        shutil.copy2(src, dest)
    size = dest.stat().st_size
    sha = _sha256_file(dest)
    rel = str(dest.relative_to(dest.parent.parent)).replace("\\", "/")
    return {"path": rel, "sha256": sha, "size": size}

def _collect_sqlite_artifacts(project_path: Path, db_out_dir: Path) -> Dict[str, dict]:
    artifacts: Dict[str, dict] = {}
    for key in KNOWN_DB_KEYS:
        try:
            src = resolve_path(project_path, key)
        except Exception as e:
            debug(f"resolve_path failed for {key}:", e)
            continue
        if not src.exists():
            debug(f"db not found for {key} ->", src)
            continue

        try:
            original_rel = str(src.relative_to(project_path)).replace("\\", "/")
        except ValueError:
            debug(f"db '{key}' is outside project path, skipping sqlite backup:", src)
            continue  # Skip this key

        dest = db_out_dir / f"{key}.sqlite"
        info = _sqlite_hot_copy(src, dest)
        info["original_rel"] = original_rel
        info["key"] = key
        info["backend"] = "sqlite"
        artifacts[key] = info
        debug("db snapshot:", key, "->", info)
    return artifacts

# ──────────────────────────────────────────────────────────────────────────────
# Postgres logical dumps (RDS)
# ──────────────────────────────────────────────────────────────────────────────
def _get_key_dsn(key: str) -> Optional[str]:
    try:
        uri = get_db_uri(key)
        if uri and uri.startswith("postgresql"):
            return _normalize_for_psycopg(uri)
    except Exception as e:
        debug("[dsn] resolver failed for", key, "->", repr(e))
    return None

def _pg_copy_to_csv(conn, sql: str, params: dict, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    debug("[pg][copy] →", out_path.name, "::", sql.replace("\n", " "))
    with conn.cursor() as cur, out_path.open("w", encoding="utf-8", newline="") as f:
        with cur.copy(sql, params=params) as copy:
            while True:
                chunk = copy.read()
                if not chunk:
                    break
                if isinstance(chunk, memoryview):
                    chunk = chunk.tobytes()
                f.write(chunk.decode("utf-8"))

def _pg_list_project_prefixed_tables(conn, project: str) -> List[str]:
    like_pattern = project + r"\_%"
    sql = """
      SELECT quote_ident(n.nspname) || '.' || quote_ident(c.relname) AS fqtn
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'public'
        AND c.relkind IN ('r','p')
        AND c.relname LIKE %(pfx)s ESCAPE '\\'
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"pfx": like_pattern})
        return [r[0] for r in cur.fetchall()]

def _pg_list_meta_tables_with_project_col(conn) -> List[str]:
    sql = """
      SELECT quote_ident(n.nspname) || '.' || quote_ident(c.relname) AS fqtn
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'project'
      WHERE n.nspname = 'public'
        AND c.relkind IN ('r','p')
        AND c.relname LIKE 'meta%%'
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return [r[0] for r in cur.fetchall()]

def _dump_archive_sql_db_pg(project: str, out_dir: Path, dsn: str) -> Dict[str, Any]:
    files = []
    with _pg_conn(dsn) as conn:
        for fqtn in _pg_list_project_prefixed_tables(conn, project):
            table_name = fqtn.split(".")[-1]
            out = out_dir / f"{table_name}.csv"
            _pg_copy_to_csv(conn, f"COPY (SELECT * FROM {fqtn}) TO STDOUT WITH CSV HEADER", {}, out)
            files.append(out.name)
        for name in ("noun_archive_index", "runs_archive_index"):
            out = out_dir / f"{name}.csv"
            _pg_copy_to_csv(
                conn,
                f"COPY (SELECT * FROM public.{name} WHERE project = %(project)s) TO STDOUT WITH CSV HEADER",
                {"project": project},
                out,
            )
            files.append(out.name)
    return {"backend": "pg", "dir": out_dir.name, "files": files, "file_count": len(files)}

def _dump_objects_sql_db_pg(project: str, out_dir: Path, dsn: str) -> Dict[str, Any]:
    files = []
    with _pg_conn(dsn) as conn:
        for fqtn in _pg_list_project_prefixed_tables(conn, project):
            table_name = fqtn.split(".")[-1]
            out = out_dir / f"{table_name}.csv"
            _pg_copy_to_csv(conn, f"COPY (SELECT * FROM {fqtn}) TO STDOUT WITH CSV HEADER", {}, out)
            files.append(out.name)
        for fqtn in _pg_list_meta_tables_with_project_col(conn):
            table_name = fqtn.split(".")[-1]
            out = out_dir / f"{table_name}.csv"
            _pg_copy_to_csv(
                conn,
                f"COPY (SELECT * FROM {fqtn} WHERE project = %(project)s) TO STDOUT WITH CSV HEADER",
                {"project": project},
                out,
            )
            files.append(out.name)
    return {"backend": "pg", "dir": out_dir.name, "files": files, "file_count": len(files)}

def _dump_nodes_db_pg(project: str, out_dir: Path, dsn: str) -> Dict[str, Any]:
    files = []
    with _pg_conn(dsn) as conn:
        out = out_dir / "audit_log.csv"
        _pg_copy_to_csv(
            conn,
            """
            COPY (
              SELECT *
              FROM public.audit_log
              WHERE %(project)s = ANY ( string_to_array(project, ', ') )
            ) TO STDOUT WITH CSV HEADER
            """,
            {"project": project},
            out,
        )
        files.append(out.name)
        out = out_dir / "compliance_log.csv"
        _pg_copy_to_csv(
            conn,
            "COPY (SELECT * FROM public.compliance_log WHERE project = %(project)s) TO STDOUT WITH CSV HEADER",
            {"project": project},
            out,
        )
        files.append(out.name)
    return {"backend": "pg", "dir": out_dir.name, "files": files, "file_count": len(files)}

def _dump_logins_db_pg(project: str, out_dir: Path, dsn: str) -> Dict[str, Any]:
    files = []
    with _pg_conn(dsn) as conn:
        out = out_dir / "projects.csv"
        _pg_copy_to_csv(
            conn,
            "COPY (SELECT * FROM public.projects WHERE name = %(project)s) TO STDOUT WITH CSV HEADER",
            {"project": project},
            out,
        )
        files.append(out.name)
        out = out_dir / "accounts_projects.csv"
        _pg_copy_to_csv(
            conn,
            """
            COPY (
              SELECT ap.*
              FROM public.accounts_projects ap
              JOIN public.projects p ON p.id = ap.project_id
              WHERE p.name = %(project)s
            ) TO STDOUT WITH CSV HEADER
            """,
            {"project": project},
            out,
        )
        files.append(out.name)
        out = out_dir / "users.csv"
        _pg_copy_to_csv(
            conn,
            """
            COPY (
              SELECT u.*
              FROM public.users u
              WHERE u.id IN (
                SELECT ap.user_id
                FROM public.accounts_projects ap
                JOIN public.projects p ON p.id = ap.project_id
                WHERE p.name = %(project)s
              )
            ) TO STDOUT WITH CSV HEADER
            """,
            {"project": project},
            out,
        )
        files.append(out.name)
    return {"backend": "pg", "dir": out_dir.name, "files": files, "file_count": len(files)}

def _collect_pg_artifacts(project: str, project_path: Path, db_out_dir: Path) -> Dict[str, dict]:
    artifacts: Dict[str, dict] = {}
    for key in KNOWN_DB_KEYS:
        dsn = _get_key_dsn(key)
        if not dsn:
            continue
        out_dir = db_out_dir / key
        try:
            if key == "archive_sql_db":
                meta = _dump_archive_sql_db_pg(project, out_dir, dsn)
            elif key == "object_sql_db":
                meta = _dump_objects_sql_db_pg(project, out_dir, dsn)
            elif key == "nodes_db":
                meta = _dump_nodes_db_pg(project, out_dir, dsn)
            elif key == "logins_db":
                meta = _dump_logins_db_pg(project, out_dir, dsn)
            else:
                debug("[pg] unknown key, skipping:", key)
                continue
            meta["key"] = key
            artifacts[key] = meta
            debug("[pg] dumped", key, "->", meta)
        except Exception as e:
            debug("[pg] dump failed for", key, ":", repr(e))
            artifacts[key] = {"key": key, "backend": "pg", "dir": str(out_dir.relative_to(db_out_dir)), "error": str(e)}
    return artifacts

# ──────────────────────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────────────────────
class BackupNowRequest(BaseModel):
    project: str = Field(..., description="Project directory name under /projects")
    type: str = Field("hybrid", description="zip | sqlite | hybrid")
    paranoid: bool = Field(False, description="If true, also write per-file checksums.txt where applicable")
    notes: Optional[str] = None

    @validator("type")
    def _type_ok(cls, v):
        if v not in {"zip", "sqlite", "hybrid"}:
            raise ValueError("type must be one of: zip | sqlite | hybrid")
        return v

class RestoreRequest(BaseModel):
    project: str = Field(..., description="Original project name (for lookup)")
    mode: str = Field("clone", description="clone | inplace (inplace not yet implemented)")
    new_project: Optional[str] = Field(None, description="New project name for clone mode")
    scope: Optional[str] = Field(None, description="None | db_only | files_only")

class Schedule(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("sch-"))
    project: str
    type: str = Field("hybrid", description="zip | sqlite | hybrid")
    frequency: str = Field(..., description="hourly | daily | weekly | monthly")
    # timing options
    minute: int = 0
    hour: int = 2
    dow: Optional[int] = None
    dom: Optional[int] = None
    retention_keep: Optional[int] = Field(10, description="Keep last N backups for this schedule")
    enabled: bool = True
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    notes: Optional[str] = None

    @validator("frequency")
    def _freq_ok(cls, v):
        if v not in {"hourly", "daily", "weekly", "monthly"}:
            raise ValueError("frequency must be hourly|daily|weekly|monthly")
        return v

    @validator("dom")
    def _dom_ok(cls, v, values):
        if values.get("frequency") == "monthly":
            if v is None:
                return 1
            if v < 1 or v > 28:
                raise ValueError("dom must be in 1..28 for monthly")
        return v

# ──────────────────────────────────────────────────────────────────────────────
# Simple JSON store (schedules) — now S3-aware
# ──────────────────────────────────────────────────────────────────────────────
def _schedules_path() -> Path:
    return _cfg_root() / "schedules.json"

def _load_schedules() -> list[Schedule]:
    path = _schedules_path()
    try:
        data = i_o.load_data(path)  # S3-aware read + JSON parse
        if not data:
            return []
        return [Schedule(**s) for s in data]
    except Exception as e:
        debug(f"[S3] failed to read schedules: {e}")
        return []

def _save_schedules(items: List[Schedule]):
    _save_json_s3(_schedules_path(), [s.dict() for s in items])

# ──────────────────────────────────────────────────────────────────────────────
# Manifest helpers (S3-aware JSON)
# ──────────────────────────────────────────────────────────────────────────────
def _manifest_skeleton(project: str, btype: str, created_by="system") -> dict:
    return {
        "backup_id": _new_id("bkp-"),
        "project": project,
        "type": btype,
        "created_at": _now_iso(),
        "created_by": created_by,
        "engine": {"app_version": None, "migration": None, "git": None},
        "artifacts": {},
        "retention_class": "ad-hoc",
        "destination": None,   # primary path (local backups folder or S3-mounted)
        "notes": None,
    }

def _write_checksums_txt(folder: Path, manifest: dict):
    lines = []
    artifacts = manifest.get("artifacts", {})
    if "project_zip" in artifacts:
        entry = artifacts["project_zip"]
        lines.append(f"{entry.get('sha256')}  {entry.get('path')}")
    db_map = (artifacts.get("db") or {})
    for key, meta in db_map.items():
        if meta.get("backend") == "sqlite":
            db_rel = f"db/{key}.sqlite"
            p = Path(folder) / db_rel
            if p.exists():
                lines.append(f"{_sha256_file(p)}  {db_rel}")
    if lines:
        _write_text(folder / "checksums.txt", "\n".join(lines) + "\n")

def _find_backup_dir(project: str, backup_id: str, base: Optional[Path] = None) -> Path | None:
    root = (base or _backups_root()) / project
    if not root.exists():
        return None
    for dated in sorted(root.iterdir()):
        if not dated.is_dir():
            continue
        candidate = dated / backup_id
        if candidate.exists():
            return candidate
    return None

def _load_manifest(project: str, backup_id: str, base: Optional[Path] = None) -> Tuple[dict, Path]:
    bdir = _find_backup_dir(project, backup_id, base=base)
    if not bdir:
        raise HTTPException(status_code=404, detail="Backup not found")
    mpath = bdir / "SNAPSHOT_MANIFEST.json"
    # S3-aware read:
    manifest = _read_json_s3(mpath)
    return manifest, bdir

def _validate_artifacts(project: str, backup_id: str, base: Optional[Path] = None) -> dict:
    debug("=" * 90)
    debug(f"[VALIDATE][BEGIN] project={project!r}, backup_id={backup_id!r}")

    try:
        manifest, bdir = _load_manifest(project, backup_id, base=base)
        debug(f"[VALIDATE][LOAD] Manifest loaded from {bdir}")
    except Exception as e:
        debug(f"[VALIDATE][ERROR] Failed to load manifest: {e}")
        raise HTTPException(status_code=500, detail=f"Could not load manifest: {e}")

    results = {"project_zip": None, "db": {}, "ok": True}
    artifacts = manifest.get("artifacts", {})
    debug(f"[VALIDATE][MANIFEST] artifact keys = {list(artifacts.keys())}")

    # Validate project.zip
    try:
        if "project_zip" in artifacts:
            meta = artifacts["project_zip"]
            path = bdir / meta["path"]
            debug(f"[VALIDATE][ZIP] Checking project.zip → {path}")

            if not path.exists():
                debug(f"[VALIDATE][ZIP][FAIL] Missing file: {path}")
                results["project_zip"] = {"ok": False, "error": "missing"}
                results["ok"] = False
            else:
                sha = _sha256_file(path)
                expected = meta.get("sha256")
                ok = sha == expected
                results["project_zip"] = {"ok": ok, "sha256": sha, "expected": expected}
                debug(f"[VALIDATE][ZIP][OK={ok}] sha256={sha}, expected={expected}")
                if not ok:
                    results["ok"] = False
        else:
            debug("[VALIDATE][ZIP][SKIP] No project_zip entry in manifest")
    except Exception as e:
        debug(f"[VALIDATE][ZIP][ERROR] Exception during zip validation: {e}")
        results["ok"] = False

    # Validate DB artifacts
    db_map = artifacts.get("db") or {}
    debug(f"[VALIDATE][DB] Found {len(db_map)} DB entries: {list(db_map.keys())}")

    for key, meta in db_map.items():
        backend = meta.get("backend", "?")
        debug(f"[VALIDATE][DB][{key}] backend={backend} meta={meta}")
        try:
            if backend == "sqlite":
                path = bdir / "db" / f"{key}.sqlite"
                if not path.exists():
                    debug(f"[VALIDATE][DB][{key}][FAIL] Missing {path}")
                    results["db"][key] = {"ok": False, "error": "missing"}
                    results["ok"] = False
                else:
                    sha = _sha256_file(path)
                    results["db"][key] = {"ok": True, "sha256": sha}
                    debug(f"[VALIDATE][DB][{key}][OK] sha256={sha}")

            elif backend == "pg":
                dir_rel = meta.get("dir", f"db/{key}")
                if not dir_rel.startswith("db/"):
                    dir_rel = f"db/{dir_rel}"
                d = bdir / dir_rel
                debug(f"[VALIDATE][DB][{key}] Checking PG dir {d}")
                if not d.exists():
                    results["db"][key] = {"ok": False, "error": f"Missing directory {d}"}
                    results["ok"] = False
                    debug(f"[VALIDATE][DB][{key}][FAIL] Missing directory {d}")
                else:
                    csvs = list(d.glob("*.csv"))
                    results["db"][key] = {"ok": len(csvs) > 0, "files": len(csvs)}
                    debug(f"[VALIDATE][DB][{key}][OK] {len(csvs)} csv files")
                    if len(csvs) == 0:
                        results["ok"] = False

            else:
                debug(f"[VALIDATE][DB][{key}][FAIL] Unknown backend: {backend}")
                results["db"][key] = {"ok": False, "error": f"unknown backend '{backend}'"}
                results["ok"] = False

        except Exception as e:
            debug(f"[VALIDATE][DB][{key}][ERROR] Exception: {e}")
            results["db"][key] = {"ok": False, "error": str(e)}
            results["ok"] = False

    debug(f"[VALIDATE][SUMMARY] ok={results['ok']} project_zip={results['project_zip']} db={list(results['db'].keys())}")
    debug("=" * 90)
    return results


def _clone_restore(project: str, backup_id: str, new_project: Optional[str], scope: Optional[str]) -> dict:
    manifest, bdir = _load_manifest(project, backup_id)
    src_project_path = _project_path(project)
    if not src_project_path.exists():
        debug("warning: original project path not found, continuing:", src_project_path)
    target_name = new_project or f"{project} (restored {datetime.utcnow().strftime('%Y-%m-%d_%H%M%S')})"
    safe_name = "".join(ch for ch in target_name if ch.isalnum() or ch in ("-", "_", " ", ".")).strip()
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid new project name")
    target_path = _project_path(safe_name)
    if target_path.exists():
        raise HTTPException(status_code=409, detail="Target project already exists")
    debug("clone restore target:", target_path)
    target_path.mkdir(parents=True, exist_ok=False)
    scope = (scope or "").lower().strip() or None

    # Files restore (project.zip)
    if scope != "db_only" and "project_zip" in manifest.get("artifacts", {}):
        zip_meta = manifest["artifacts"]["project_zip"]
        zip_path = bdir / zip_meta["path"]
        if not zip_path.exists():
            raise HTTPException(status_code=500, detail="project.zip missing in backup")
        debug("extracting ZIP:", zip_path)
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(target_path)

    # DB restore: SQLite snapshots and/or Postgres CSV dumps
    if scope != "files_only":
        db_map = manifest.get("artifacts", {}).get("db") or {}
        for key, meta in db_map.items():
            backend = meta.get("backend")

            if backend == "sqlite":
                snap_path = bdir / "db" / f"{key}.sqlite"
                if not snap_path.exists():
                    debug("sqlite artifact missing, skipping:", key)
                    continue
                rel = meta.get("original_rel")
                if not rel:
                    debug("sqlite artifact missing original_rel:", key)
                    continue
                dest = target_path / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                debug("placing sqlite:", snap_path, "->", dest)
                shutil.copy2(snap_path, dest)
                continue

            if backend == "pg":
                dsn = _get_key_dsn(key)
                if not dsn:
                    debug(f"[pg][restore] no DSN for {key}, skipping")
                    continue
                dir_rel = meta.get("dir", f"db/{key}")
                src_dir = bdir / dir_rel
                if not src_dir.exists():
                    debug(f"[pg][restore] missing dump folder for {key}, skipping")
                    continue
                debug(f"[pg][restore] restoring {key} from {src_dir} → {dsn}")
                try:
                    with _pg_conn(dsn) as conn:
                        with conn.cursor() as cur:
                            for csv_path in sorted(src_dir.glob("*.csv")):
                                table_name = Path(csv_path.name).stem.replace('"', "")
                                sql = f'COPY public."{table_name}" FROM STDIN WITH CSV HEADER'
                                debug(f"[pg][restore][COPY] {csv_path.name} → {table_name}")
                                with open(csv_path, "r", encoding="utf-8") as f:
                                    try:
                                        cur.copy(sql, f.read())
                                    except Exception as e:
                                        debug(f"[pg][restore][error] {table_name}: {e}")
                        conn.commit()
                except Exception as e:
                    debug(f"[pg][restore] failed for {key}: {e}")
                continue

    return {
        "ok": True,
        "restored_to": target_path.as_posix(),
        "backup_id": backup_id,
        "project": project,
        "new_project": safe_name,
    }

# ──────────────────────────────────────────────────────────────────────────────
# Scheduling helpers
# ──────────────────────────────────────────────────────────────────────────────
def _compute_next_run(s: Schedule, from_time: Optional[datetime] = None) -> datetime:
    t = (from_time or _now_utc()).replace(second=0, microsecond=0)
    minute = int(s.minute or 0)
    hour = int(s.hour or 0)
    if s.frequency == "hourly":
        candidate = t.replace(minute=minute)
        if candidate <= t:
            candidate += timedelta(hours=1)
        return candidate
    if s.frequency == "daily":
        candidate = t.replace(hour=hour, minute=minute)
        if candidate <= t:
            candidate += timedelta(days=1)
        return candidate
    if s.frequency == "weekly":
        dow = int(s.dow if s.dow is not None else 0)  # 0=Mon
        days_ahead = (dow - t.weekday()) % 7
        candidate = t + timedelta(days=days_ahead)
        candidate = candidate.replace(hour=hour, minute=minute)
        if candidate <= t:
            candidate += timedelta(days=7)
        return candidate
    if s.frequency == "monthly":
        dom = int(s.dom if s.dom is not None else 1)
        candidate = t.replace(day=min(dom, 28), hour=hour, minute=minute)
        if candidate <= t:
            month = candidate.month + 1
            year = candidate.year + (1 if month == 13 else 0)
            month = 1 if month == 13 else month
            candidate = candidate.replace(year=year, month=month, day=min(dom, 28))
        return candidate
    return t + timedelta(hours=1)

def _apply_retention(project: str, keep_last: Optional[int]):
    if not keep_last or keep_last <= 0:
        return
    root = _backups_root() / project
    if not root.exists():
        return
    entries: List[Tuple[str, Path]] = []
    for dated in sorted(root.iterdir(), reverse=True):
        if not dated.is_dir():
            continue
        for bdir in sorted(dated.iterdir(), reverse=True):
            if not bdir.is_dir():
                continue
            entries.append((f"{dated.name}/{bdir.name}", bdir))
    if len(entries) <= keep_last:
        return
    to_delete = entries[keep_last:]
    for _, path in to_delete:
        debug("retention: deleting", path)
        shutil.rmtree(path, ignore_errors=True)

# ──────────────────────────────────────────────────────────────────────────────
# Routes: Projects
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return i_o.io_list_projects()
    except Exception as e:
        # Optional: return empty list on failure instead of 500
        print(f"[list_projects] list_projects failed: {e!r}")
        return []

# ──────────────────────────────────────────────────────────────────────────────
# Routes: Backups
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/backups")
def list_backups(project: str = Query(..., description="Project name under /projects")):
    root = _backups_root() / project
    if not root.exists():
        return {"project": project, "backups": []}
    entries = []
    for dated in sorted(root.iterdir(), reverse=True):
        if not dated.is_dir():
            continue
        for bdir in sorted(dated.iterdir(), reverse=True):
            if not bdir.is_dir():
                continue
            m = bdir / "SNAPSHOT_MANIFEST.json"
            if not m.exists():
                continue
            try:
                # S3-aware manifest read:
                manifest = _read_json_s3(m)
                size = None
                zmeta = (manifest.get("artifacts", {}).get("project_zip") or {})
                zpath = bdir / zmeta.get("path", "project.zip")
                if zpath.exists():
                    try:
                        size = zpath.stat().st_size
                    except Exception:
                        size = None
                entries.append({
                    "backup_id": manifest.get("backup_id") or bdir.name,
                    "created_at": manifest.get("created_at"),
                    "type": manifest.get("type"),
                    "notes": manifest.get("notes"),
                    "dated_dir": dated.name,
                    "size_bytes": size
                })
            except Exception as e:
                debug("manifest parse error:", m, e)
                continue
    return {"project": project, "backups": entries}

@router.get("/backups/{backup_id}")
def get_backup_manifest(backup_id: str, project: str = Query(...)):
    manifest, bdir = _load_manifest(project, backup_id)
    manifest["_paths"] = {
        "folder": bdir.as_posix(),
        "project_zip": (bdir / (manifest.get("artifacts", {}).get("project_zip") or {}).get("path", "project.zip")).as_posix(),
    }
    return manifest

@router.post("/backup-now")
def backup_now(req: BackupNowRequest = Body(...)):
    debug("="*80)
    debug("[BACKUP_NOW_START]")
    debug("[1. PROJECT] Validating project path...")
    project_path = _project_path(req.project)
    if not project_path.exists():
        debug(f"[1. PROJECT][ERROR] Project path not found: {project_path}")
        raise HTTPException(status_code=404, detail=f"Project '{req.project}' not found")
    debug(f"[1. PROJECT][OK] Project path found: {project_path}")

    debug("[2. MANIFEST] Creating skeleton...")
    manifest = _manifest_skeleton(req.project, req.type, created_by="(api)")
    if req.notes:
        manifest["notes"] = req.notes
    debug(f"[2. MANIFEST][OK] Skeleton created. backup_id={manifest['backup_id']}")

    debug("[3. FOLDERS] Creating backup directory structure...")
    backup_dir = _dated_backup_dir(req.project, manifest["backup_id"])
    db_out_dir = backup_dir / "db"
    artifacts: Dict[str, Any] = {}
    debug(f"[3. FOLDERS][OK] Backup root created: {backup_dir}")
    debug(f"[3. FOLDERS][OK] DB output dir set: {db_out_dir}")

    debug(f"[4. DB] Collecting DB artifacts (type={req.type})...")
    if req.type in {"sqlite", "hybrid"}:
        debug("[4a. DB-PG] Checking for Postgres artifacts...")
        pg_map = _collect_pg_artifacts(req.project, project_path, db_out_dir)
        debug(f"[4a. DB-PG][OK] Postgres artifact collection complete. Found {len(pg_map)} DBs.")

        debug("[4b. DB-SQLITE] Checking for SQLite artifacts...")
        sqlite_map = _collect_sqlite_artifacts(project_path, db_out_dir)
        debug(f"[4b. DB-SQLITE][OK] SQLite artifact collection complete. Found {len(sqlite_map)} DBs.")

        debug("[4c. DB-MERGE] Merging DB artifact maps (PG overrides SQLite)...")
        db_map: Dict[str, Any] = {}
        db_map.update(sqlite_map)
        db_map.update(pg_map)
        artifacts["db"] = db_map
        debug(f"[4c. DB-MERGE][OK] Merge complete. Final DB keys: {list(db_map.keys())}")
    else:
        debug(f"[4. DB][SKIP] Skipping DB artifact collection (type is '{req.type}')")

    debug(f"[5. ZIP-CREATE] Creating initial project.zip (type={req.type})...")
    if req.type in {"zip", "hybrid"}:
        zip_path = backup_dir / "project.zip"
        debug(f"[5. ZIP-CREATE] Zipping project tree from {project_path} -> {zip_path}")
        zip_info = _zip_project_tree(project_path, zip_path)
        debug(f"[5. ZIP-CREATE][OK] Initial zip created. Size={zip_info['size']}, Files={zip_info['files']}, SHA={zip_info['sha256']}")

        debug(f"[6. ZIP-APPEND] Appending 'db' folder ({db_out_dir}) to {zip_path}...")
        file_count_added = 0
        if db_out_dir.exists() and any(db_out_dir.iterdir()):
            debug("[6. ZIP-APPEND] 'db' folder exists and is not empty. Opening zip in 'a' (append) mode...")
            try:
                with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                    debug("[6. ZIP-APPEND] Walking {db_out_dir} to find files to add...")
                    for root, _, files in os.walk(db_out_dir):
                        for fname in files:
                            fpath = Path(root) / fname
                            arcname = str(fpath.relative_to(backup_dir)).replace("\\", "/")
                            if arcname.endswith(".pg.zip"):
                                debug(f"  -> [SKIP] Ignoring lazy-zip file: {arcname}")
                                continue
                            debug(f"  -> [ADD] Adding {fpath.name} as {arcname}")
                            z.write(fpath, arcname=arcname)
                            file_count_added += 1
                debug(f"[6. ZIP-APPEND][OK] Finished appending. Added {file_count_added} files.")
            except Exception as e:
                debug(f"[6. ZIP-APPEND][ERROR] Failed to append files to zip: {e}")
                raise
        else:
            debug("[6. ZIP-APPEND][SKIP] 'db' folder is missing or empty. Nothing to append.")

        debug("[7. ZIP-RECALC] Recalculating final zip info (size, sha, file count)...")
        new_size = zip_path.stat().st_size
        debug(f"[7. ZIP-RECALC]  -> New size: {new_size}")
        new_sha = _sha256_file(zip_path)
        debug(f"[7. ZIP-RECALC]  -> New SHA: {new_sha}")
        final_file_count = 0
        try:
            debug("[7. ZIP-RECALC] Opening zip in 'r' mode to count files...")
            with zipfile.ZipFile(zip_path, "r") as z:
                final_file_count = len(z.infolist())
            debug(f"[7. ZIP-RECALC]  -> New file count: {final_file_count}")
        except Exception as e:
            debug(f"[7. ZIP-RECALC][WARN] Could not read zip for file count: {e}. Falling back to estimate.")
            final_file_count = zip_info.get("files", 0) + file_count_added
            debug(f"[7. ZIP-RECALC]  -> Estimated file count: {final_file_count}")

        zip_info["size"] = new_size
        zip_info["files"] = final_file_count
        zip_info["sha256"] = new_sha
        debug(f"[7. ZIP-RECALC][OK] Final zip info: size={new_size}, files={final_file_count}, sha={new_sha}")
        artifacts["project_zip"] = zip_info
    else:
        debug(f"[5. ZIP-CREATE][SKIP] Skipping zip creation (type is '{req.type}')")

    debug("[8. MANIFEST-WRITE] Writing final manifest...")
    manifest["artifacts"] = artifacts
    manifest["destination"] = backup_dir.as_posix()
    # S3-aware write:
    _save_json_s3(backup_dir / "SNAPSHOT_MANIFEST.json", manifest)
    debug("[8. MANIFEST-WRITE][OK] SNAPSHOT_MANIFEST.json written.")

    if req.paranoid:
        debug("[9. CHECKSUMS] Paranoid mode: writing checksums.txt...")
        _write_checksums_txt(backup_dir, manifest)
        debug("[9. CHECKSUMS][OK] checksums.txt written.")
    else:
        debug("[9. CHECKSUMS][SKIP] Paranoid mode off.")

    debug("[10. COMPLETE] Backup complete.")
    debug("="*80)
    return {
        "ok": True,
        "backup_id": manifest["backup_id"],
        "project": req.project,
        "folder": backup_dir.as_posix(),
        "manifest": manifest,
    }

@router.post("/validate/{backup_id}")
def validate_backup(backup_id: str, project: str = Body(..., embed=True)):
    results = _validate_artifacts(project, backup_id)
    return {"project": project, "backup_id": backup_id, **results}

@router.post("/restore/{backup_id}")
def restore_backup(backup_id: str, req: RestoreRequest = Body(...)):
    if req.mode not in {"clone", "inplace"}:
        raise HTTPException(status_code=400, detail="mode must be clone | inplace")
    if req.mode == "inplace":
        raise HTTPException(status_code=400, detail="in-place restore not implemented yet")
    return _clone_restore(req.project, backup_id, req.new_project, req.scope)

@router.delete("/backups/{backup_id}")
def delete_backup(backup_id: str, project: str = Body(..., embed=True)):
    bdir = _find_backup_dir(project, backup_id)
    if not bdir:
        raise HTTPException(status_code=404, detail="Backup not found")
    debug("deleting backup:", bdir)
    shutil.rmtree(bdir)
    return {"ok": True, "deleted": bdir.as_posix(), "project": project, "backup_id": backup_id}

# ──────────────────────────────────────────────────────────────────────────────
# Downloads
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/download/{backup_id}/project.zip")
def download_zip(backup_id: str, project: str = Query(...)):
    debug("=" * 90)
    debug(f"[download][BEGIN] project.zip request for project='{project}', backup_id='{backup_id}'")
    try:
        manifest, bdir = _load_manifest(project, backup_id)
        debug(f"[download] manifest loaded from {_backups_root()} -> {bdir}")
    except Exception as e:
        debug(f"[download][error] failed to load manifest: {e}")
        raise

    debug("[download][manifest] keys:", list(manifest.keys()))
    artifacts = manifest.get("artifacts", {})
    debug("[download][manifest.artifacts] keys:", list(artifacts.keys()))

    zip_meta = artifacts.get("project_zip", {})
    debug("[download][zip_meta]", zip_meta)

    zip_rel = zip_meta.get("path", "project.zip")
    z = bdir / zip_rel
    debug(f"[download][path] expected zip file: {z}")

    try:
        all_entries = [p.relative_to(bdir) for p in bdir.rglob("*")]
        debug(f"[download][bdir content] {len(all_entries)} entries under {bdir}")
        for p in all_entries[:50]:
            debug("   ↳", p)
        if len(all_entries) > 50:
            debug("   ... (truncated)")
    except Exception as e:
        debug(f"[download][error] failed to enumerate {bdir}: {e}")

    if not z.exists():
        debug(f"[download][missing] file not found: {z}")
        debug(f"[download][cwd] os.getcwd()={os.getcwd()}")
        debug(f"[download][repo_root]={_repo_root()}")
        debug(f"[download][backups_root]={_backups_root()}")
        debug(f"[download][projects_root]={_projects_root()}")
        alt_zips = list(bdir.glob("*.zip"))
        debug(f"[download][alt candidates] found {len(alt_zips)} zips: {[p.name for p in alt_zips]}")
        raise HTTPException(status_code=404, detail="project.zip not found in backup")

    try:
        sz = z.stat().st_size
        sha = _sha256_file(z)
        debug(f"[download][file stats] size={sz:,} bytes, sha256={sha}")
    except Exception as e:
        debug(f"[download][error] failed to stat/hash file {z}: {e}")

    db_artifacts = artifacts.get("db") or {}
    debug(f"[download][db_artifacts] total={len(db_artifacts)}")
    for key, meta in db_artifacts.items():
        debug(f"    {key} backend={meta.get('backend')} dir={meta.get('dir')} path={bdir / 'db' / (meta.get('dir') or key)}")

    debug(f"[download][SERVE] returning FileResponse({z}) as {project}__{backup_id}__project.zip")
    debug("=" * 90)
    return FileResponse(z, filename=f"{project}__{backup_id}__project.zip")

@router.get("/download/{backup_id}/db/{key}")
def download_db_any(backup_id: str, key: str, project: str = Query(...)):
    manifest, bdir = _load_manifest(project, backup_id)
    meta = (manifest.get("artifacts", {}).get("db") or {}).get(key)
    if not meta:
        raise HTTPException(status_code=404, detail=f"No DB artifact found for key '{key}'")
    backend = meta.get("backend", "sqlite")

    if backend == "sqlite":
        p = bdir / "db" / f"{key}.sqlite"
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"{key}.sqlite not found in backup")
        debug(f"[download] serving SQLite {p}")
        return FileResponse(p, filename=f"{project}__{backup_id}__{key}.sqlite")

    if backend == "pg":
        dir_from_manifest = meta.get("dir", key)
        d = bdir / "db" / dir_from_manifest
        if not d.exists():
            err_path = f"db/{dir_from_manifest}"
            debug(f"[download] Dump folder missing at expected path: {d}")
            raise HTTPException(status_code=404, detail=f"Dump folder missing: {err_path}")
        out_zip = bdir / "db" / f"{key}.pg.zip"
        if not out_zip.exists():
            out_zip.parent.mkdir(parents=True, exist_ok=True)
            debug(f"[download] zipping Postgres dump {d} -> {out_zip}")
            with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for p in sorted(d.glob("*.csv")):
                    z.write(p, arcname=p.name)
        debug(f"[download] serving Postgres zip {out_zip}")
        return FileResponse(out_zip, filename=f"{project}__{backup_id}__{key}.pg.zip")

    raise HTTPException(status_code=400, detail=f"Unsupported backend type '{backend}'")

# ──────────────────────────────────────────────────────────────────────────────
# Schedules API (S3-aware JSON for schedules.json)
# ──────────────────────────────────────────────────────────────────────────────
class ScheduleRequest(BaseModel):
    id: Optional[str] = None
    project: str
    type: str = "hybrid"
    frequency: str
    minute: int = 0
    hour: int = 2
    dow: Optional[int] = None
    dom: Optional[int] = None
    retention_keep: Optional[int] = 10
    enabled: bool = True
    notes: Optional[str] = None

@router.get("/schedules")
def list_schedules(project: Optional[str] = Query(None)):
    items = _load_schedules()
    if project:
        items = [s for s in items if s.project == project]
    return [s.dict() for s in items]

@router.post("/schedules")
def create_or_update_schedule(req: ScheduleRequest):
    items = _load_schedules()
    if req.id:
        idx = next((i for i, s in enumerate(items) if s.id == req.id), None)
        if idx is None:
            raise HTTPException(status_code=404, detail="schedule id not found")
        s = items[idx]
        s.project = req.project
        s.type = req.type
        s.frequency = req.frequency
        s.minute = req.minute
        s.hour = req.hour
        s.dow = req.dow
        s.dom = req.dom
        s.retention_keep = req.retention_keep
        s.enabled = req.enabled
        s.notes = req.notes
        s.next_run_at = _compute_next_run(s).isoformat()
        items[idx] = s
    else:
        s = Schedule(
            project=req.project,
            type=req.type,
            frequency=req.frequency,
            minute=req.minute,
            hour=req.hour,
            dow=req.dow,
            dom=req.dom,
            retention_keep=req.retention_keep,
            enabled=req.enabled,
            notes=req.notes,
        )
        s.next_run_at = _compute_next_run(s).isoformat()
        items.append(s)
    _save_schedules(items)
    return {"ok": True, "schedules": [s.dict() for s in items]}

@router.delete("/schedules/{sch_id}")
def delete_schedule(sch_id: str = FPath(...)):
    items = _load_schedules()
    new_items = [s for s in items if s.id != sch_id]
    if len(new_items) == len(items):
        raise HTTPException(status_code=404, detail="schedule id not found")
    _save_schedules(new_items)
    return {"ok": True, "deleted": sch_id}

@router.post("/schedule/tick")
def schedule_tick(now_iso: Optional[str] = Body(None, embed=True)):
    """
    Community edition: have system cron call this every minute.
    Enterprise: server can call internally on a loop.
    Runs due schedules and updates next_run_at/last_run_at. Applies retention.
    """
    now = _now_utc() if not now_iso else datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    ran: List[Dict[str, Any]] = []
    items = _load_schedules()
    changed = False
    for s in items:
        if not s.enabled:
            continue
        if not s.next_run_at:
            s.next_run_at = _compute_next_run(s, from_time=now).isoformat()
            changed = True
            continue
        next_run = datetime.fromisoformat(s.next_run_at.replace("Z", "+00:00"))
        if next_run <= now:
            debug("schedule due:", s.id, s.project, s.frequency, "@", s.next_run_at)
            req = BackupNowRequest(
                project=s.project,
                type=s.type,
                paranoid=False,
                notes=f"(scheduled {s.frequency})",
            )
            res = backup_now(req)
            s.last_run_at = now.replace(microsecond=0).isoformat()
            s.next_run_at = _compute_next_run(s, from_time=now + timedelta(seconds=1)).isoformat()
            changed = True
            ran.append({"schedule_id": s.id, "backup_id": res["backup_id"], "project": s.project})
            _apply_retention(s.project, s.retention_keep)
    if changed:
        _save_schedules(items)
    return {"ok": True, "ran": ran, "now": now.replace(microsecond=0).isoformat()}
