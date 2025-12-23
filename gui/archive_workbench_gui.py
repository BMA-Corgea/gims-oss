# api/archive_gui.py

from __future__ import annotations
from fastapi import APIRouter, HTTPException, Body, Query
from pathlib import Path
from typing import Any, Dict, List, Tuple, Iterable, Optional, Literal
import json
import sqlite3
import shutil
import os
import hashlib
from datetime import datetime
import contextlib
import re

# ------------------------------------------------------------------------------
# Debug control
# ------------------------------------------------------------------------------
DEBUG_ENABLED = False  # Change to True to enable debug logs

def debug(*args, **kwargs):
    """Debug print that respects DEBUG_ENABLED flag."""
    if DEBUG_ENABLED:
        print("[archive_workbench]", *args, **kwargs)

# ------------------------------------------------------------------------------
# Optional Postgres (psycopg v3)
# ------------------------------------------------------------------------------
try:
    import psycopg  # pip install psycopg[binary]
    from psycopg import errors as pg_errors
    _PSYCOPG_AVAILABLE = True
except Exception as e:
    _PSYCOPG_AVAILABLE = False
    pg_errors = None
    debug("psycopg not available:", repr(e))

# ------------------------------------------------------------------------------
# S3-aware helpers (match verb_gui behavior)
# ensure_prefix: create visible "folder" (prefix) in S3 or mkdir locally
# touch: create empty file (S3 object or local file)
# read_text/write_text: S3-aware text IO
# + NEW: list_projects, list_dirnames, move_prefix, delete_prefix, prefix_exists, project_exists
# NOTE: When unavailable, fall back to local FS.
# ------------------------------------------------------------------------------
try:
    from api import json_proxy  # import the module so we can call all helpers off it
    ensure_prefix = json_proxy.ensure_prefix
    touch = json_proxy.touch
    read_text = json_proxy.read_text
    write_text = json_proxy.write_text
    _HAS_S3 = True
except Exception:
    _HAS_S3 = False
    json_proxy = None  # type: ignore

    def ensure_prefix(path: Path) -> bool:
        path.mkdir(parents=True, exist_ok=True)
        return True

    def touch(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    def read_text(path: Path, encoding: str = "utf-8") -> str:
        return path.read_text(encoding=encoding)

    def write_text(path: Path, data: str, encoding: str = "utf-8") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding=encoding)

# ---- local fallbacks for the new json_proxy helpers --------------------------
def _jp_list_projects_fallback() -> List[str]:
    from api.manifest.resolver import resolve_path
    projects_root = resolve_path(Path(), "project_root")
    return sorted([p.name for p in projects_root.iterdir() if p.is_dir()])

def _jp_list_dirnames_fallback(path_str: str, include_hidden: bool = False) -> List[str]:
    p = Path(path_str)
    if not p.exists() or not p.is_dir():
        return []
    return sorted([d.name for d in p.iterdir() if d.is_dir() and (include_hidden or not d.name.startswith("."))])

def _jp_move_prefix_fallback(src: str, dst: str) -> None:
    # move files/dirs on local FS
    shutil.move(src, dst)

def _jp_delete_prefix_fallback(prefix: str) -> None:
    p = Path(prefix)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        try:
            os.remove(p.as_posix())
        except FileNotFoundError:
            pass

def _jp_prefix_exists_fallback(prefix: str) -> bool:
    return Path(prefix).exists()

def _jp_project_exists_fallback(project_name: str) -> bool:
    from api.manifest.resolver import resolve_path
    projects_root = resolve_path(Path(), "project_root")
    return (projects_root / project_name).exists()

# Small wrappers that call json_proxy if present, else local fallbacks
def _jp_list_projects() -> List[str]:
    if _HAS_S3 and hasattr(json_proxy, "list_projects"):
        return sorted(json_proxy.list_projects())
    return _jp_list_projects_fallback()

def _jp_list_dirnames(path_str: str, include_hidden: bool = False) -> List[str]:
    if _HAS_S3 and hasattr(json_proxy, "list_dirnames"):
        return sorted(json_proxy.list_dirnames(path_str, include_hidden=include_hidden))
    return _jp_list_dirnames_fallback(path_str, include_hidden)

def _jp_move_prefix(src: str, dst: str) -> None:
    if _HAS_S3 and hasattr(json_proxy, "move_prefix"):
        return json_proxy.move_prefix(src, dst)
    return _jp_move_prefix_fallback(src, dst)

def _jp_delete_prefix(prefix: str) -> None:
    if _HAS_S3 and hasattr(json_proxy, "delete_prefix"):
        return json_proxy.delete_prefix(prefix)
    return _jp_delete_prefix_fallback(prefix)

def _jp_prefix_exists(prefix: str) -> bool:
    if _HAS_S3 and hasattr(json_proxy, "prefix_exists"):
        return bool(json_proxy.prefix_exists(prefix))
    return _jp_prefix_exists_fallback(prefix)

def _jp_project_exists(project_name: str) -> bool:
    if _HAS_S3 and hasattr(json_proxy, "project_exists"):
        return bool(json_proxy.project_exists(project_name))
    return _jp_project_exists_fallback(project_name)

# ------------------------------------------------------------------------------
# Imports from your codebase
# ------------------------------------------------------------------------------
from api.manifest.resolver import resolve_path, get_db_uri  # project-aware paths + RDS DSNs
from api.i_o import (
    load_schema,
    get_verb_schema,
    get_verb_group_log_config,    # S3-aware (DB-first) log config load
    load_verb_group_log,          # S3-aware (DB-first) JSONL log loader
    io_list_projects,
)
from core.core_archive_workbench import (
    Plan, PlanStep, EnsureSoftColumns, EnsureArchiveTable, SQLStep, FileOp,
    plan_apply_archive_policy_for_nouns,
    plan_soft_archive_nouns, plan_hard_archive_nouns,
    plan_restore_nouns_soft, plan_restore_nouns_hard,
    plan_archive_runs_soft, plan_archive_runs_hard, plan_restore_runs
)

# ------------------------------------------------------------------------------
# Router
# ------------------------------------------------------------------------------
router = APIRouter(prefix="/api/archive_workbench", tags=["Archive_Workbench"])

# ------------------------------------------------------------------------------
# Backend selection (SQLite vs RDS Postgres)
# ------------------------------------------------------------------------------

def _normalize_for_psycopg(url: str) -> str:
    """Convert SQLAlchemy/async URIs to psycopg-compatible."""
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("postgresql+", 1)[1]
    if "?ssl=require" in url:
        url = url.replace("?ssl=require", "?sslmode=require")
    url = url.replace("postgresql://asyncpg://", "postgresql://")
    return url

def _effective_db_target(project_path: Path, key: str) -> Tuple[str, str]:
    """
    Returns (kind, target):
      - ("pg", DSN) if resolver returns a Postgres URI for the key
      - ("sqlite", /abs/path/to.db) otherwise
    Keys are 'object_sql_db' and 'archive_sql_db'.
    """
    try:
        uri = get_db_uri(key)
        debug(f"[dsn] {key} ->", uri)
    except Exception as e:
        debug(f"[dsn] {key} resolver failed:", repr(e))
        uri = None

    if uri and uri.startswith("postgresql"):
        return ("pg", _normalize_for_psycopg(uri))

    db_path = resolve_path(project_path, key)
    return ("sqlite", db_path.as_posix())

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
def _open_db(project_path: Path, key: str) -> _DBHandle:
    """
    Open a single DB (hot or archive) with autocommit OFF for PG; caller manages tx boundaries.
    Adds safe timeouts to avoid indefinite stalls.
    """
    kind, target = _effective_db_target(project_path, key)
    if kind == "pg" and _PSYCOPG_AVAILABLE:
        debug("[db] connect PG:", key, target)
        conn = psycopg.connect(target, autocommit=False)
        try:
            with conn.cursor() as cur:
                # Keep operations in the 'public' schema (harmless if public default)
                try:
                    cur.execute("SET search_path TO public;")
                except Exception:
                    pass
                # Avoid indefinite lock/wait hangs
                try:
                    cur.execute("SET lock_timeout = '5s';")
                    cur.execute("SET statement_timeout = '30s';")
                    cur.execute("SET idle_in_transaction_session_timeout = '60s';")
                except Exception as e:
                    debug("[db][pg] timeout SETs failed (non-fatal):", repr(e))
            yield _DBHandle("pg", conn)
        except Exception:
            # On exit/error: best-effort rollback (ignore if admin terminated)
            try:
                conn.rollback()
            except Exception as e:
                if not (pg_errors and isinstance(e, pg_errors.AdminShutdown)):
                    debug("[db][pg] rollback failed:", repr(e))
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return

    if kind == "pg" and not _PSYCOPG_AVAILABLE:
        debug("[db] psycopg missing; falling back to SQLite for", key)

    # SQLite fallback
    path = resolve_path(project_path, key)
    ensure_prefix(path.parent)  # S3/FS-safe parent creation
    debug("[db] connect SQLite:", key, path)
    conn = sqlite3.connect(path.as_posix(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield _DBHandle("sqlite", conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass

@contextlib.contextmanager
def _open_hot_and_arc(project_path: Path) -> Tuple[_DBHandle, _DBHandle]:
    """
    Open both hot (object_sql_db) and archive (archive_sql_db) DBs.
    Transaction boundaries are controlled by the executor.
    """
    with _open_db(project_path, "object_sql_db") as hot:
        with _open_db(project_path, "archive_sql_db") as arc:
            yield hot, arc

# ------------------------------------------------------------------------------
# Helpers: table names & columns (RDS-aware)
# ------------------------------------------------------------------------------

_SAN_RE = re.compile(r"[^0-9a-zA-Z_]")

def _sanitize_table_name(noun: str) -> str:
    base = _SAN_RE.sub("_", noun).strip("_")
    if not base or not base[0].isalpha():
        base = f"T_{base}"
    return f"noun_{base}"

def _prefixed(project: str, noun_table: str) -> str:
    """noun_Sample -> <Project>_noun_Sample"""
    base = noun_table
    if base.startswith("noun_"):
        base = base[len("noun_"):]
    return f"{project}_noun_{base}"

def _effective_noun_table_name(db: _DBHandle, project: str, noun_type: str) -> str:
    """
    In PG (RDS) we use prefixed tables: <Project>_noun_<Name>.
    In SQLite we keep legacy: noun_<Name>.
    """
    base = _sanitize_table_name(noun_type)
    return _prefixed(project, base) if db.kind == "pg" else base

# ------------------------------------------------------------------------------
# Generic DB metadata & execution helpers
# ------------------------------------------------------------------------------

def _db_table_exists(db: _DBHandle, table: str) -> bool:
    if db.kind == "sqlite":
        row = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        exists = bool(row)
        debug(f"[exists][sqlite] {table} -> {exists}")
        return exists
    with db.conn.cursor() as cur:
        try:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema='public' AND table_name=%s
                LIMIT 1
                """,
                (table,)
            )
            exists = cur.fetchone() is not None
            debug(f"[exists][pg] {table} -> {exists}")
            return exists
        except Exception as e:
            debug("[exists][pg] error:", repr(e))
            return False

def _db_columns_simple(db: _DBHandle, table: str) -> List[Tuple[str, str]]:
    """[(name, decl_type)]"""
    if not _db_table_exists(db, table):
        return []
    if db.kind == "sqlite":
        rows = db.conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        out = [(r["name"], (r["type"] or "TEXT")) for r in rows]
        debug("[cols][sqlite]", table, "->", out)
        return out
    with db.conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            ORDER BY ordinal_position
            """,
            (table,)
        )
        res = cur.fetchall()
        out = [(r[0], (r[1] or "").upper()) for r in res]
        debug("[cols][pg]", table, "->", out)
        return out

def _db_columns_full(db: _DBHandle, table: str) -> List[Dict[str, Any]]:
    """
    [{'name','type','notnull','dflt_value','pk'}]
    """
    if not _db_table_exists(db, table):
        return []
    if db.kind == "sqlite":
        rows = db.conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        out = []
        for r in rows:
            out.append({
                "name": r["name"],
                "type": r["type"] or "TEXT",
                "notnull": int(r["notnull"]),
                "dflt_value": r["dflt_value"],
                "pk": int(r["pk"]),
            })
        return out
    with db.conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              c.column_name AS name,
              c.data_type   AS type,
              CASE WHEN c.is_nullable='NO' THEN 1 ELSE 0 END AS notnull,
              c.column_default AS dflt_value,
              CASE WHEN tc.constraint_type='PRIMARY KEY' THEN 1 ELSE 0 END AS pk
            FROM information_schema.columns c
            LEFT JOIN information_schema.key_column_usage kcu
              ON kcu.table_schema = c.table_schema
             AND kcu.table_name   = c.table_name
             AND kcu.column_name  = c.column_name
            LEFT JOIN information_schema.table_constraints tc
              ON tc.table_schema = kcu.table_schema
             AND tc.table_name   = kcu.table_name
             AND tc.constraint_name = kcu.constraint_name
            WHERE c.table_schema='public' AND c.table_name=%s
            ORDER BY c.ordinal_position
            """,
            (table,)
        )
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
        out = [dict(zip(cols, r)) for r in rows]
        for x in out:
            x["type"] = (x.get("type") or "TEXT").upper()
            x["notnull"] = int(x.get("notnull") or 0)
            x["pk"] = int(x.get("pk") or 0)
        return out

def _db_count_rows(db: _DBHandle, table: str) -> int:
    if not _db_table_exists(db, table):
        return 0
    if db.kind == "sqlite":
        n = db.conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
        return int(n)
    with db.conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) AS n FROM "{table}"')
        n = cur.fetchone()[0] or 0
        return int(n)

def _compute_schema_hash_db(db: _DBHandle, table: str) -> str:
    cols = _db_columns_full(db, table)
    cols_sorted = sorted(cols, key=lambda x: x["name"])
    schema_str = json.dumps(cols_sorted, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(schema_str.encode("utf-8")).hexdigest()

def _convert_qmarks_to_pg(sql: str, param_count: int) -> str:
    """
    Naive converter: replace each '?' with '%s' up to param_count times.
    Assumes no '?' inside string literals in generated SQL (holds for our plans).
    """
    out = []
    replaced = 0
    for ch in sql:
        if ch == "?" and replaced < param_count:
            out.append("%s")
            replaced += 1
        else:
            out.append(ch)
    return "".join(out)

# ------------------------------------------------------------------------------
# Utilities (unchanged signatures; now S3-aware where applicable)
# ------------------------------------------------------------------------------

def _resolve_project_path(project_name: str) -> Path:
    debug("[_resolve_project_path] input:", project_name)
    projects_root = resolve_path(Path(), "project_root")
    candidate = projects_root / project_name
    debug("[_resolve_project_path] candidate:", candidate)
    # S3-aware existence check (falls back to FS)
    if not _jp_project_exists(project_name):
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")
    return candidate

def _diff_schemas(hot_cols_full: List[Dict[str, Any]], arc_cols_full: List[Dict[str, Any]]) -> Dict[str, Any]:
    hot_by = {c["name"]: c for c in hot_cols_full}
    arc_by = {c["name"]: c for c in arc_cols_full}
    hot_names = set(hot_by.keys())
    arc_names = set(arc_by.keys())

    added = sorted(list(hot_names - arc_names))
    removed = sorted(list(arc_names - hot_names))

    type_changes: List[Dict[str, Any]] = []
    for name in sorted(hot_names & arc_names):
        if (hot_by[name]["type"] or "").upper() != (arc_by[name]["type"] or "").upper():
            type_changes.append({
                "column": name,
                "from": (arc_by[name]["type"] or "TEXT"),
                "to": (hot_by[name]["type"] or "TEXT")
            })

    blocking_notnull: List[Dict[str, Any]] = []
    for name in added:
        c = hot_by[name]
        if int(c.get("notnull", 0)) == 1 and c.get("dflt_value") is None:
            blocking_notnull.append({"column": name, "type": c.get("type") or "TEXT", "dflt": None})

    return {
        "columns_added": added,
        "columns_removed": removed,
        "type_changes": type_changes,
        "blocking_notnull_columns": blocking_notnull,
    }

def _get_primary_field(noun_schema_entry: Dict[str, Any]) -> Optional[str]:
    pf = (noun_schema_entry or {}).get("primary_id_field")
    debug("[_get_primary_field] ->", pf)
    return pf

def _get_primary_field_or_first(db: _DBHandle, table: str, noun_schema_entry: Dict[str, Any]) -> Optional[str]:
    pf = _get_primary_field(noun_schema_entry)
    if pf:
        return pf
    cols = _db_columns_simple(db, table)
    return cols[0][0] if cols else None

def _ensure_legacy_column(db: _DBHandle, table: str):
    """
    Ensure __legacy_data TEXT exists on HOT table — used to stash archive-only columns.
    """
    if not _db_table_exists(db, table):
        return
    cols = [n for n, _ in _db_columns_simple(db, table)]
    if "__legacy_data" in cols:
        return
    if db.kind == "sqlite":
        db.conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "__legacy_data" TEXT')
    else:
        with db.conn.cursor() as cur:
            cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "__legacy_data" TEXT')

def _ordered_oldest_ids(db: _DBHandle, table: str, primary_field: str, date_field: Optional[str]) -> List[str]:
    if not _db_table_exists(db, table):
        return []

    use_date_field = False
    if date_field:
        cols = {name for name, _ in _db_columns_simple(db, table)}
        if date_field in cols:
            use_date_field = True
        else:
            debug(f"[_ordered_oldest_ids] warning: date_field '{date_field}' not found in '{table}', falling back to primary key sort.")

    try:
        if use_date_field:
            debug("[_ordered_oldest_ids] by date_field:", date_field)
            if db.kind == "sqlite":
                sql = f'SELECT "{primary_field}" AS pid FROM "{table}" WHERE "{primary_field}" IS NOT NULL ORDER BY "{date_field}" ASC, rowid ASC'
                rows = db.conn.execute(sql).fetchall()
                return [str(r["pid"]) for r in rows if r["pid"] is not None]
            else:
                sql = f'SELECT "{primary_field}" AS pid FROM "{table}" WHERE "{primary_field}" IS NOT NULL ORDER BY "{date_field}" ASC NULLS FIRST, "{primary_field}" ASC'
                with db.conn.cursor() as cur:
                    cur.execute(sql)
                    return [str(r[0]) for r in cur.fetchall() if r and r[0] is not None]
        else:
            debug("[_ordered_oldest_ids] fallback by primary/row surrogate")
            if db.kind == "sqlite":
                sql = f'SELECT "{primary_field}" AS pid FROM "{table}" WHERE "{primary_field}" IS NOT NULL ORDER BY rowid ASC'
                rows = db.conn.execute(sql).fetchall()
                return [str(r["pid"]) for r in rows if r["pid"] is not None]
            else:
                sql = f'SELECT "{primary_field}" AS pid FROM "{table}" WHERE "{primary_field}" IS NOT NULL ORDER BY "{primary_field}" ASC'
                with db.conn.cursor() as cur:
                    cur.execute(sql)
                    return [str(r[0]) for r in cur.fetchall() if r and r[0] is not None]
    except Exception as e:
        debug(f"[_ordered_oldest_ids] error during query execution for table '{table}':", e)
        try:
            debug("[_ordered_oldest_ids] attempting last resort primary key sort")
            if db.kind == "sqlite":
                sql = f'SELECT "{primary_field}" AS pid FROM "{table}" WHERE "{primary_field}" IS NOT NULL ORDER BY rowid ASC'
                rows = db.conn.execute(sql).fetchall()
                return [str(r["pid"]) for r in rows if r["pid"] is not None]
            else:
                sql = f'SELECT "{primary_field}" AS pid FROM "{table}" WHERE "{primary_field}" IS NOT NULL ORDER BY "{primary_field}" ASC'
                with db.conn.cursor() as cur:
                    cur.execute(sql)
                    return [str(r[0]) for r in cur.fetchall() if r and r[0] is not None]
        except Exception as final_e:
            debug(f"[_ordered_oldest_ids] last resort sort also failed for table '{table}':", final_e)
            return []

def _rows_for_age_eval(db: _DBHandle, table: str, primary_field: str, date_field: str) -> List[Dict[str, Any]]:
    if not _db_table_exists(db, table):
        return []
    cols = {name for name, _ in _db_columns_simple(db, table)}
    if date_field not in cols:
        debug(f"[_rows_for_age_eval] date_field '{date_field}' not found in '{table}', returning empty list.")
        return []
    try:
        if db.kind == "sqlite":
            sql = f'SELECT "{primary_field}" AS pid, "{date_field}" AS dt FROM "{table}" WHERE "{date_field}" IS NOT NULL AND "{primary_field}" IS NOT NULL'
            rows = db.conn.execute(sql).fetchall()
            out = [{"__id_field__": primary_field, primary_field: str(r["pid"]), date_field: r["dt"]} for r in rows if r["pid"] is not None]
            debug(f"[_rows_for_age_eval][sqlite] {table} ->", len(out))
            return out
        else:
            sql = f'SELECT "{primary_field}" AS pid, "{date_field}" AS dt FROM "{table}" WHERE "{date_field}" IS NOT NULL AND "{primary_field}" IS NOT NULL'
            with db.conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
                out = [{"__id_field__": primary_field, primary_field: str(r[0]), date_field: r[1]} for r in rows if r and r[0] is not None]
                debug(f"[_rows_for_age_eval][pg] {table} ->", len(out))
                return out
    except Exception as e:
        debug(f"[_rows_for_age_eval] error during query execution for table '{table}':", e)
        return []

def _load_policy(project_path: Path) -> Dict[str, Any]:
    policy_path = resolve_path(project_path, "archive_policy")
    debug("[_load_policy] path:", policy_path)
    try:
        payload = read_text(policy_path, encoding="utf-8")  # S3-aware
    except FileNotFoundError:
        debug("[_load_policy] missing; using empty {}")
        return {}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid archive_policy.json: {e}")
    try:
        data = json.loads(payload)
        debug("[_load_policy] loaded keys:", list(data.keys()))
        return data
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid archive_policy.json: {e}")

def _serialize_plan(plan: Plan) -> Dict[str, Any]:
    def step_to_dict(s: PlanStep) -> Dict[str, Any]:
        if isinstance(s, EnsureSoftColumns):
            return {"type": "EnsureSoftColumns", "target": s.target, "table": s.table}
        if isinstance(s, EnsureArchiveTable):
            return {
                "type": "EnsureArchiveTable",
                "source_target": s.source_target,
                "source_table": s.source_table,
                "dest_target": s.dest_target,
                "dest_table": s.dest_table,
                "columns": s.columns,
                "include_meta": s.include_meta,
            }
        if isinstance(s, SQLStep):
            return {"type": "SQLStep", "target": s.target, "sql": s.sql, "params": list(s.params)}
        if isinstance(s, FileOp):
            return {"type": "FileOp", "op": s.op, "src": s.src, "dst": s.dst, "text": s.text}
        return {"type": type(s).__name__, "repr": repr(s)}
    out = {"description": plan.description, "meta": plan.meta, "steps": [step_to_dict(s) for s in plan.steps]}
    debug("[_serialize_plan] steps:", len(out["steps"]))
    return out

# ------------------------------------------------------------------------------
# Plan executor (DB + FS)  — RDS-aware
# ------------------------------------------------------------------------------

class _ExecContext:
    def __init__(self, hot: _DBHandle, arc: _DBHandle):
        self.hot = hot
        self.arc = arc
        self.last_select_row: Optional[Dict[str, Any]] = None  # dict with column names

def _ensure_soft_columns(ctx: _ExecContext, table: str):
    debug("[_ensure_soft_columns] table:", table)
    if not _db_table_exists(ctx.hot, table):
        debug("[_ensure_soft_columns] table missing; skipping:", table)
        return
    cols = [c[0] for c in _db_columns_simple(ctx.hot, table)]
    if "archived" not in cols:
        if ctx.hot.kind == "sqlite":
            ctx.hot.conn.execute(f'ALTER TABLE "{table}" ADD COLUMN archived INTEGER DEFAULT 0')
        else:
            with ctx.hot.conn.cursor() as cur:
                cur.execute(f'ALTER TABLE "{table}" ADD COLUMN archived INTEGER DEFAULT 0')
        debug("[_ensure_soft_columns] added 'archived'")
    if "archived_at" not in cols:
        if ctx.hot.kind == "sqlite":
            ctx.hot.conn.execute(f'ALTER TABLE "{table}" ADD COLUMN archived_at TEXT')
        else:
            with ctx.hot.conn.cursor() as cur:
                cur.execute(f'ALTER TABLE "{table}" ADD COLUMN archived_at TEXT')
        debug("[_ensure_soft_columns] added 'archived_at'")

def _ensure_aux_archive_tables(db: _DBHandle):
    """
    Ensure archive-side index tables exist (idempotent), add schema_hash to noun_archive_index
    if missing, and ensure a 'project' column exists on both index tables.
    Uses session timeouts already set on connection to avoid indefinite hangs.
    """
    debug(f"[_ensure_aux_archive_tables] START: Ensuring index tables on {db.kind} DB")
    
    ddl1 = """
        CREATE TABLE IF NOT EXISTS noun_archive_index (
            project      TEXT,
            noun_type    TEXT,
            primary_id   TEXT,
            table_name   TEXT,
            archived_at  TEXT,
            strategy     TEXT,
            notes        TEXT,
            schema_hash  TEXT
        )
    """
    ddl2 = """
        CREATE TABLE IF NOT EXISTS runs_archive_index (
            project      TEXT,
            run_id       TEXT,
            verb         TEXT,
            verb_group   TEXT,
            archive_path TEXT,
            archived_at  TEXT,
            strategy     TEXT,
            notes        TEXT
        )
    """
    ddl3 = 'ALTER TABLE "noun_archive_index" ADD COLUMN "schema_hash" TEXT'
    
    if db.kind == "sqlite":
        try:
            debug("[_ensure_aux_archive_tables][sqlite] Executing DDL1 (noun_archive_index)")
            db.conn.execute(ddl1)
            debug("[_ensure_aux_archive_tables][sqlite] Executing DDL2 (runs_archive_index)")
            db.conn.execute(ddl2)
            cols = db.conn.execute('PRAGMA table_info("noun_archive_index")').fetchall()
            have = {r["name"] for r in cols}
            if "schema_hash" not in have:
                debug("[_ensure_aux_archive_tables][sqlite] Adding schema_hash column. DDL3:", ddl3)
                db.conn.execute(ddl3)
            else:
                debug("[_ensure_aux_archive_tables][sqlite] schema_hash column already exists.")
            for tname in ("noun_archive_index", "runs_archive_index"):
                cols = db.conn.execute(f'PRAGMA table_info("{tname}")').fetchall()
                have = {r["name"] for r in cols}
                if "project" not in have:
                    db.conn.execute(f'ALTER TABLE "{tname}" ADD COLUMN "project" TEXT')
                    debug(f"[_ensure_aux_archive_tables][sqlite] Added project column to {tname}")
                else:
                    debug(f"[_ensure_aux_archive_tables][sqlite] project column already exists on {tname}")
        except Exception as e:
            debug(f"[_ensure_aux_archive_tables][sqlite] !!! EXECUTION FAILED !!! ERROR: {e}")
            raise e
        debug("[_ensure_aux_archive_tables][sqlite] FINISHED")
        return

    try:
        with db.conn.cursor() as cur:
            try:
                cur.execute("SET lock_timeout = '3s';")
            except Exception:
                pass
            try:
                debug("[_ensure_aux_archive_tables][pg] Executing DDL1 (noun_archive_index)")
                cur.execute(ddl1)
            except psycopg.errors.LockNotAvailable:
                debug("[_ensure_aux_archive_tables][pg] DDL1 skipped: lock timeout (already exists or locked)")
            except Exception as e:
                debug("[_ensure_aux_archive_tables][pg] DDL1 error:", e)

            try:
                debug("[_ensure_aux_archive_tables][pg] Executing DDL2 (runs_archive_index)")
                cur.execute(ddl2)
            except psycopg.errors.LockNotAvailable:
                debug("[_ensure_aux_archive_tables][pg] DDL2 skipped: lock timeout (already exists or locked)")
            except Exception as e:
                debug("[_ensure_aux_archive_tables][pg] DDL2 error:", e)

            debug("[_ensure_aux_archive_tables][pg] Checking existing columns for noun_archive_index")
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name='noun_archive_index'
                """
            )
            have_noun = {r[0] for r in cur.fetchall()}
            debug(f"[_ensure_aux_archive_tables][pg] Existing columns (noun_archive_index): {have_noun}")

            if "schema_hash" not in have_noun:
                try:
                    debug("[_ensure_aux_archive_tables][pg] Adding schema_hash column. DDL3:", ddl3)
                    cur.execute(ddl3)
                except psycopg.errors.DuplicateColumn:
                    debug("[_ensure_aux_archive_tables][pg] schema_hash already exists (race condition)")
                except psycopg.errors.LockNotAvailable:
                    debug("[_ensure_aux_archive_tables][pg] DDL3 skipped: lock timeout (already locked)")
                except Exception as e:
                    debug("[_ensure_aux_archive_tables][pg] DDL3 error:", e)
            else:
                debug("[_ensure_aux_archive_tables][pg] schema_hash column already exists.")

            for tname in ("noun_archive_index", "runs_archive_index"):
                debug(f"[_ensure_aux_archive_tables][pg] Checking for project column on {tname}")
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=%s
                    """,
                    (tname,),
                )
                have_cols = {r[0] for r in cur.fetchall()}
                if "project" not in have_cols:
                    try:
                        cur.execute(f'ALTER TABLE "{tname}" ADD COLUMN "project" TEXT')
                        debug(f"[_ensure_aux_archive_tables][pg] Added project column to {tname}")
                    except psycopg.errors.DuplicateColumn:
                        debug(f"[_ensure_aux_archive_tables][pg] project already exists on {tname}")
                    except psycopg.errors.LockNotAvailable:
                        debug(f"[_ensure_aux_archive_tables][pg] project add skipped due to lock on {tname}")
                    except Exception as e:
                        debug(f"[_ensure_aux_archive_tables][pg] project column add failed ({tname}):", e)
                else:
                    debug(f"[_ensure_aux_archive_tables][pg] project column already exists on {tname}")

    except Exception as e:
        debug(f"[_ensure_aux_archive_tables][pg] !!! EXECUTION FAILED !!! ERROR: {e}")
        raise

def _exec_sql_step(ctx: _ExecContext, step: SQLStep):
    """
    Execute a single SQLStep on either the hot or archive DB.
    Automatically adapts SQLite SQL to PostgreSQL syntax and
    rewrites INSERT OR REPLACE for compatibility.

    Additionally, suppresses legacy inserts into runs_archive_index and
    noun_archive_index (we write those canonically elsewhere).
    """
    debug("[_exec_sql_step] target:", step.target)
    db = ctx.hot if step.target == "hot" else ctx.arc
    sql = step.sql
    params = list(step.params or [])

    if ctx.last_select_row and any(
        isinstance(x, str) and x.startswith("<") and x.endswith(">")
        for x in params
    ):
        debug("[_exec_sql_step] param substitution from last_select_row")
        for i, p in enumerate(params):
            if isinstance(p, str) and p.startswith("<") and p.endswith(">"):
                key = p[1:-1]
                params[i] = ctx.last_select_row.get(key)

    debug("[_exec_sql_step] SQL:", sql)
    debug("[_exec_sql_step] params:", params)

    try:
        sql_l = sql.strip().lower()
        if sql_l.startswith("insert") and "runs_archive_index" in sql_l:
            m_runs = re.search(
                r'insert\s+(?:or\s+replace\s+)?into\s+"?runs_archive_index"?\s*\(([^)]*)\)',
                sql_l,
                re.IGNORECASE | re.DOTALL,
            )
            if m_runs:
                cols_lc = [c.strip().strip('"').strip().lower() for c in m_runs.group(1).split(",") if c.strip()]
                if "project" not in cols_lc and "verb" not in cols_lc:
                    debug("[_exec_sql_step] SKIP legacy runs_archive_index insert without project/verb (handled canonically later)")
                    return
            else:
                debug("[_exec_sql_step] SKIP bare runs_archive_index insert (no column list detected)")
                return
    except Exception as e:
        debug("[_exec_sql_step] runs_archive_index suppression check failed (non-fatal):", repr(e))

    try:
        sql_l = sql.strip().lower()
        if sql_l.startswith("insert"):
            if re.search(
                r'insert\s+(?:or\s+replace\s+)?into\s+"?noun_archive_index"?\s*\(',
                sql_l,
                re.IGNORECASE | re.DOTALL,
            ):
                debug("[_exec_sql_step] SKIP plan-driven noun_archive_index insert (canonical writer handles it)")
                return
    except Exception as e:
        debug("[_exec_sql_step] noun_archive_index suppression check failed (non-fatal):", repr(e))

    if db.kind == "sqlite":
        cur = db.conn.execute(sql, tuple(params))
        if sql.strip().lower().startswith("select"):
            row = cur.fetchone()
            ctx.last_select_row = dict(row) if row else None
            debug(
                "[_exec_sql_step] last_select_row keys:",
                list(ctx.last_select_row.keys()) if ctx.last_select_row else None,
            )
        return

    import re as _re, psycopg as _psycopg

    sql_pg = _convert_qmarks_to_pg(sql, len(params))
    sql_pg_stripped = sql_pg.strip().lower()

    if sql_pg_stripped.startswith("insert or replace"):
        debug("[_exec_sql_step][pg] Detected 'INSERT OR REPLACE' — rewriting for Postgres")
        m = _re.search(
            r'insert\s+or\s+replace\s+into\s+\"?([\w\-\_\.]+)\"?\s*\((.*?)\)\s*values',
            sql_pg,
            _re.IGNORECASE | _re.DOTALL,
        )
        if m:
            table_name = m.group(1)
            cols_str = m.group(2)
            cols = [c.strip().strip('"') for c in cols_str.split(",") if c.strip()]
            if not cols:
                raise ValueError(f"[_exec_sql_step][pg] could not extract column list: {sql_pg}")

            pk = cols[0]
            updates = ", ".join([f'"{c}" = EXCLUDED."{c}"' for c in cols if c != pk])
            has_rowid = any(c.lower() == "_rowid" for c in cols)
            values_clause = "OVERRIDING SYSTEM VALUE VALUES" if has_rowid else "VALUES"

            sql_pg = (
                f'INSERT INTO "{table_name}" ({cols_str}) {values_clause} ('
                + ", ".join(["%s"] * len(cols))
                + f') ON CONFLICT ("{pk}") DO UPDATE SET {updates}'
            )
            debug("[_exec_sql_step][pg] Rewritten INSERT OR REPLACE → ON CONFLICT:")
            debug(sql_pg)

    debug(f"[_exec_sql_step][pg] TRANSLATED SQL: {sql_pg}")
    debug(f"[_exec_sql_step][pg] PARAMS: {tuple(params)}")

    try:
        with db.conn.cursor() as cur:
            cur.execute(sql_pg, tuple(params))
            if sql.strip().lower().startswith("select"):
                desc = cur.description
                row = cur.fetchone()
                if row and desc:
                    cols = [d.name for d in desc]
                    ctx.last_select_row = dict(zip(cols, row))
                else:
                    ctx.last_select_row = None
                debug(
                    "[_exec_sql_step][pg] last_select_row keys:",
                    list(ctx.last_select_row.keys()) if ctx.last_select_row else None,
                )
    except Exception as e:
        debug(f"[_exec_sql_step][pg] !!! EXECUTION FAILED !!!")
        debug(f"[_exec_sql_step][pg] ERROR: {e}")
        debug(f"[_exec_sql_step][pg] FAILED SQL: {sql_pg}")
        debug(f"[_exec_sql_step][pg] FAILED PARAMS: {tuple(params)}")
        raise e


def _exec_file_op(step: FileOp):
    """
    FileOp executor:
      - mkdir_p: S3/FS-safe via ensure_prefix
      - write_text: S3-aware write_text
      - move/delete: now S3-aware via json_proxy (copy+delete / object deletes) with FS fallback
    """
    debug("[_exec_file_op] op:", step.op, "src:", step.src, "dst:", step.dst)
    if step.op == "mkdir_p" and step.dst:
        ensure_prefix(Path(step.dst))  # S3-visible prefix or local mkdir -p
    elif step.op == "move" and step.src and step.dst:
        _jp_move_prefix(step.src, step.dst)  # S3-safe (copy+delete) or FS move
    elif step.op == "write_text" and step.dst is not None:
        p = Path(step.dst)
        ensure_prefix(p.parent)
        write_text(p, step.text or "", encoding="utf-8")  # S3-aware
    elif step.op == "delete" and step.src:
        _jp_delete_prefix(step.src)  # S3-safe (delete objects) or FS delete

def _safe_commit(conn):
    try:
        conn.commit()
    except Exception as e:
        if not (pg_errors and isinstance(e, pg_errors.AdminShutdown)):
            debug("[commit] failed:", repr(e))
            raise

def _safe_rollback(conn):
    try:
        conn.rollback()
    except Exception as e:
        if not (pg_errors and isinstance(e, pg_errors.AdminShutdown)):
            debug("[rollback] failed:", repr(e))

def _execute_plan(project_path: Path, plan: Plan) -> Dict[str, Any]:
    debug("[_execute_plan] description:", plan.description, "steps:", len(plan.steps))
    with _open_hot_and_arc(project_path) as (hot, arc):
        _ensure_aux_archive_tables(arc)

        ctx = _ExecContext(hot, arc)

        if hot.kind == "sqlite":
            hot.conn.execute("BEGIN")
        if arc.kind == "sqlite":
            arc.conn.execute("BEGIN")

        try:
            for s in plan.steps:
                if isinstance(s, EnsureSoftColumns):
                    _ensure_soft_columns(ctx, s.table)
                elif isinstance(s, EnsureArchiveTable):
                    _ensure_archive_table(ctx, s.source_table, s.dest_table, s.columns, s.include_meta)
                elif isinstance(s, SQLStep):
                    _exec_sql_step(ctx, s)
                elif isinstance(s, FileOp):
                    if hot.kind == "sqlite":
                        hot.conn.commit()
                    else:
                        _safe_commit(hot.conn)
                    if arc.kind == "sqlite":
                        arc.conn.commit()
                    else:
                        _safe_commit(arc.conn)
                    _exec_file_op(s)
                    if hot.kind == "sqlite":
                        hot.conn.execute("BEGIN")
                    if arc.kind == "sqlite":
                        arc.conn.execute("BEGIN")
                else:
                    debug("[_execute_plan] ! unknown step:", type(s).__name__)

            if hot.kind == "sqlite":
                hot.conn.commit()
            else:
                _safe_commit(hot.conn)
            if arc.kind == "sqlite":
                arc.conn.commit()
            else:
                _safe_commit(arc.conn)

            debug("[_execute_plan] commit complete")
            return {"ok": True, "description": plan.description, "meta": plan.meta}

        except Exception as e:
            if hot.kind == "sqlite":
                try:
                    hot.conn.rollback()
                except Exception as e2:
                    debug("[_execute_plan] hot rollback failed:", repr(e2))
            else:
                _safe_rollback(hot.conn)

            if arc.kind == "sqlite":
                try:
                    arc.conn.rollback()
                except Exception as e2:
                    debug("[_execute_plan] arc rollback failed:", repr(e2))
            else:
                _safe_rollback(arc.conn)

            debug("[_execute_plan] X rollback due to:", e)
            raise HTTPException(status_code=500, detail=str(e))

# ------------------------------------------------------------------------------
# JSON helpers (S3-aware)
# ------------------------------------------------------------------------------

def _load_json_file(p: Path):
    debug("[_load_json_file]", p)
    try:
        payload = read_text(p, encoding="utf-8")  # S3-aware
        return json.loads(payload)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {p}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in {p.name}: {e}")

def _resolve_schema_path(project: str, key: str, fallback_name: str) -> Path:
    root = _resolve_project_path(project)
    try:
        p = resolve_path(root, key)
        p = Path(p)
    except Exception:
        p = root / fallback_name
    return p

def _extract_noun_types(schema: object) -> list[str]:
    if isinstance(schema, dict):
        return [k for k in schema.keys() if isinstance(k, str)]
    if isinstance(schema, list):
        out = []
        for item in schema:
            if isinstance(item, dict):
                v = item.get("noun_type") or item.get("name")
                if isinstance(v, str):
                    out.append(v)
        return out
    return []

def _extract_verb_types(schema: object) -> list[str]:
    names: set[str] = set()
    if isinstance(schema, dict):
        if any(isinstance(v, dict) and ("verb_name" in v or "verb_group" in v or v) for v in schema.values()):
            names.update([k for k in schema.keys() if isinstance(k, str)])
        for v in schema.values():
            if isinstance(v, dict):
                for k2 in v.keys():
                    if isinstance(k2, str):
                        names.add(k2)
    return sorted(names)

# ------------------------------------------------------------------------------
# Noun linkage & scanning (RDS-aware)
# ------------------------------------------------------------------------------

def _split_noun_ids_by_actual_archive(project_path: Path, noun_type: str, ids: List[str]) -> Tuple[List[str], List[str]]:
    """
    Return (soft_ids, hard_ids):
      - soft: nouns HOT DB has archived=1
      - hard: archive DB has the row
    """
    if not ids:
        return [], []
    project = project_path.name
    noun_schema = load_schema(project_path, "noun") or {}

    with _open_hot_and_arc(project_path) as (hot, arc):
        entry = (noun_schema or {}).get(noun_type, {}) if isinstance(noun_schema, dict) else {}
        table_hot = _effective_noun_table_name(hot, project, noun_type)
        table_arc = _effective_noun_table_name(arc, project, noun_type)
        pf = _get_primary_field_or_first(hot, table_hot, entry or {})
        if not pf:
            return [], []

        soft_set: set[str] = set()
        hard_set: set[str] = set()

        if _db_table_exists(hot, table_hot):
            cols = [name for name, _ in _db_columns_simple(hot, table_hot)]
            if "archived" in cols:
                if hot.kind == "sqlite":
                    ph = ",".join("?" * len(ids))
                    try:
                        rows = hot.conn.execute(
                            f'SELECT "{pf}" AS pid FROM "{table_hot}" WHERE archived=1 AND "{pf}" IN ({ph})',
                            tuple(ids)
                        ).fetchall()
                        soft_set = {str(r["pid"]) for r in rows if r["pid"] is not None}
                    except Exception:
                        pass
                else:
                    with hot.conn.cursor() as cur:
                        ph = ",".join(["%s"] * len(ids))
                        cur.execute(
                            f'SELECT "{pf}" AS pid FROM "{table_hot}" WHERE archived=1 AND "{pf}" IN ({ph})',
                            tuple(ids)
                        )
                        soft_set = {str(r[0]) for r in cur.fetchall() if r and r[0] is not None}

        if _db_table_exists(arc, table_arc):
            if arc.kind == "sqlite":
                ph = ",".join("?" * len(ids))
                rows = arc.conn.execute(
                    f'SELECT "{pf}" AS pid FROM "{table_arc}" WHERE "{pf}" IN ({ph})',
                    tuple(ids)
                ).fetchall()
                hard_set = {str(r["pid"]) for r in rows if r["pid"] is not None}
            else:
                with arc.conn.cursor() as cur:
                    ph = ",".join(["%s"] * len(ids))
                    cur.execute(
                        f'SELECT "{pf}" AS pid FROM "{table_arc}" WHERE "{pf}" IN ({ph})',
                        tuple(ids)
                    )
                    hard_set = {str(r[0]) for r in cur.fetchall() if r and r[0] is not None}

        soft_ids = [i for i in ids if i in soft_set]
        hard_ids = [i for i in ids if i in hard_set and i not in soft_set]
        return soft_ids, hard_ids

def _collect_linked_noun_ids(project_path: Path, test_type: Optional[str], run_id: str) -> Tuple[Optional[str], List[str]]:
    """
    Finds linked nouns based on verb schema hint.
    Safely skips if the hinted table does not have a run ID column.
    """
    if not test_type:
        return None, []
    try:
        vs = get_verb_schema(project_path, test_type) or {}
    except Exception:
        vs = {}
    noun_type = ((vs.get("data_entry_schema") or {}).get("set_up_inputs") or {}).get("noun_type_ref")
    if not noun_type:
        return None, []

    project = project_path.name
    with _open_db(project_path, "object_sql_db") as hot:
        table = _effective_noun_table_name(hot, project, noun_type)
        if not _db_table_exists(hot, table):
            return noun_type, []

        noun_schema = load_schema(project_path, "noun") or {}
        entry = noun_schema.get(noun_type) or {}
        pf = _get_primary_field_or_first(hot, table, entry)
        if not pf:
            return noun_type, []

        cols_hot_names = {c for c, _ in _db_columns_simple(hot, table)}
        run_id_col = "_run_ID" if "_run_ID" in cols_hot_names else ("_runID" if "_runID" in cols_hot_names else None)
        if not run_id_col:
            debug(f"[_collect_linked_noun_ids] skip {noun_type}: no run ID column found in hinted table {table}")
            return noun_type, []
        
        debug(f"[_collect_linked_noun_ids] querying hinted table {table} using col '{run_id_col}'")
        if hot.kind == "sqlite":
            rows = hot.conn.execute(
                f'SELECT "{pf}" AS pid FROM "{table}" WHERE "{run_id_col}"=?', (run_id,)
            ).fetchall()
            ids = [str(r["pid"]) for r in rows if r["pid"] is not None]
        else:
            with hot.conn.cursor() as cur:
                cur.execute(f'SELECT "{pf}" AS pid FROM "{table}" WHERE "{run_id_col}"=%s', (run_id,))
                ids = [str(r[0]) for r in cur.fetchall() if r and r[0] is not None]
        
        return noun_type, ids

def _collect_linked_noun_ids_by_scan(project_path: Path, run_id: str) -> Dict[str, List[str]]:
    """
    Brute-force scan all noun tables for rows with a run ID column == run_id
    across HOT and ARCHIVE DBs.
    Safely skips tables that do not have a run ID column.
    Returns {noun_type: [ids...]} with IDs deduped and sorted.
    """
    debug("[_collect_linked_noun_ids_by_scan] run_id:", run_id)
    out: Dict[str, List[str]] = {}
    project = project_path.name

    noun_schema = load_schema(project_path, "noun") or {}
    noun_items = list((noun_schema or {}).items())

    with _open_hot_and_arc(project_path) as (hot, arc):
        for noun_type, entry in noun_items:
            table_hot = _effective_noun_table_name(hot, project, noun_type)
            table_arc = _effective_noun_table_name(arc, project, noun_type)

            pf = _get_primary_field(entry)
            if not pf:
                if _db_table_exists(hot, table_hot):
                    pf = _get_primary_field_or_first(hot, table_hot, entry or {})
                if not pf and _db_table_exists(arc, table_arc):
                    cols_arc = _db_columns_simple(arc, table_arc)
                    pf = cols_arc[0][0] if cols_arc else None
            if not pf:
                debug(f"[_collect_linked_noun_ids_by_scan] skip {noun_type}: no primary field")
                continue

            ids_set: set[str] = set()

            if _db_table_exists(hot, table_hot):
                cols_hot_names = {c for c, _ in _db_columns_simple(hot, table_hot)}
                run_id_col = "_run_ID" if "_run_ID" in cols_hot_names else ("_runID" if "_runID" in cols_hot_names else None)
                if run_id_col:
                    debug(f"[_collect_linked_noun_ids_by_scan] scanning hot {table_hot} using col '{run_id_col}'")
                    if hot.kind == "sqlite":
                        rows = hot.conn.execute(
                            f'SELECT "{pf}" AS pid FROM "{table_hot}" WHERE "{run_id_col}"=?', (run_id,)
                        ).fetchall()
                        for r in rows:
                            if r["pid"] is not None:
                                ids_set.add(str(r["pid"]))
                    else:
                        with hot.conn.cursor() as cur:
                            cur.execute(
                                f'SELECT "{pf}" AS pid FROM "{table_hot}" WHERE "{run_id_col}"=%s', (run_id,)
                            )
                            for r in cur.fetchall():
                                if r and r[0] is not None:
                                    ids_set.add(str(r[0]))
                else:
                    debug(f"[_collect_linked_noun_ids_by_scan] skip scan hot {table_hot}: no run ID column found")

            if _db_table_exists(arc, table_arc):
                cols_arc_names = {c for c, _ in _db_columns_simple(arc, table_arc)}
                run_id_col = "_run_ID" if "_run_ID" in cols_arc_names else ("_runID" if "_runID" in cols_arc_names else None)
                if run_id_col:
                    debug(f"[_collect_linked_noun_ids_by_scan] scanning archive {table_arc} using col '{run_id_col}'")
                    if arc.kind == "sqlite":
                        rows = arc.conn.execute(
                            f'SELECT "{pf}" AS pid FROM "{table_arc}" WHERE "{run_id_col}"=?', (run_id,)
                        ).fetchall()
                        for r in rows:
                            if r["pid"] is not None:
                                ids_set.add(str(r["pid"]))
                    else:
                        with arc.conn.cursor() as cur:
                            cur.execute(
                                f'SELECT "{pf}" AS pid FROM "{table_arc}" WHERE "{run_id_col}"=%s', (run_id,)
                            )
                            for r in cur.fetchall():
                                if r and r[0] is not None:
                                    ids_set.add(str(r[0]))
                else:
                    debug(f"[_collect_linked_noun_ids_by_scan] skip scan archive {table_arc}: no run ID column found")

            if ids_set:
                out[noun_type] = sorted(ids_set)

    return out

def _list_dirs(p: Path) -> list[str]:
    """
    S3-aware directory listing using json_proxy (prefix listing),
    falling back to local FS when json_proxy is unavailable.
    """
    return _jp_list_dirnames(p.as_posix(), include_hidden=False)

# ----------------------- Schema index & legacy (RDS-aware) --------------------

ARCHIVE_OP_TIMEOUT_SEC = 90.0

def _insert_noun_archive_index_rows(
    arc: _DBHandle,
    project: str,
    noun_type: str,
    table: str,
    primary_field: str,
    ids: List[str],
    strategy: str,
    schema_hash: str
):
    if not ids:
        return
    _ensure_aux_archive_tables(arc)
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    debug(f"[_insert_noun_archive_index_rows] Inserting {len(ids)} rows with project='{project}', noun_type='{noun_type}', strategy='{strategy}'")
    if arc.kind == "sqlite":
        for pid in ids:
            try:
                arc.conn.execute(
                    'INSERT INTO noun_archive_index (project, noun_type, primary_id, table_name, archived_at, strategy, notes, schema_hash) VALUES (?,?,?,?,?,?,?,?)',
                    (project, noun_type, str(pid), table, now, strategy, None, schema_hash)
                )
                debug(f"[_insert_noun_archive_index_rows][sqlite] Inserted row for pid={pid}")
            except Exception as e:
                debug(f"[_insert_noun_archive_index_rows][sqlite] ERROR inserting pid={pid}: {e}")
                raise
        return
    with arc.conn.cursor() as cur:
        for pid in ids:
            try:
                cur.execute(
                    'INSERT INTO noun_archive_index (project, noun_type, primary_id, table_name, archived_at, strategy, notes, schema_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                    (project, noun_type, str(pid), table, now, strategy, None, schema_hash)
                )
                debug(f"[_insert_noun_archive_index_rows][pg] Inserted row for pid={pid}")
            except Exception as e:
                debug(f"[_insert_noun_archive_index_rows][pg] ERROR inserting pid={pid}: {e}")
                debug(f"[_insert_noun_archive_index_rows][pg] Values: project={project}, noun_type={noun_type}, table={table}, strategy={strategy}")
                raise

def _runs_index_row_exists(
    arc: _DBHandle,
    project: str,
    run_id: str,
    strategy: str,
    archive_path: Optional[str],
) -> bool:
    if arc.kind == "sqlite":
        row = arc.conn.execute(
            '''
            SELECT 1
            FROM runs_archive_index
            WHERE run_id = ?
              AND strategy = ?
              AND COALESCE(archive_path, '') = COALESCE(?, '')
            LIMIT 1
            ''',
            (run_id, strategy, archive_path)
        ).fetchone()
        return row is not None
    with arc.conn.cursor() as cur:
        cur.execute(
            '''
            SELECT 1
            FROM runs_archive_index
            WHERE run_id = %s
              AND strategy = %s
              AND archive_path IS NOT DISTINCT FROM %s
            LIMIT 1
            ''',
            (run_id, strategy, archive_path)
        )
        return cur.fetchone() is not None

def _promote_legacy_runs_index_row_project(
    arc: _DBHandle,
    project: str,
    run_id: str,
    verb: Optional[str],
    verb_group: Optional[str],
    archive_path: Optional[str],
    strategy: str,
) -> int:
    if arc.kind == "sqlite":
        cur = arc.conn.execute(
            '''
            UPDATE runs_archive_index
               SET project = ?,
                   verb = COALESCE(verb, ?),
                   verb_group = COALESCE(verb_group, ?)
             WHERE run_id = ?
               AND strategy = ?
               AND COALESCE(archive_path, '') = COALESCE(?, '')
               AND project IS NULL
            ''',
            (project, verb, verb_group, run_id, strategy, archive_path)
        )
        return cur.rowcount or 0
    with arc.conn.cursor() as cur:
        cur.execute(
            '''
            UPDATE runs_archive_index
               SET project = %s,
                   verb = COALESCE(verb, %s),
                   verb_group = COALESCE(verb_group, %s)
             WHERE run_id = %s
               AND strategy = %s
               AND archive_path IS NOT DISTINCT FROM %s
               AND project IS NULL
            ''',
            (project, verb, verb_group, run_id, strategy, archive_path)
        )
        return cur.rowcount or 0

def _insert_runs_archive_index_row(
    arc: _DBHandle,
    project: str,
    run_id: str,
    verb: Optional[str],
    verb_group: Optional[str],
    archive_path: Optional[str],
    strategy: str
):
    _ensure_aux_archive_tables(arc)
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    try:
        upgraded = _promote_legacy_runs_index_row_project(
            arc=arc,
            project=project,
            run_id=run_id,
            verb=verb,
            verb_group=verb_group,
            archive_path=archive_path,
            strategy=strategy,
        )
        if upgraded:
            debug("[runs_index] upgraded legacy row with missing project:", project, run_id, strategy, archive_path)
            return
    except Exception as e:
        debug("[runs_index] legacy upgrade attempt failed (non-fatal):", repr(e))

    if _runs_index_row_exists(arc, project, run_id, strategy, archive_path):
        debug("[runs_index] skip duplicate:", project, run_id, strategy, archive_path)
        return

    if arc.kind == "sqlite":
        cols = arc.conn.execute('PRAGMA table_info("runs_archive_index")').fetchall()
        col_names = [r["name"] for r in cols]
    else:
        with arc.conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema='public'
                   AND table_name='runs_archive_index'
                 ORDER BY ordinal_position
                """
            )
            col_names = [r[0] for r in cur.fetchall()]

    debug(f"[_insert_runs_archive_index_row] Column order: {col_names}")

    values_dict = {
        "project": project,
        "run_id": run_id,
        "verb": verb,
        "verb_group": verb_group,
        "archive_path": archive_path,
        "archived_at": now,
        "strategy": strategy,
        "notes": None
    }

    if "project" not in col_names:
        col_names.insert(0, "project")

    intended_cols = ["project", "run_id", "verb", "verb_group",
                     "archive_path", "archived_at", "strategy", "notes"]
    insert_cols = [c for c in intended_cols if c in col_names]
    insert_vals = [values_dict[c] for c in insert_cols]
    cols_str = ", ".join([f'"{c}"' for c in insert_cols])

    debug(f"[_insert_runs_archive_index_row] Inserting: {dict(zip(insert_cols, insert_vals))}")

    if arc.kind == "sqlite":
        placeholders = ",".join(["?"] * len(insert_vals))
        sql = f'INSERT INTO runs_archive_index ({cols_str}) VALUES ({placeholders})'
        arc.conn.execute(sql, tuple(insert_vals))
    else:
        placeholders = ",".join(["%s"] * len(insert_vals))
        sql = f'INSERT INTO runs_archive_index ({cols_str}) VALUES ({placeholders})'
        with arc.conn.cursor() as cur:
            cur.execute(sql, tuple(insert_vals))

    debug(f"[_insert_runs_archive_index_row] Successfully inserted run_id={run_id}, project={project}, verb={verb}")

def _fetch_schema_hashes_for_ids(
    arc: _DBHandle,
    noun_type: str,
    table: str,
    ids: List[str]
) -> List[str]:
    if not ids:
        return []
    _ensure_aux_archive_tables(arc)
    if arc.kind == "sqlite":
        ph = ",".join("?" * len(ids))
        rows = arc.conn.execute(
            f'''
            SELECT DISTINCT schema_hash FROM noun_archive_index
            WHERE noun_type=? AND table_name=? AND primary_id IN ({ph})
              AND schema_hash IS NOT NULL
            ''',
            (noun_type, table, *[str(i) for i in ids])
        ).fetchall()
        return [r["schema_hash"] for r in rows if r["schema_hash"]]
    with arc.conn.cursor() as cur:
        ph = ",".join(["%s"] * len(ids))
        cur.execute(
            f'''
            SELECT DISTINCT schema_hash FROM noun_archive_index
            WHERE noun_type=%s AND table_name=%s AND primary_id IN ({ph})
              AND schema_hash IS NOT NULL
            ''',
            (noun_type, table, *[str(i) for i in ids])
        )
        return [r[0] for r in cur.fetchall() if r and r[0]]

def _build_legacy_payload_dict(row: Dict[str, Any] | sqlite3.Row, legacy_cols: List[str]) -> Dict[str, Any]:
    d: Dict[str, Any] = {}
    if isinstance(row, dict):
        for c in legacy_cols:
            if c in row:
                val = row[c]
                if isinstance(val, bytes):
                    d[c] = val.decode("utf-8", errors="replace")
                else:
                    d[c] = val
        return d
    for c in legacy_cols:
        if c in row.keys():
            val = row[c]
            if isinstance(val, bytes):
                d[c] = val.decode("utf-8", errors="replace")
            else:
                d[c] = val
    return d

def _stash_legacy_data_after_hard_restore(
    project_path: Path,
    noun_type: str,
    table: str,
    pf: str,
    ids: List[str],
    archive_only_columns: List[str]
):
    if not ids or not archive_only_columns:
        return
    project = project_path.name
    with _open_hot_and_arc(project_path) as (hot, arc):
        if not _db_table_exists(hot, table) or not _db_table_exists(arc, table):
            return
        _ensure_legacy_column(hot, table)

        sel_cols = ', '.join([f'"{c}"' for c in archive_only_columns])
        for rid in ids:
            if arc.kind == "sqlite":
                row = arc.conn.execute(
                    f'SELECT {sel_cols} FROM "{table}" WHERE "{pf}"=? LIMIT 1', (rid,)
                ).fetchone()
                row_dict = dict(row) if row else None
            else:
                with arc.conn.cursor() as cur:
                    cur.execute(
                        f'SELECT {sel_cols} FROM "{table}" WHERE "{pf}"=%s LIMIT 1', (rid,)
                    )
                    got = cur.fetchone()
                    if not got:
                        row_dict = None
                    else:
                        cols = [d.name for d in cur.description]
                        row_dict = dict(zip(cols, got))

            if not row_dict:
                continue

            legacy_dict = _build_legacy_payload_dict(row_dict, archive_only_columns)
            if not legacy_dict:
                continue
            payload = json.dumps(legacy_dict, ensure_ascii=False)

            if hot.kind == "sqlite":
                hot.conn.execute(
                    f'UPDATE "{table}" SET "__legacy_data"=? WHERE "{pf}"=?',
                    (payload, rid)
                )
            else:
                with hot.conn.cursor() as cur:
                    cur.execute(
                        f'UPDATE "{table}" SET "__legacy_data"=%s WHERE "{pf}"=%s',
                        (payload, rid)
                    )

# ------------------------------------------------------------------------------
# Endpoints (DB-agnostic implementations)
# ------------------------------------------------------------------------------

@router.get("/{project}/nouns/archived")
def list_archived_candidates(
    project: str,
    noun: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None, pattern="^(soft|hard)$"),
    limit: int = Query(200, ge=1, le=5000)
):
    """
    Return IDs currently in the archive for each noun (soft->hot.archived=1, hard->archive table).
    """
    debug("[list_archived_candidates]", project, "noun=", noun, "strategy=", strategy)
    project_path = _resolve_project_path(project)
    policy = _load_policy(project_path)
    noun_types = load_schema(project_path, "noun")  # dict of noun_type -> schema
    if noun:
        noun_types = {noun: noun_types.get(noun)} if noun in noun_types else {}

    with _open_hot_and_arc(project_path) as (hot, arc):
        out = {}
        for noun_type, entry in (noun_types or {}).items():
            if not entry:
                continue
            table_hot = _effective_noun_table_name(hot, project, noun_type)
            table_arc = _effective_noun_table_name(arc, project, noun_type)
            pf = _get_primary_field_or_first(hot, table_hot, entry)
            if not pf:
                continue

            strat = strategy or (policy.get("nouns", {}).get(noun_type, {}).get("strategy", "soft"))

            if strat == "soft":
                _ensure_soft_columns(_ExecContext(hot, arc), table_hot)
                if not _db_table_exists(hot, table_hot):
                    continue
                ids: List[str] = []
                if hot.kind == "sqlite":
                    rows = hot.conn.execute(
                        f'SELECT "{pf}" AS pid FROM "{table_hot}" WHERE archived=1 ORDER BY archived_at ASC, rowid ASC LIMIT ?',
                        (limit,)
                    ).fetchall()
                    ids = [str(r["pid"]) for r in rows if r["pid"] is not None]
                    count = hot.conn.execute(
                        f'SELECT COUNT(*) AS n FROM "{table_hot}" WHERE archived=1'
                    ).fetchone()["n"]
                else:
                    with hot.conn.cursor() as cur:
                        cur.execute(
                            f'SELECT "{pf}" AS pid FROM "{table_hot}" WHERE archived=1 ORDER BY archived_at ASC NULLS FIRST, "{pf}" ASC LIMIT %s',
                            (limit,)
                        )
                        ids = [str(r[0]) for r in cur.fetchall() if r and r[0] is not None]
                        cur.execute(f'SELECT COUNT(*) FROM "{table_hot}" WHERE archived=1')
                        count = cur.fetchone()[0] or 0
            else:
                if not _db_table_exists(arc, table_arc):
                    continue
                ids: List[str] = []
                if arc.kind == "sqlite":
                    rows = arc.conn.execute(
                        f'SELECT "{pf}" AS pid FROM "{table_arc}" ORDER BY rowid ASC LIMIT ?',
                        (limit,)
                    ).fetchall()
                    ids = [str(r["pid"]) for r in rows if r["pid"] is not None]
                else:
                    with arc.conn.cursor() as cur:
                        cur.execute(
                            f'SELECT "{pf}" AS pid FROM "{table_arc}" ORDER BY "{pf}" ASC LIMIT %s',
                            (limit,)
                        )
                        ids = [str(r[0]) for r in cur.fetchall() if r and r[0] is not None]
                count = _db_count_rows(arc, table_arc)

            out[noun_type] = {
                "strategy": strat,
                "primary_field": pf,
                "count": int(count),
                "ids": ids
            }
        return out

@router.post("/{project}/nouns/restore/preview")
def preview_noun_restore(
    project: str,
    selection: Any = Body(...),
    strategy: Optional[str] = Query(None, pattern="^(soft|hard)$")
):
    """
    Build a restore plan for the provided IDs.
    For hard strategy: include schema drift detection (via stored schema_hash if present).
    """
    debug("[preview_noun_restore]", project, "items:", {k: len(v) for k, v in (selection or {}).items()})
    project_path = _resolve_project_path(project)
    policy = _load_policy(project_path)
    noun_types = load_schema(project_path, "noun")

    with _open_hot_and_arc(project_path) as (hot, arc):
        out = {}
        for noun_type, ids in (selection or {}).items():
            entry = noun_types.get(noun_type)
            if not entry or not ids:
                continue

            table_hot = _effective_noun_table_name(hot, project, noun_type)
            table_arc = _effective_noun_table_name(arc, project, noun_type)
            pf = _get_primary_field_or_first(hot, table_hot, entry)
            if not pf:
                continue

            strat = strategy or (policy.get("nouns", {}).get(noun_type, {}).get("strategy", "soft"))

            drift_detected = False
            drift_report: Optional[Dict[str, Any]] = None

            if strat == "soft":
                plan = plan_restore_nouns_soft(noun_type, table_hot, pf, ids)
            else:
                hot_cols_full = _db_columns_full(hot, table_hot)
                arc_cols_full = _db_columns_full(arc, table_arc)

                stored_hashes = _fetch_schema_hashes_for_ids(arc, noun_type, table_arc, ids)
                current_hot_hash = _compute_schema_hash_db(hot, table_hot) if _db_table_exists(hot, table_hot) else ""
                if stored_hashes:
                    for h in stored_hashes:
                        if h != current_hot_hash:
                            drift_detected = True
                            break
                else:
                    if json.dumps(sorted([(c["name"], c["type"], c["notnull"], c["dflt_value"], c["pk"]) for c in hot_cols_full])) != \
                       json.dumps(sorted([(c["name"], c["type"], c["notnull"], c["dflt_value"], c["pk"]) for c in arc_cols_full])):
                        drift_detected = True

                drift_report = _diff_schemas(hot_cols_full, arc_cols_full)

                plan = plan_restore_nouns_hard(
                    noun_type, table_arc, table_hot, pf, ids,
                    hot_columns=[(c["name"], c["type"]) for c in hot_cols_full],
                    archive_columns=[(c["name"], c["type"]) for c in arc_cols_full]
                )

            out[noun_type] = {
                "strategy": strat,
                "ids": ids,
                "plan": _serialize_plan(plan),
                **({"drift_detected": bool(drift_detected), "drift_report": drift_report} if strat == "hard" else {})
            }
        return out

@router.post("/{project}/nouns/restore/apply")
def apply_noun_restore(
    project: str,
    selection: Any = Body(...),
    strategy: Optional[str] = Query(None, pattern="^(soft|hard)$")
):
    """
    Execute restore (soft: clear flags; hard: copy from archive to hot).
    On hard: writes archive-only columns into hot.__legacy_data JSON.
    """
    debug("[apply_noun_restore]", project, "items:", {
        k: len(v) for k, v in (selection or {}).items() if isinstance(v, (list, tuple))
    })
    project_path = _resolve_project_path(project)
    policy = _load_policy(project_path)
    noun_types = load_schema(project_path, "noun")

    with _open_hot_and_arc(project_path) as (hot, arc):
        results: Dict[str, Any] = {}
        for noun_type, ids in (selection or {}).items():
            entry = noun_types.get(noun_type)
            if not entry or not ids:
                results[noun_type] = {"ok": True, "affected": 0}
                continue

            table_hot = _effective_noun_table_name(hot, project, noun_type)
            table_arc = _effective_noun_table_name(arc, project, noun_type)
            pf = _get_primary_field_or_first(hot, table_hot, entry)
            if not pf:
                results[noun_type] = {"ok": False, "error": "No primary field"}
                continue

            strat = strategy or (policy.get("nouns", {}).get(noun_type, {}).get("strategy", "soft"))

            if strat == "soft":
                plan = plan_restore_nouns_soft(noun_type, table_hot, pf, ids)
                exec_result = _execute_plan(project_path, plan)
                results[noun_type] = {"ok": exec_result["ok"], "affected": len(ids), "strategy": strat}
            else:
                hot_cols_full = _db_columns_full(hot, table_hot)
                arc_cols_full = _db_columns_full(arc, table_arc)
                hot_names = {c["name"] for c in hot_cols_full}
                arc_names = {c["name"] for c in arc_cols_full}
                archive_only_cols = sorted(list(arc_names - hot_names))

                plan = plan_restore_nouns_hard(
                    noun_type, table_arc, table_hot, pf, ids,
                    hot_columns=[(c["name"], c["type"]) for c in hot_cols_full],
                    archive_columns=[(c["name"], c["type"]) for c in arc_cols_full]
                )

                exec_result = _execute_plan(project_path, plan)

                try:
                    _stash_legacy_data_after_hard_restore(project_path, noun_type, table_hot, pf, ids, archive_only_cols)
                except Exception as e:
                    results[noun_type] = {
                        "ok": exec_result["ok"],
                        "affected": len(ids),
                        "strategy": strat,
                        "legacy_note": f"__legacy_data write had an issue: {e}"
                    }
                    continue

                results[noun_type] = {"ok": exec_result["ok"], "affected": len(ids), "strategy": strat}

        debug("[apply_noun_restore] done")
        return results

@router.get("/{project}/noun_types")
def list_noun_types(project: str):
    debug("[noun_types] start", project)
    project_path = _resolve_project_path(project)
    try:
        data = load_schema(project_path, "noun")
        names = sorted(data.keys())
        debug("[noun_types] ->", len(names), "items")
        return names
    except FileNotFoundError as e:
        debug("[noun_types] FileNotFoundError:", str(e))
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        debug("[noun_types] Error:", str(e))
        raise HTTPException(status_code=500, detail=f"Failed to load noun types: {str(e)}")

@router.get("/{project}/verb_types")
def list_verb_types(project: str):
    debug("[verb_types] start", project)
    project_path = _resolve_project_path(project)
    try:
        data = load_schema(project_path, "verb")
        names = sorted(data.keys())
        debug("[verb_types] ->", len(names), "items")
        return names
    except FileNotFoundError as e:
        debug("[verb_types] FileNotFoundError:", str(e))
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        debug("[verb_types] Error:", str(e))
        raise HTTPException(status_code=500, detail=f"Failed to load verb types: {str(e)}")

@router.get("/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception as e:
        # Optional: return empty list on failure instead of 500
        print(f"[list_projects] list_projects failed: {e!r}")
        return []

@router.get("/{project}/policy")
def get_policy(project: str):
    debug("[get_policy]", project)
    project_path = _resolve_project_path(project)
    return _load_policy(project_path)

@router.post("/{project}/policy")
def save_policy(project: str, body: Dict[str, Any] = Body(...)):
    debug("[save_policy]", project)
    project_path = _resolve_project_path(project)
    policy_path = resolve_path(project_path, "archive_policy")
    ensure_prefix(policy_path.parent)  # S3/FS-safe
    write_text(policy_path, json.dumps(body, indent=2), encoding="utf-8")  # S3-aware
    debug("[save_policy] saved to", policy_path)
    return {"ok": True}

@router.get("/{project}/nouns/preview")
def preview_noun_archive(project: str):
    """
    Build a plan-per-noun using archive_policy.json and current SQL stats.
    Returns a serialized summary (does not execute).
    """
    debug("[preview_noun_archive] start", project)
    try:
        project_path = _resolve_project_path(project)
        policy = _load_policy(project_path)
        noun_types = load_schema(project_path, "noun")  # dict
        if not noun_types:
            debug("[preview_noun_archive] ! no noun types found in schema")
            return {}
    except Exception as e:
        debug(f"[preview_noun_archive] ! ERROR during init: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load project config: {e}")

    debug(f"[preview_noun_archive] opening DBs for project: {project}")
    with _open_hot_and_arc(project_path) as (hot, arc):
        noun_tables: Dict[str, Dict[str, Any]] = {}
        for noun_type, entry in (noun_types or {}).items():
            debug("\n[preview_noun_archive] --- noun_type:", noun_type, "---")
            try:
                table_hot = _effective_noun_table_name(hot, project, noun_type)
                table_arc = _effective_noun_table_name(arc, project, noun_type)
                debug(f"[preview_noun_archive] hot_table='{table_hot}', arc_table='{table_arc}'")

                pf = _get_primary_field(entry) or next((c for c, _ in _db_columns_simple(hot, table_hot)[:1]), None)
                if not pf:
                    debug("[preview_noun_archive] ! no primary field found, skipping noun type")
                    continue
                
                debug(f"[preview_noun_archive] primary_field='{pf}'")

                pol = (policy.get("nouns") or {}).get(noun_type, {})
                date_field = pol.get("date_field")
                debug(f"[preview_noun_archive] policy='{pol}', date_field='{date_field}'")

                total_count = _db_count_rows(hot, table_hot)
                ordered_ids = _ordered_oldest_ids(hot, table_hot, pf, date_field if date_field else None)
                age_eval_rows = _rows_for_age_eval(hot, table_hot, pf, date_field) if date_field else []
                hot_cols = _db_columns_simple(hot, table_hot)
                arc_cols = _db_columns_simple(arc, table_arc)

                debug(f"[preview_noun_archive] total_count={total_count}, len(ordered_ids)={len(ordered_ids)}, len(age_eval_rows)={len(age_eval_rows)}")
                debug(f"[preview_noun_archive] hot_cols_count={len(hot_cols)}, arc_cols_count={len(arc_cols)}")

                noun_tables[noun_type] = {
                    "table": table_hot,
                    "primary_field": pf,
                    "total_count": total_count,
                    "ordered_oldest_ids": ordered_ids,
                    "date_field": date_field,
                    "rows_for_age_eval": age_eval_rows,
                    "hot_columns": hot_cols,
                    "archive_columns": arc_cols
                }
            except Exception as e:
                debug(f"[preview_noun_archive] ! ERROR processing noun '{noun_type}': {e}")
                continue

        debug(f"\n[preview_noun_archive] processed {len(noun_tables)} noun types, building plans via core...")
        try:
            plan_map = plan_apply_archive_policy_for_nouns(policy, noun_tables)
            debug(f"[preview_noun_archive] core returned {len(plan_map)} plans")
        except Exception as e:
            debug(f"[preview_noun_archive] ! ERROR during core plan generation: {e}")
            raise HTTPException(status_code=500, detail=f"Core planning failed: {e}")

        summary = {
            k: {
                "strategy": v["strategy"],
                "eligible_ids": v["eligible_ids"],
                "plan": _serialize_plan(v["plan"])
            } for k, v in plan_map.items()
        }
        debug("[preview_noun_archive] done, returning summary")
        return summary

# ---------------------- noun instance ID listing (RDS-aware) ------------------

@router.get("/{project}/nouns/ids")
def list_noun_instance_ids(
    project: str,
    type: str = Query(..., description="Noun type, e.g. 'Glove'"),
    q: Optional[str] = Query(None, description="Case-insensitive substring filter on primary ID"),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> List[str]:
    """
    Return primary-key values (IDs) for the given noun type from the *hot* nouns DB.
    RDS: <Project>_noun_<Name> + ILIKE; SQLite: noun_<Name> + LIKE.
    """
    debug("[list_noun_instance_ids]", project, "type=", type, "q=", q, "limit=", limit, "offset=", offset)
    project_path = _resolve_project_path(project)
    with _open_db(project_path, "object_sql_db") as hot:
        entry = (load_schema(project_path, "noun") or {}).get(type, {})
        table = _effective_noun_table_name(hot, project, type)
        if not _db_table_exists(hot, table):
            debug("[list_noun_instance_ids] table missing:", table, "(returning empty list)")
            return []

        pf = _get_primary_field_or_first(hot, table, entry or {})
        if not pf:
            debug("[list_noun_instance_ids] no primary field for:", table)
            return []

        if hot.kind == "sqlite":
            where = f'WHERE "{pf}" IS NOT NULL'
            params: List[Any] = []
            if q:
                where += f' AND CAST("{pf}" AS TEXT) LIKE ?'
                params.append(f"%{q}%")
            sql = f'SELECT DISTINCT "{pf}" AS id FROM "{table}" {where} ORDER BY 1 LIMIT ? OFFSET ?;'
            params.extend([limit, offset])
            rows = [r["id"] for r in hot.conn.execute(sql, params).fetchall()]
            out = ["" if r is None else str(r) for r in rows]
            debug("[list_noun_instance_ids] ->", len(out), "ids")
            return out

        with hot.conn.cursor() as cur:
            if q:
                cur.execute(
                    f'SELECT DISTINCT "{pf}" AS id FROM "{table}" WHERE "{pf}" IS NOT NULL AND CAST("{pf}" AS TEXT) ILIKE %s ORDER BY 1 LIMIT %s OFFSET %s',
                    (f"%{q}%", limit, offset)
                )
            else:
                cur.execute(
                    f'SELECT DISTINCT "{pf}" AS id FROM "{table}" WHERE "{pf}" IS NOT NULL ORDER BY 1 LIMIT %s OFFSET %s',
                    (limit, offset)
                )
            rows = [r[0] for r in cur.fetchall()]
            out = ["" if r is None else str(r) for r in rows]
            debug("[list_noun_instance_ids][pg] ->", len(out), "ids")
            return out

@router.get("/{project}/nouns/ids/{type}")
def list_noun_instance_ids_path(
    project: str,
    type: str,
    q: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> List[str]:
    return list_noun_instance_ids(project, type=type, q=q, limit=limit, offset=offset)

# ------------------------------------------------------------------------------
# Archive apply (write schema_hash in hard mode; RDS-aware)
# ------------------------------------------------------------------------------

@router.post("/{project}/nouns/apply")
def apply_noun_archive(project: str, selection: Any = Body(None)):
    """
    Execute archive for provided noun IDs (or derive from policy if selection omitted).
    For hard archive, index schema_hash of HOT table at time of archive.
    """
    debug("[apply_noun_archive]", project)
    project_path = _resolve_project_path(project)
    policy = _load_policy(project_path)
    noun_types = load_schema(project_path, "noun")  # dict

    with _open_hot_and_arc(project_path) as (hot, arc):
        _ensure_aux_archive_tables(arc)

        results: Dict[str, Any] = {}
        for noun_type, entry in (noun_types or {}).items():
            ids = (selection or {}).get(noun_type, [])
            debug("\n[apply_noun_archive] noun:", noun_type, "requested_ids:", ids)

            table_hot = _effective_noun_table_name(hot, project, noun_type)
            table_arc = _effective_noun_table_name(arc, project, noun_type)

            if not ids:
                debug("[apply_noun_archive] deriving from policy via preview")
                pf_guess = _get_primary_field(entry) or next((c for c, _ in _db_columns_simple(hot, table_hot)[:1]), None)
                if not pf_guess:
                    continue
                pol = (policy.get("nouns") or {}).get(noun_type, {})
                date_field = pol.get("date_field")
                noun_tables = {
                    noun_type: {
                        "table": table_hot,
                        "primary_field": pf_guess,
                        "total_count": _db_count_rows(hot, table_hot),
                        "ordered_oldest_ids": _ordered_oldest_ids(hot, table_hot, pf_guess, date_field if date_field else None),
                        "date_field": date_field,
                        "rows_for_age_eval": _rows_for_age_eval(hot, table_hot, pf_guess, date_field) if date_field else [],
                        "hot_columns": _db_columns_simple(hot, table_hot),
                        "archive_columns": _db_columns_simple(arc, table_arc)
                    }
                }
                plan_map = plan_apply_archive_policy_for_nouns(policy, noun_tables)
                ids = plan_map.get(noun_type, {}).get("eligible_ids", [])

            if not ids:
                debug("[apply_noun_archive] nothing to do for", noun_type)
                results[noun_type] = {"ok": True, "affected": 0}
                continue

            pol = (policy.get("nouns") or {}).get(noun_type, {})
            strategy = pol.get("strategy") or policy.get("default", {}).get("strategy", "soft")
            pf = _get_primary_field(entry) or next((c for c, _ in _db_columns_simple(hot, table_hot)[:1]), None)
            if not pf:
                continue

            if strategy == "soft":
                plan = plan_soft_archive_nouns(noun_type, table_hot, pf, ids)
            else:
                plan = plan_hard_archive_nouns(
                    noun_type, table_hot, table_arc, pf, ids,
                    hot_columns=_db_columns_simple(hot, table_hot),
                    archive_columns=_db_columns_simple(arc, table_arc)
                )

            debug("[apply_noun_archive] executing plan steps:", len(plan.steps))
            exec_result = _execute_plan(project_path, plan)

            if strategy == "hard" and _db_table_exists(hot, table_hot):
                try:
                    schema_hash = _compute_schema_hash_db(hot, table_hot)
                    with _open_db(project_path, "archive_sql_db") as index_arc:
                        _insert_noun_archive_index_rows(
                            index_arc, project_path.name, noun_type, table_arc, pf, ids, strategy, schema_hash
                        )
                        _safe_commit(index_arc.conn)
                except Exception as e:
                    debug("[apply_noun_archive] warning: failed to index schema_hash:", e)

            results[noun_type] = {"ok": exec_result["ok"], "affected": len(ids), "strategy": strategy}

        debug("\n[apply_noun_archive] all done")
        return results

# ------------------------------------------------------------------------------
# Runs archive/restore (file ops + linked nouns; RDS-aware for nouns)
# ------------------------------------------------------------------------------

@router.post("/{project}/runs/archive/preview")
def preview_run_archive(
    project: str,
    items: List[Dict[str, str]] = Body(...),
    strategy: str = Query("hard", pattern="^(soft|hard)$")
):
    debug("[preview_run_archive]", project, "strategy=", strategy, "items=", len(items))
    project_path = _resolve_project_path(project)
    norm_items = _normalize_run_items(project_path, items)
    if strategy == "soft":
        plan = plan_archive_runs_soft(norm_items)
    else:
        plan = plan_archive_runs_hard(norm_items)
    return _serialize_plan(plan)

@router.post("/{project}/runs/archive/apply")
def apply_run_archive(
    project: str,
    payload: Any = Body(...),
    strategy: str = Query("hard", pattern="^(soft|hard)$")
):
    if isinstance(payload, dict) and "verb_group" in payload and "run_ids" in payload:
        vg = payload.get("verb_group")
        items: List[Dict[str, str]] = [{"run_id": str(rid), "verb_group": vg} for rid in (payload.get("run_ids") or [])]
    elif isinstance(payload, list):
        items = payload
    else:
        raise HTTPException(status_code=422, detail="Body must be a list of items or {'verb_group','run_ids'}.")

    debug("[apply_run_archive]", project, "strategy=", strategy, "items=", len(items))
    project_path = _resolve_project_path(project)

    norm_items = _normalize_run_items(project_path, items)

    try:
        for it in norm_items:
            _ensure_verb_archive_exists(project_path, it["verb_group"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to ensure archive folder(s): {e}")

    plan = plan_archive_runs_hard(norm_items) if strategy == "hard" else plan_archive_runs_soft(norm_items)

    runs_result = _execute_plan(project_path, plan)

    try:
        with _open_db(project_path, "archive_sql_db") as arc:
            for it in norm_items:
                _insert_runs_archive_index_row(
                    arc=arc,
                    project=project_path.name,
                    run_id=it["run_id"],
                    verb=it.get("test_type") or it.get("verb"),
                    verb_group=it.get("verb_group"),
                    archive_path=it.get("dst_dir"),
                    strategy=strategy,
                )
            _safe_commit(arc.conn)
    except Exception as e:
        debug("[apply_run_archive] ERROR: Failed to write to runs_archive_index:", e)
        runs_result["index_error"] = str(e)

    policy = _load_policy(project_path)
    noun_types = load_schema(project_path, "noun")  # dict

    with _open_hot_and_arc(project_path) as (hot, arc):
        selection: Dict[str, List[str]] = {}
        for it in norm_items:
            hinted_nt, ids = _collect_linked_noun_ids(project_path, it.get("test_type"), it["run_id"])
            if hinted_nt and ids:
                selection.setdefault(hinted_nt, []).extend(ids)
            else:
                scan_map = _collect_linked_noun_ids_by_scan(project_path, it["run_id"])
                if not scan_map:
                    debug(f"[apply_run_restore] no linked nouns found for run_id={it['run_id']} (this is normal).")
                for nt, idlist in scan_map.items():
                    selection.setdefault(nt, []).extend(idlist)

        for nt in list(selection.keys()):
            selection[nt] = sorted(set(selection[nt]))

        noun_results: Dict[str, Any] = {}

        for noun_type, ids in selection.items():
            if not ids:
                noun_results[noun_type] = {"ok": True, "affected": 0}
                continue

            entry = noun_types.get(noun_type)
            if not entry:
                noun_results[noun_type] = {"ok": False, "error": "Unknown noun type"}
                continue

            table_hot = _effective_noun_table_name(hot, project, noun_type)
            table_arc = _effective_noun_table_name(arc, project, noun_type)
            if not _db_table_exists(hot, table_hot):
                noun_results[noun_type] = {"ok": True, "affected": 0}
                continue

            pf = _get_primary_field_or_first(hot, table_hot, entry)
            if not pf:
                noun_results[noun_type] = {"ok": False, "error": "No primary field"}
                continue

            pol = (policy.get("nouns") or {}).get(noun_type, {})
            noun_strategy = pol.get("strategy", "soft")

            if noun_strategy == "soft":
                noun_plan = plan_soft_archive_nouns(noun_type, table_hot, pf, ids)
            else:
                noun_plan = plan_hard_archive_nouns(
                    noun_type, table_hot, table_arc, pf, ids,
                    hot_columns=_db_columns_simple(hot, table_hot),
                    archive_columns=_db_columns_simple(arc, table_arc)
                )

            exec_res = _execute_plan(project_path, noun_plan)

            if noun_strategy == "hard" and _db_table_exists(hot, table_hot):
                try:
                    schema_hash = _compute_schema_hash_db(hot, table_hot)
                    with _open_db(project_path, "archive_sql_db") as index_arc:
                        _insert_noun_archive_index_rows(
                            index_arc, project_path.name, noun_type, table_arc, pf, ids, noun_strategy, schema_hash
                        )
                        _safe_commit(index_arc.conn)
                except Exception as e:
                    debug("[apply_run_archive] warning: failed to index schema_hash for noun:", noun_type, e)

            noun_results[noun_type] = {
                "ok": exec_res["ok"],
                "affected": len(ids),
                "strategy": noun_strategy
            }

    return {"ok": runs_result.get("ok", True), "runs": runs_result, "nouns": noun_results}

@router.post("/{project}/runs/restore/apply")
def apply_run_restore(
    project: str,
    payload: Any = Body(...)
):
    """
    Restores run folders AND their linked nouns (soft+hard).
    """
    debug("\n=== [apply_run_restore] START ===")
    debug("[apply_run_restore] raw payload:", payload)

    if isinstance(payload, dict) and "verb_group" in payload and "run_ids" in payload:
        vg = payload.get("verb_group")
        items: List[Dict[str, Any]] = [{"run_id": str(rid), "verb_group": vg} for rid in (payload.get("run_ids") or [])]
        debug("[apply_run_restore] normalized simple payload -> items:", items)
    elif isinstance(payload, list):
        items = payload
        debug("[apply_run_restore] using advanced payload -> items:", items)
    else:
        raise HTTPException(status_code=422, detail="Body must be a list of items or {'verb_group','run_ids'}.")

    debug("[apply_run_restore] project:", project, "items count:", len(items))
    project_path = _resolve_project_path(project)
    debug("[apply_run_restore] project_path:", project_path)

    norm_items = _normalize_restore_items(project_path, items)
    debug("[apply_run_restore] norm_items:", norm_items)

    try:
        for it in norm_items:
            debug("[apply_run_restore] ensuring dirs for verb_group:", it["verb_group"])
            _ensure_verb_archive_exists(project_path, it["verb_group"])
            _ensure_hot_dump_parent_exists(project_path, it["verb_group"])
    except Exception as e:
        debug("[apply_run_restore] X Failed to ensure dirs:", e)
        raise HTTPException(status_code=500, detail=f"Failed to ensure run directories: {e}")

    debug("[apply_run_restore] building restore plan for runs...")
    runs_plan = plan_restore_runs(norm_items)
    debug("[apply_run_restore] runs_plan steps:", len(runs_plan.steps))
    try:
        runs_result = _execute_plan(project_path, runs_plan)
        debug("[apply_run_restore] runs_result:", runs_result)
    except Exception as e:
        debug("[apply_run_restore] ERROR executing runs plan:", repr(e))
        raise HTTPException(status_code=500, detail=f"Failed to restore runs: {str(e)}")

    debug("[apply_run_restore] beginning noun restore...")
    noun_schema = load_schema(project_path, "noun")  # dict

    with _open_hot_and_arc(project_path) as (hot, arc):
        selection: Dict[str, List[str]] = {}
        for it in norm_items:
            debug("[apply_run_restore] checking linked nouns for run:", it["run_id"])
            hinted_nt, ids = _collect_linked_noun_ids(project_path, it.get("test_type"), it["run_id"])
            if hinted_nt and ids:
                debug("[apply_run_restore] linked via schema ->", hinted_nt, ids)
                selection.setdefault(hinted_nt, []).extend(ids)
            else:
                debug(f"[apply_run_restore] no schema hint; scanning all tables for run_id={it['run_id']} (0 linked is fine).")
                scan_map = _collect_linked_noun_ids_by_scan(project_path, it["run_id"])
                debug(f"[apply_run_restore] scan_map: {len(scan_map)} noun type(s) linked (0 is fine).")
                for nt, idlist in scan_map.items():
                    selection.setdefault(nt, []).extend(idlist)

        for nt in list(selection.keys()):
            before = selection[nt]
            selection[nt] = sorted(set(selection[nt]))
            debug(f"[apply_run_restore] dedup {nt}: before={before}, after={selection[nt]}")

        noun_results: Dict[str, Any] = {}

        for noun_type, ids in selection.items():
            debug(f"\n[apply_run_restore] noun_type={noun_type}, candidate_ids={ids}")
            if not ids:
                noun_results[noun_type] = {"ok": True, "affected": 0}
                debug(f"[apply_run_restore] noun_type={noun_type} -> no IDs, skip")
                continue

            table_hot = _effective_noun_table_name(hot, project, noun_type)
            entry = noun_schema.get(noun_type) or {}
            pf = _get_primary_field_or_first(hot, table_hot, entry)
            debug(f"[apply_run_restore] table={table_hot}, primary_field={pf}")
            if not pf:
                noun_results[noun_type] = {"ok": False, "error": "No primary field"}
                continue

            soft_ids, hard_ids = _split_noun_ids_by_actual_archive(project_path, noun_type, ids)
            debug(f"[apply_run_restore] split: soft_ids={soft_ids}, hard_ids={hard_ids}")

            if soft_ids:
                debug(f"[apply_run_restore] building soft restore plan for {noun_type}, ids={soft_ids}")
                plan_soft = plan_restore_nouns_soft(noun_type, table_hot, pf, soft_ids)
                debug(f"[apply_run_restore] plan_soft steps={len(plan_soft.steps)}")
                _execute_plan(project_path, plan_soft)

            if hard_ids:
                debug(f"[apply_run_restore] building hard restore plan for {noun_type}, ids={hard_ids}")
                table_arc = _effective_noun_table_name(arc, project, noun_type)
                hot_cols_full = _db_columns_full(hot, table_hot)
                arc_cols_full = _db_columns_full(arc, table_arc)
                hot_names = {c["name"] for c in hot_cols_full}
                arc_names = {c["name"] for c in arc_cols_full}
                archive_only_cols = sorted(list(arc_names - hot_names))

                plan_hard = plan_restore_nouns_hard(
                    noun_type, table_arc, table_hot, pf, hard_ids,
                    hot_columns=[(c["name"], c["type"]) for c in hot_cols_full],
                    archive_columns=[(c["name"], c["type"]) for c in arc_cols_full]
                )
                debug(f"[apply_run_restore] plan_hard steps={len(plan_hard.steps)}")
                _execute_plan(project_path, plan_hard)

                try:
                    _stash_legacy_data_after_hard_restore(project_path, noun_type, table_hot, pf, hard_ids, archive_only_cols)
                except Exception as e:
                    debug("[apply_run_restore] legacy stash warning:", e)

            noun_results[noun_type] = {
                "ok": True,
                "affected": len(soft_ids) + len(hard_ids),
                "restored_soft": len(soft_ids),
                "restored_hard": len(hard_ids)
            }
            debug(f"[apply_run_restore] noun_results[{noun_type}] ->", noun_results[noun_type])

    final = {"ok": runs_result.get("ok", True), "runs": runs_result, "nouns": noun_results}
    debug(f"[apply_run_restore] FINAL RESULT: runs_ok={final.get('ok', True)}, noun_types_restored={len(final.get('nouns', {}))} (0 is fine)")
    debug("=== [apply_run_restore] END ===\n")
    return final

@router.post("/{project}/runs/restore/preview")
def preview_run_restore(
    project: str,
    items: List[Dict[str, Any]] = Body(...)
):
    debug("[preview_run_restore]", project, "items=", len(items))
    project_path = _resolve_project_path(project)
    norm_items = _normalize_restore_items(project_path, items)
    for it in norm_items:
        _ensure_verb_archive_exists(project_path, it["verb_group"])
        _ensure_hot_dump_parent_exists(project_path, it["verb_group"])
    plan = plan_restore_runs(norm_items)
    return _serialize_plan(plan)

@router.get("/{project}/verb_groups")
def list_verb_groups(
    project: str,
    include_hidden: bool = Query(False, description="Include folders beginning with '.'")
) -> List[str]:
    """
    S3-aware folder listing for verb groups via json_proxy; FS fallback.
    """
    debug("[list_verb_groups] start", project)
    project_path = _resolve_project_path(project)
    try:
        verbs_root = resolve_path(project_path, "verbs_dir")
        debug("[list_verb_groups] verbs_root:", verbs_root)
        groups = _jp_list_dirnames(verbs_root.as_posix(), include_hidden=include_hidden)
        debug("[list_verb_groups] ->", groups)
        return groups
    except Exception as e:
        debug("[list_verb_groups] Error:", str(e))
        raise HTTPException(status_code=500, detail=f"Failed to list verb groups: {e}")

@router.get("/{project}/runs/list")
def list_runs(
    project: str,
    verb_group: str = Query(..., description="Name of the verb group (folder under verbs_dir)"),
    where: Literal["active", "archived"] = Query("active"),
):
    """
    List runs for a verb group by reading from the verb group log (DB or JSONL).
    For 'active' runs, filters out those that exist in the archive folder.
    For 'archived' runs, returns only those that exist in the archive folder.
    Archive presence is detected via S3 prefix listing or FS folder listing.
    """
    debug(f"[list_runs] project={project}, verb_group={verb_group}, where={where}")
    project_path = _resolve_project_path(project)

    try:
        # Use S3-aware helpers from i_o (DB-first; JSONL fallback)
        cfg = get_verb_group_log_config(project_path, verb_group)
        primary_id_field = cfg.get("primary_id")
        debug(f"[list_runs] primary_id_field: {primary_id_field}")

        if not primary_id_field:
            debug(f"[list_runs] no primary_id field in config")
            return {"runs": []}

        entries = load_verb_group_log(project_path, verb_group) or []
        all_runs = []
        for entry in entries:
            rid = entry.get(primary_id_field)
            if rid is not None:
                all_runs.append(str(rid))

        debug(f"[list_runs] found {len(all_runs)} total runs in log")

        archive_base = resolve_path(project_path, "data_dump_archive", verb_group=verb_group).as_posix()
        archived_dirs = set(_jp_list_dirnames(archive_base))

        if where == "active":
            runs = [r for r in all_runs if r not in archived_dirs]
            debug(f"[list_runs] active runs (not in archive): {runs}")
        else:
            runs = [r for r in all_runs if r in archived_dirs]
            debug(f"[list_runs] archived runs (in archive): {runs}")

        return {"runs": runs}

    except Exception as e:
        debug(f"[list_runs] error: {e}")
        import traceback
        debug(f"[list_runs] traceback: {traceback.format_exc()}")
        return {"runs": []}

# ------------------------------------------------------------------------------
# Archive table ensure (RDS-aware)
# ------------------------------------------------------------------------------

def _ensure_archive_table(ctx: _ExecContext, src_table: str, dst_table: str, columns: List[Tuple[str, str]], include_meta: bool):
    """
    Ensure the archive table exists *and* has all required columns.
    If the table already exists, add any missing hot or meta columns via ALTER TABLE.
    """
    debug(f"[_ensure_archive_table] START: ensuring archive table '{dst_table}'")

    meta_cols: List[Tuple[str, str]] = []
    if include_meta:
        meta_cols = [
            ("archived_from_table", "TEXT"),
            ("archived_at_meta",    "TEXT"),
            ("archive_strategy",    "TEXT"),
        ]

    arc = ctx.arc
    if not _db_table_exists(arc, dst_table):
        hot_names = {name for name, _ in (columns or [])}
        col_defs = [f'"{name}" {(ctype or "TEXT")}' for name, ctype in (columns or [])]
        for name, ctype in meta_cols:
            if name not in hot_names:
                col_defs.append(f'"{name}" {ctype}')
        ddl = f'CREATE TABLE "{dst_table}" ({", ".join(col_defs)})'
        
        debug(f"[_ensure_archive_table] Table does not exist. Executing CREATE:")
        debug(f"[_ensure_archive_table] DDL: {ddl}")

        if arc.kind == "sqlite":
            arc.conn.execute(ddl)
        else:
            try:
                with arc.conn.cursor() as cur:
                    cur.execute(ddl)
            except Exception as e:
                debug(f"[_ensure_archive_table][pg] !!! CREATE TABLE FAILED !!!")
                debug(f"[_ensure_archive_table][pg] ERROR: {e}")
                debug(f"[_ensure_archive_table][pg] FAILED DDL: {ddl}")
                raise e
        debug(f"[_ensure_archive_table] CREATE TABLE successful for '{dst_table}'")
        return

    debug(f"[_ensure_archive_table] Table '{dst_table}' exists. Checking for missing columns...")
    existing = {name: ctype for name, ctype in _db_columns_simple(arc, dst_table)}

    for name, ctype in (columns or []):
        if name not in existing:
            ddl = f'ALTER TABLE "{dst_table}" ADD COLUMN "{name}" {ctype or "TEXT"}'
            debug(f"[_ensure_archive_table] Adding missing hot column. DDL: {ddl}")
            if arc.kind == "sqlite":
                arc.conn.execute(ddl)
            else:
                try:
                    with arc.conn.cursor() as cur:
                        cur.execute(ddl)
                except Exception as e:
                    debug(f"[_ensure_archive_table][pg] !!! ALTER TABLE (hot col) FAILED !!!")
                    debug(f"[_ensure_archive_table][pg] ERROR: {e}")
                    debug(f"[_ensure_archive_table][pg] FAILED DDL: {ddl}")
                    raise e

    for name, ctype in meta_cols:
        if name not in existing:
            ddl = f'ALTER TABLE "{dst_table}" ADD COLUMN "{name}" {ctype}'
            debug(f"[_ensure_archive_table] Adding missing meta column. DDL: {ddl}")
            if arc.kind == "sqlite":
                arc.conn.execute(ddl)
            else:
                try:
                    with arc.conn.cursor() as cur:
                        cur.execute(ddl)
                except Exception as e:
                    debug(f"[_ensure_archive_table][pg] !!! ALTER TABLE (meta col) FAILED !!!")
                    debug(f"[_ensure_archive_table][pg] ERROR: {e}")
                    debug(f"[_ensure_archive_table][pg] FAILED DDL: {ddl}")
                    raise e
    
    debug(f"[_ensure_archive_table] FINISHED ensuring table '{dst_table}'")

# ------------------------------------------------------------------------------
# Run helpers for paths
# ------------------------------------------------------------------------------

def _normalize_run_items(project_path: Path, items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for it in (items or []):
        rid = it.get("run_id") or it.get("run") or it.get("id")
        if not rid:
            raise HTTPException(status_code=400, detail="Each run item must include 'run_id'.")
        
        test_type = it.get("test_type") or it.get("verb") or it.get("verb_name")
        verb_group = it.get("verb_group") or _resolve_verb_group(project_path, test_type) or "Tests"
        
        if not test_type:
            try:
                test_type = _lookup_verb_from_run_log(project_path, verb_group, rid)
                debug(f"[_normalize_run_items] Looked up test_type from log: {test_type}")
            except Exception as e:
                debug(f"[_normalize_run_items] Could not look up test_type for run {rid}: {e}")

        try:
            src_dir = it.get("src_dir") or str(resolve_path(project_path, "data_dump_dir", verb_group=verb_group, run_id=rid))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not resolve src_dir for run {rid}: {e}")

        try:
            dst_root = resolve_path(project_path, "data_dump_archive", verb_group=verb_group)
            dst_dir = it.get("dst_dir") or str((dst_root / rid))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not resolve dst_dir for run {rid}: {e}")

        debug(f"[_normalize_run_items] Normalized: run_id={rid}, test_type={test_type}, verb_group={verb_group}")

        out.append({
            **it,
            "run_id": rid,
            "test_type": test_type,
            "verb": test_type,
            "verb_group": verb_group,
            "src_dir": src_dir,
            "dst_dir": dst_dir
        })
    return out

def _lookup_verb_from_run_log(project_path: Path, verb_group: str, run_id: str) -> Optional[str]:
    """
    DB-first (S3-aware) lookup of verb (test_type) via the verb group log.
    """
    try:
        cfg = get_verb_group_log_config(project_path, verb_group)
        primary_id_field = cfg.get("primary_id")
        verb_field = cfg.get("verb_field") or "test_type"
        if not primary_id_field:
            return None
        entries = load_verb_group_log(project_path, verb_group) or []
        for entry in entries:
            if str(entry.get(primary_id_field)) == str(run_id):
                return entry.get(verb_field) or entry.get("test_type") or entry.get("verb") or entry.get("verb_name")
        return None
    except Exception as e:
        debug(f"[_lookup_verb_from_run_log] Error: {e}")
        return None

def _ensure_verb_archive_exists(project_path: Path, verb_group: str):
    vg_path = resolve_path(project_path, "verb_group", verb_group=verb_group)
    ensure_prefix(vg_path)  # S3/FS-safe
    arc_root = resolve_path(project_path, "data_dump_archive", verb_group=verb_group)
    ensure_prefix(arc_root)  # S3/FS-safe

def _ensure_hot_dump_parent_exists(project_path: Path, verb_group: str):
    vg = resolve_path(project_path, "verb_group", verb_group=verb_group)
    hot_parent = vg / "data_dumps"
    ensure_prefix(hot_parent)  # S3/FS-safe

def _normalize_restore_items(project_path: Path, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    norm: List[Dict[str, Any]] = []
    for it in (items or []):
        rid = it.get("run_id") or it.get("run") or it.get("id")
        if not rid:
            raise HTTPException(status_code=400, detail="Each restore item must include 'run_id'.")
        test_type = it.get("test_type") or it.get("verb") or it.get("verb_name")
        verb_group = it.get("verb_group") or _resolve_verb_group(project_path, test_type) or "Tests"

        arc_root = resolve_path(project_path, "data_dump_archive", verb_group=verb_group)
        arc_dir = Path(it.get("arc_dir") or (arc_root / rid))
        hot_dir = Path(it.get("hot_dir") or resolve_path(project_path, "data_dump_dir", verb_group=verb_group, run_id=rid))

        # S3-aware existence (prefix_exists) with FS fallback
        has_hard = _jp_prefix_exists(arc_dir.as_posix())
        has_soft = _jp_prefix_exists(hot_dir.as_posix()) and not has_hard

        norm.append({
            **it,
            "run_id": rid,
            "test_type": test_type,
            "verb_group": verb_group,
            "arc_dir": str(arc_dir),
            "hot_dir": str(hot_dir),
            "has_hard": bool(it.get("has_hard", has_hard)),
            "has_soft": bool(it.get("has_soft", has_soft)),
        })
    return norm

def _resolve_verb_group(project_path: Path, test_type: Optional[str], fallback: Optional[str] = None) -> str:
    try:
        if test_type:
            vs = get_verb_schema(project_path, test_type) or {}
            vg = vs.get("verb_group")
            if vg:
                return vg
    except Exception:
        pass
    return fallback or "Tests"
