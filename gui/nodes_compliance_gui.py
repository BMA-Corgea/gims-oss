# gui/nodes_compliance_gui.py
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

# Optional Postgres client for RDS
try:
    import psycopg  # psycopg v3

    _PSYCOPG_AVAILABLE = True
except Exception:
    _PSYCOPG_AVAILABLE = False

# SQLAlchemy for filewatch config table
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    select,
    delete,
)

# Project helpers
from api.manifest.resolver import resolve_path, get_db_uri  # RDS awareness
from api.i_o import load_local_layout_map, io_list_projects

router = APIRouter(prefix="/api/nodes_compliance", tags=["Nodes: Audit & Compliance"])

# ──────────────────────────────────────────────────────────────────────────────
# Debug control
# ──────────────────────────────────────────────────────────────────────────────
DEBUG_ENABLED = False  # flip to False to quiet logs


def debug(*args, **kwargs):
    if DEBUG_ENABLED:
        print("[nodes_compliance_gui]", *args, **kwargs, flush=True)


# ──────────────────────────────────────────────────────────────────────────────
# Path helpers (mirrors template_gui.py style)
# ──────────────────────────────────────────────────────────────────────────────
def _api_dir() -> Path:
    p = Path(__file__).resolve().parent
    debug("_api_dir", {"path": str(p)})
    return p


def _repo_root() -> Path:
    p = _api_dir().parent
    debug("_repo_root", {"path": str(p)})
    return p


def _projects_root() -> Path:
    layout = load_local_layout_map(_api_dir())
    root_name = layout.get("project_root", "projects")
    p = _repo_root() / root_name
    debug("_projects_root", {"root_name": root_name, "path": str(p)})
    return p


def _project_path(project_name: str) -> Path:
    p = _projects_root() / project_name
    debug("_project_path", {"project": project_name, "path": str(p)})
    return p


# ──────────────────────────────────────────────────────────────────────────────
# DB resolution (Postgres via resolver when available; else SQLite file)
# ──────────────────────────────────────────────────────────────────────────────
def _normalize_for_psycopg(url: str) -> str:
    """
    Convert SQLAlchemy/asyncpg style URLs to psycopg-compatible.
    - 'postgresql+asyncpg://' → 'postgresql://'
    - '?ssl=require'         → '?sslmode=require'
    """
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("postgresql+", 1)[1]
    if "?ssl=require" in url:
        url = url.replace("?ssl=require", "?sslmode=require")
    # safety, in case something weird happens
    url = url.replace("postgresql://asyncpg://", "postgresql://")
    return url


def _get_nodes_dsn(project_path: Path, project_name: str) -> Tuple[str, str]:
    """
    Determine whether to use Postgres or SQLite for this project's nodes DB.
    Returns (backend_kind, location)
      - ("postgres", DSN)  or
      - ("sqlite", /abs/path/to/nodes.db)
    """
    # Try resolver → DSN first (RDS mode)
    dsn = None
    try:
        uri = get_db_uri("nodes_db", project=project_name)
        dsn = uri
        debug("_get_nodes_dsn: resolver returned", dsn)
    except Exception as e:
        debug("_get_nodes_dsn: resolver failed", repr(e))
        dsn = None

    if dsn:
        if dsn.startswith("postgresql+"):
            return ("postgres", _normalize_for_psycopg(dsn))
        if dsn.startswith("postgresql://"):
            return ("postgres", _normalize_for_psycopg(dsn))
        # otherwise fall through to sqlite path resolution

    # Fallback to manifest-resolved SQLite file path
    db_path = resolve_path(project_path, "nodes_db")
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


@contextmanager
def _open_nodes_db(project_path: Path, project_name: str) -> _DBHandle:
    kind, target = _get_nodes_dsn(project_path, project_name)

    if kind == "postgres" and _PSYCOPG_AVAILABLE:
        debug("_open_nodes_db: connecting to PostgreSQL:", target)
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

    if kind == "postgres" and not _PSYCOPG_AVAILABLE:
        debug("_open_nodes_db: psycopg not available, falling back to SQLite")

    # SQLite path
    db_path = Path(target) if kind == "sqlite" else resolve_path(project_path, "nodes_db")
    debug("_open_nodes_db: connecting to SQLite at", db_path.as_posix())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path.as_posix())
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        yield _DBHandle("sqlite", conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Small helpers to hide placeholder differences
def _q_exec(db: _DBHandle, sql: str, params: Tuple[Any, ...] = ()):
    if db.kind == "pg":
        with db.conn.cursor() as cur:
            cur.execute(sql, params)
            try:
                return cur.fetchall()
            except Exception:
                return None
    else:
        sql = sql.replace("%s", "?")
        cur = db.conn.execute(sql, params)
        try:
            return cur.fetchall()
        except Exception:
            return None


def _q_one(db: _DBHandle, sql: str, params: Tuple[Any, ...] = ()):
    rows = _q_exec(db, sql, params) or []
    return rows[0] if rows else None


# ──────────────────────────────────────────────────────────────────────────────
# Introspection helpers for both engines
# ──────────────────────────────────────────────────────────────────────────────
def _list_tables_generic(db: _DBHandle) -> List[str]:
    if db.kind == "pg":
        rows = _q_exec(
            db,
            """
            SELECT tablename
            FROM pg_catalog.pg_tables
            WHERE schemaname NOT IN ('pg_catalog','information_schema')
            ORDER BY tablename;
            """,
        )
        return [r[0] for r in rows] if rows else []
    else:
        rows = _q_exec(db, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name") or []
        return [r["name"] if isinstance(r, sqlite3.Row) else r[0] for r in rows]


def _get_columns_generic(db: _DBHandle, table: str) -> List[Dict[str, Any]]:
    if db.kind == "pg":
        rows = _q_exec(
            db,
            """
            SELECT
              ordinal_position - 1 AS cid,
              column_name      AS name,
              data_type        AS type,
              CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END AS notnull,
              column_default   AS dflt_value,
              0                AS pk
            FROM information_schema.columns
            WHERE table_name = %s
            ORDER BY ordinal_position;
            """,
            (table,),
        ) or []
        cols = []
        for r in rows:
            cid, name, typ, notnull, dflt_value, pk = r
            cols.append(
                {
                    "cid": cid,
                    "name": name,
                    "type": str(typ or ""),
                    "notnull": int(notnull or 0),
                    "dflt_value": dflt_value,
                    "pk": int(pk or 0),
                }
            )
        return cols
    else:
        rows = _q_exec(db, f'PRAGMA table_info("{table}")') or []
        cols = []
        for r in rows:
            d = dict(r)
            cols.append(
                {
                    "cid": d.get("cid"),
                    "name": d.get("name"),
                    "type": d.get("type") or "",
                    "notnull": d.get("notnull") or 0,
                    "dflt_value": d.get("dflt_value"),
                    "pk": d.get("pk") or 0,
                }
            )
        return cols


def _pick_time_column(columns: List[Dict[str, Any]]) -> Optional[str]:
    candidates = ["timestamp_utc", "timestamp", "created_at", "ts", "time", "at"]
    names = {c["name"] for c in columns}
    for cand in candidates:
        for n in names:
            if n.lower() == cand:
                return n
    return None


def _text_columns(columns: List[Dict[str, Any]]) -> List[str]:
    text_like = []
    for c in columns:
        coltype = (c.get("type") or "").upper()
        if any(t in coltype for t in ("CHAR", "CLOB", "TEXT", "STRING", "VARCHAR")) or coltype == "":
            text_like.append(c["name"])
    return text_like


def _build_where_and_params(
    *,
    table: str,
    columns: List[Dict[str, Any]],
    search: Optional[str],
    start: Optional[str],
    end: Optional[str],
    extra_filters: Dict[str, str] | None = None,
) -> Tuple[str, List[Any]]:
    where_parts: List[str] = []
    params: List[Any] = []

    col_names = {c["name"] for c in columns}
    text_cols = _text_columns(columns)
    time_col = _pick_time_column(columns)

    if search and text_cols:
        like = f"%{search}%"
        or_parts = [f'"{c}" LIKE %s' for c in text_cols]
        where_parts.append("(" + " OR ".join(or_parts) + ")")
        params.extend([like] * len(text_cols))

    if time_col and (start or end):
        if start:
            where_parts.append(f'"{time_col}" >= %s')
            params.append(start)
        if end:
            where_parts.append(f'"{time_col}" <= %s')
            params.append(end)

    if extra_filters:
        for k, v in (extra_filters or {}).items():
            if k in col_names and v is not None:
                where_parts.append(f'"{k}" = %s')
                params.append(v)

    where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    return where_clause, params


def _validate_ordering(order_by: Optional[str], order_dir: str, columns: List[Dict[str, Any]]) -> Tuple[str, str]:
    valid_names = {c["name"] for c in columns}
    if order_by and order_by not in valid_names:
        raise HTTPException(status_code=400, detail=f"Invalid order_by column '{order_by}'")
    direction = "DESC" if (order_dir or "").lower() in ("desc", "d", "1", "true", "yes") else "ASC"
    chosen_col = order_by or (
        next((c for c in ("timestamp_utc", "timestamp", "created_at", "id") if c in valid_names), list(valid_names)[0])
    )
    return chosen_col, direction


def _paged_query(
    db: _DBHandle,
    table: str,
    where: str,
    params: List[Any],
    order_by: str,
    order_dir: str,
    limit: int,
    offset: int,
) -> Tuple[List[Dict[str, Any]], int]:
    q_count = f'SELECT COUNT(*) AS cnt FROM "{table}"{where}'
    row = _q_one(db, q_count, tuple(params))
    if row is None:
        total = 0
    else:
        total = row[0] if not isinstance(row, sqlite3.Row) else row["cnt"]

    q_rows = f'SELECT * FROM "{table}"{where} ORDER BY "{order_by}" {order_dir} LIMIT %s OFFSET %s'
    rows_raw = _q_exec(db, q_rows, tuple(params + [limit, offset])) or []

    rows: List[Dict[str, Any]] = []
    if db.kind == "pg":
        # Build dicts using column names from metadata
        cols_meta = _get_columns_generic(db, table)
        colnames = [c["name"] for c in cols_meta]
        for tup in rows_raw:
            rows.append({colnames[i]: tup[i] if i < len(tup) else None for i in range(len(colnames))})
    else:
        rows = [dict(r) for r in rows_raw]

    return rows, int(total)


# ──────────────────────────────────────────────────────────────────────────────
# Core fetchers (table-specific wrappers)
# ──────────────────────────────────────────────────────────────────────────────
def _fetch_table(
    project: str,
    table: str,
    *,
    limit: int,
    offset: int,
    search: Optional[str],
    start: Optional[str],
    end: Optional[str],
    order_by: Optional[str],
    order_dir: str,
    filters: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    debug("_fetch_table.begin", {"project": project, "table": table})
    project_path = _project_path(project)
    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    with _open_nodes_db(project_path, project) as db:
        columns = _get_columns_generic(db, table)
        where, params = _build_where_and_params(
            table=table, columns=columns, search=search, start=start, end=end, extra_filters=filters
        )
        order_col, direction = _validate_ordering(order_by, order_dir, columns)
        rows, total = _paged_query(db, table, where, params, order_col, direction, limit=limit, offset=offset)
        payload = {
            "project": project,
            "table": table,
            "limit": limit,
            "offset": offset,
            "order_by": order_col,
            "order_dir": direction,
            "total": total,
            "rows": rows,
            "columns": [c["name"] for c in columns],
            "db_backend": "postgres" if db.kind == "pg" else "sqlite",
        }
        debug("_fetch_table.end", {"count": len(rows), "total": total})
        return payload


# ──────────────────────────────────────────────────────────────────────────────
# Routes: discovery & schema
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception as e:
        print(f"[list_projects] list_projects failed: {e!r}")
        return []


@router.get("/{project}/tables")
def list_nodes_tables(project: str):
    debug("GET /tables", {"project": project})
    project_path = _project_path(project)
    with _open_nodes_db(project_path, project) as db:
        tables = _list_tables_generic(db)
    return {"project": project, "tables": tables}


@router.get("/{project}/schema/{table}")
def get_table_schema(project: str, table: str):
    debug("GET /schema/{table}", {"project": project, "table": table})
    project_path = _project_path(project)
    with _open_nodes_db(project_path, project) as db:
        columns = _get_columns_generic(db, table)
    return {"project": project, "table": table, "columns": columns}


# ──────────────────────────────────────────────────────────────────────────────
# Common query params
# ──────────────────────────────────────────────────────────────────────────────
def _common_query_params():
    return {
        "limit": Query(100, ge=1, le=1000, description="Max rows to return"),
        "offset": Query(0, ge=0, description="Row offset"),
        "search": Query(None, description="Text search across text-like columns"),
        "start": Query(None, description="Start time (applies if table has a timestamp-like column)"),
        "end": Query(None, description="End time (applies if table has a timestamp-like column)"),
        "order_by": Query(None, description="Column to order by"),
        "order_dir": Query("desc", description="asc|desc"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Routes: viewers for compliance_log and audit_log
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/{project}/compliance")
def list_compliance(
    project: str,
    limit: int = _common_query_params()["limit"],
    offset: int = _common_query_params()["offset"],
    search: Optional[str] = _common_query_params()["search"],
    start: Optional[str] = _common_query_params()["start"],
    end: Optional[str] = _common_query_params()["end"],
    order_by: Optional[str] = _common_query_params()["order_by"],
    order_dir: str = _common_query_params()["order_dir"],
    actor: Optional[str] = Query(None, description="Exact match filter if column exists"),
    action: Optional[str] = Query(None, description="Exact match filter if column exists"),
    node: Optional[str] = Query(None, description="Exact match filter if column exists"),
    module: Optional[str] = Query(None, description="Exact match filter if column exists"),
):
    debug(
        "GET /compliance",
        {
            "project": project,
            "params": dict(
                limit=limit,
                offset=offset,
                search=search,
                start=start,
                end=end,
                order_by=order_by,
                order_dir=order_dir,
                actor=actor,
                action=action,
                node=node,
                module=module,
            ),
        },
    )
    filters = {k: v for k, v in {"actor": actor, "action": action, "node": node, "module": module}.items() if v is not None}
    return _fetch_table(
        project,
        "compliance_log",
        limit=limit,
        offset=offset,
        search=search,
        start=start,
        end=end,
        order_by=order_by,
        order_dir=order_dir,
        filters=filters,
    )


@router.get("/{project}/audit")
def list_audit(
    project: str,
    limit: int = _common_query_params()["limit"],
    offset: int = _common_query_params()["offset"],
    search: Optional[str] = _common_query_params()["search"],
    start: Optional[str] = _common_query_params()["start"],
    end: Optional[str] = _common_query_params()["end"],
    order_by: Optional[str] = _common_query_params()["order_by"],
    order_dir: str = _common_query_params()["order_dir"],
    user_id: Optional[str] = Query(None, description="Exact match filter if column exists"),
    run_id: Optional[str] = Query(None, description="Exact match filter if column exists"),
    verb: Optional[str] = Query(None, description="Exact match filter if column exists"),
):
    debug(
        "GET /audit",
        {
            "project": project,
            "params": dict(
                limit=limit,
                offset=offset,
                search=search,
                start=start,
                end=end,
                order_by=order_by,
                order_dir=order_dir,
                user_id=user_id,
                run_id=run_id,
                verb=verb,
            ),
        },
    )
    filters = {k: v for k, v in {"user_id": user_id, "run_id": run_id, "verb": verb}.items() if v is not None}
    return _fetch_table(
        project,
        "audit_log",
        limit=limit,
        offset=offset,
        search=search,
        start=start,
        end=end,
        order_by=order_by,
        order_dir=order_dir,
        filters=filters,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Routes: generic table browse (optional but handy)
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/{project}/table/{table}")
def browse_any_table(
    project: str,
    table: str,
    limit: int = _common_query_params()["limit"],
    offset: int = _common_query_params()["offset"],
    search: Optional[str] = _common_query_params()["search"],
    start: Optional[str] = _common_query_params()["start"],
    end: Optional[str] = _common_query_params()["end"],
    order_by: Optional[str] = _common_query_params()["order_by"],
    order_dir: str = _common_query_params()["order_dir"],
):
    debug(
        "GET /table/{table}",
        {
            "project": project,
            "table": table,
            "params": dict(
                limit=limit,
                offset=offset,
                search=search,
                start=start,
                end=end,
                order_by=order_by,
                order_dir=order_dir,
            ),
        },
    )
    return _fetch_table(
        project,
        table,
        limit=limit,
        offset=offset,
        search=search,
        start=start,
        end=end,
        order_by=order_by,
        order_dir=order_dir,
        filters=None,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Routes: export (CSV / JSON)
# ──────────────────────────────────────────────────────────────────────────────
def _export_rows(
    project: str,
    table: str,
    *,
    search: Optional[str],
    start: Optional[str],
    end: Optional[str],
    filters: Dict[str, str] | None,
    order_by: Optional[str],
    order_dir: str,
    max_rows: int = 1_000_000,  # safety cap
) -> Tuple[List[str], List[Dict[str, Any]]]:
    project_path = _project_path(project)
    with _open_nodes_db(project_path, project) as db:
        cols_meta = _get_columns_generic(db, table)
        where, params = _build_where_and_params(
            table=table, columns=cols_meta, search=search, start=start, end=end, extra_filters=filters
        )
        order_col, direction = _validate_ordering(order_by, order_dir, cols_meta)
        q = f'SELECT * FROM "{table}"{where} ORDER BY "{order_col}" {direction} LIMIT %s'
        rows_raw = _q_exec(db, q, tuple(params + [max_rows])) or []
        columns = [c["name"] for c in cols_meta]

        rows: List[Dict[str, Any]] = []
        if db.kind == "pg":
            for tup in rows_raw:
                rows.append({columns[i]: tup[i] if i < len(tup) else None for i in range(len(columns))})
        else:
            rows = [dict(r) for r in rows_raw]

        return columns, rows


def _csv_stream(columns: List[str], rows: List[Dict[str, Any]]):
    sio = io.StringIO()
    writer = csv.DictWriter(sio, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    yield sio.getvalue()
    sio.seek(0)
    sio.truncate(0)
    for r in rows:
        writer.writerow(r)
        yield sio.getvalue()
        sio.seek(0)
        sio.truncate(0)


def _json_stream(rows: List[Dict[str, Any]]):
    data = json.dumps(rows, ensure_ascii=False)
    yield data


# Compliance exports
@router.get("/{project}/compliance/export.csv")
def export_compliance_csv(
    project: str,
    search: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    order_by: Optional[str] = Query(None),
    order_dir: str = Query("desc"),
    actor: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    node: Optional[str] = Query(None),
    module: Optional[str] = Query(None),
):
    filters = {k: v for k, v in {"actor": actor, "action": action, "node": node, "module": module}.items() if v is not None}
    columns, rows = _export_rows(
        project,
        "compliance_log",
        search=search,
        start=start,
        end=end,
        filters=filters,
        order_by=order_by,
        order_dir=order_dir,
    )
    filename = f"compliance_log__{project}__{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv"
    return StreamingResponse(
        _csv_stream(columns, rows),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{project}/compliance/export.json")
def export_compliance_json(
    project: str,
    search: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    order_by: Optional[str] = Query(None),
    order_dir: str = Query("desc"),
    actor: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    node: Optional[str] = Query(None),
    module: Optional[str] = Query(None),
):
    filters = {k: v for k, v in {"actor": actor, "action": action, "node": node, "module": module}.items() if v is not None}
    _, rows = _export_rows(
        project,
        "compliance_log",
        search=search,
        start=start,
        end=end,
        filters=filters,
        order_by=order_by,
        order_dir=order_dir,
    )
    filename = f"compliance_log__{project}__{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
    return StreamingResponse(
        _json_stream(rows),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Audit exports
@router.get("/{project}/audit/export.csv")
def export_audit_csv(
    project: str,
    search: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    order_by: Optional[str] = Query(None),
    order_dir: str = Query("desc"),
    user_id: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
    verb: Optional[str] = Query(None),
):
    filters = {k: v for k, v in {"user_id": user_id, "run_id": run_id, "verb": verb}.items() if v is not None}
    columns, rows = _export_rows(
        project,
        "audit_log",
        search=search,
        start=start,
        end=end,
        filters=filters,
        order_by=order_by,
        order_dir=order_dir,
    )
    filename = f"audit_log__{project}__{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv"
    return StreamingResponse(
        _csv_stream(columns, rows),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{project}/audit/export.json")
def export_audit_json(
    project: str,
    search: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    order_by: Optional[str] = Query(None),
    order_dir: str = Query("desc"),
    user_id: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
    verb: Optional[str] = Query(None),
):
    filters = {k: v for k, v in {"user_id": user_id, "run_id": run_id, "verb": verb}.items() if v is not None}
    _, rows = _export_rows(
        project,
        "audit_log",
        search=search,
        start=start,
        end=end,
        filters=filters,
        order_by=order_by,
        order_dir=order_dir,
    )
    filename = f"audit_log__{project}__{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
    return StreamingResponse(
        _json_stream(rows),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ──────────────────────────────────────────────────────────────────────────────
# File-Watcher Endpoints (Instrument Compliance Runtime)
# Monitors folders for new files and automatically logs them to compliance system
#
# NOTE: This is intended for **local agent / on-prem** deployments.
# A hosted-only instance cannot see instrument PCs' file systems.
# ──────────────────────────────────────────────────────────────────────────────

import asyncio
import hashlib
from typing import Any as _Any

try:
    from watchfiles import awatch, Change  # type: ignore

    _GIMS_FILEWATCH_AVAILABLE = True
except Exception:
    _GIMS_FILEWATCH_AVAILABLE = False

# SQLAlchemy metadata & table for persisted folder config
_sa_metadata = MetaData()

filewatch_folders = Table(
    "filewatch_folders",
    _sa_metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project", String(128), nullable=False, index=True),
    Column("folder", String(1024), nullable=False),
    Column("label", String(256), nullable=True),
    Column("active", Boolean, nullable=False, default=False),
    Column("created_at", DateTime, nullable=False, default=datetime.utcnow),
)


def _nodes_sqlalchemy_url(project: str) -> str:
    """
    Produce a SQLAlchemy URL for nodes_db for this project, normalizing async drivers.
    Mirrors the same RDS/local handling as _get_nodes_dsn/_normalize_for_psycopg,
    but in SQLAlchemy URL form.
    """
    try:
        raw = get_db_uri("nodes_db", project=project)
    except Exception:
        raw = None

    if raw:
        # normalize async drivers first
        if raw.startswith("sqlite+aiosqlite"):
            url = raw.replace("sqlite+aiosqlite", "sqlite")
        elif raw.startswith("postgresql"):
            # Reuse the psycopg normalizer to fix '?ssl=require' → '?sslmode=require'
            url = _normalize_for_psycopg(raw)
        else:
            url = raw

        return url

    # Fallback: resolve sqlite path via manifest
    proj_path = _project_path(project)
    db_path = resolve_path(proj_path, "nodes_db")
    return f"sqlite:///{db_path.as_posix()}"


def _get_sa_engine(project: str):
    url = _nodes_sqlalchemy_url(project)
    debug("filewatch:sqlalchemy_engine_url", {"project": project, "url": url})
    engine = create_engine(url, future=True)
    # Ensure table exists
    _sa_metadata.create_all(engine, tables=[filewatch_folders])
    return engine


def _db_load_folders(project: str) -> List[str]:
    try:
        engine = _get_sa_engine(project)
    except Exception as e:
        debug("filewatch:db_load_folders:error", {"project": project, "error": str(e)})
        return []
    with engine.begin() as conn:
        rows = conn.execute(
            select(filewatch_folders.c.folder).where(filewatch_folders.c.project == project)
        ).fetchall()
    folders = [r[0] for r in rows]
    debug("filewatch:db_load_folders", {"project": project, "folders": folders})
    return folders


def _db_save_folders(project: str, folders: List[str]) -> None:
    try:
        engine = _get_sa_engine(project)
    except Exception as e:
        debug("filewatch:db_save_folders:error", {"project": project, "error": str(e)})
        return
    folders = [f.strip() for f in folders if f and f.strip()]
    with engine.begin() as conn:
        conn.execute(
            delete(filewatch_folders).where(filewatch_folders.c.project == project)
        )
        if folders:
            conn.execute(
                filewatch_folders.insert(),
                [
                    {
                        "project": project,
                        "folder": f,
                        "active": False,
                        "created_at": datetime.utcnow(),
                    }
                    for f in folders
                ],
            )
    debug("filewatch:db_save_folders", {"project": project, "folders": folders})


# ──────────────────────────────────────────────────────────────────────────────
# Data structures (runtime status)
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class FileEvent:
    """Represents a detected file event"""

    filename: str
    path: str
    sha256: str
    size: int
    timestamp: float
    logged: bool = False
    error: Optional[str] = None


@dataclass
class WatcherStatus:
    """Status of a single folder watcher"""

    folder: str
    exists: bool
    active: bool
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    files_detected: int = 0
    files_logged: int = 0
    last_event: Optional[FileEvent] = None
    last_error: Optional[str] = None
    last_health_check: Optional[float] = None
    health_check_ok: Optional[bool] = None


@dataclass
class ProjectWatcherCtx:
    """Per-project watcher context with multi-folder support"""

    project: str
    base_url: str = ""
    token: Optional[str] = None
    watchers: Dict[str, WatcherStatus] = field(default_factory=dict)
    tasks: Dict[str, asyncio.Task] = field(default_factory=dict)
    recent_events: List[FileEvent] = field(default_factory=list)
    max_recent_events: int = 50


_FILEWATCHERS: Dict[str, ProjectWatcherCtx] = {}
debug("filewatch: initial _FILEWATCHERS created", {"projects": list(_FILEWATCHERS.keys())})


# ──────────────────────────────────────────────────────────────────────────────
# Helper functions
# ──────────────────────────────────────────────────────────────────────────────
def _get_ctx(project: str) -> ProjectWatcherCtx:
    """Get or create project watcher context and hydrate from DB if new."""
    debug("filewatch:_get_ctx:start", {"project": project, "existing_projects": list(_FILEWATCHERS.keys())})
    if project not in _FILEWATCHERS:
        ctx = ProjectWatcherCtx(project=project)
        _FILEWATCHERS[project] = ctx
        # Hydrate configured folders from DB
        try:
            folders = _db_load_folders(project)
        except Exception as e:
            debug("filewatch:_get_ctx:db_load_error", {"project": project, "error": str(e)})
            folders = []
        for folder in folders:
            ctx.watchers[folder] = WatcherStatus(
                folder=folder,
                exists=os.path.isdir(folder),
                active=False,
            )
        debug(
            "filewatch:_get_ctx:create_new",
            {
                "project": project,
                "folders_loaded": folders,
            },
        )
    ctx = _FILEWATCHERS[project]
    debug(
        "filewatch:_get_ctx:end",
        {
            "project": ctx.project,
            "watchers_count": len(ctx.watchers),
            "tasks_count": len(ctx.tasks),
            "base_url": ctx.base_url,
            "has_token": bool(ctx.token),
        },
    )
    return ctx


def _extract_bearer(request: Request) -> Optional[str]:
    """Extract bearer token from Authorization header"""
    header = request.headers.get("Authorization", "")
    debug("filewatch:_extract_bearer:start", {"header_present": bool(header)})
    if not header:
        debug("filewatch:_extract_bearer:no_header")
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2:
        debug("filewatch:_extract_bearer:invalid_format", {"header": header})
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        debug("filewatch:_extract_bearer:invalid_scheme", {"scheme": scheme})
        return None
    token = token.strip() or None
    debug("filewatch:_extract_bearer:end", {"has_token": bool(token)})
    return token


async def _compute_sha256(path: str) -> str:
    """Compute SHA256 hash of file asynchronously"""
    debug("filewatch:_compute_sha256:start", {"path": path})
    loop = asyncio.get_event_loop()

    def _hash():
        debug("filewatch:_compute_sha256:_hash:begin", {"path": path})
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        digest = h.hexdigest()
        debug("filewatch:_compute_sha256:_hash:end", {"path": path, "sha256": digest})
        return digest

    result = await loop.run_in_executor(None, _hash)
    debug("filewatch:_compute_sha256:end", {"path": path, "sha256": result})
    return result


async def _wait_for_file_stable(path: str, max_attempts: int = 10) -> bool:
    """Wait until file size stabilizes (file finished writing)"""
    debug("filewatch:_wait_for_file_stable:start", {"path": path, "max_attempts": max_attempts})
    prev_size = -1

    for attempt in range(max_attempts):
        try:
            curr_size = os.path.getsize(path)
            debug(
                "filewatch:_wait_for_file_stable:check",
                {
                    "path": path,
                    "attempt": attempt,
                    "prev_size": prev_size,
                    "curr_size": curr_size,
                },
            )
            if curr_size == prev_size and curr_size > 0:
                debug(
                    "filewatch:_wait_for_file_stable:stable",
                    {"path": path, "size": curr_size, "attempt": attempt},
                )
                return True
            prev_size = curr_size
            await asyncio.sleep(0.05)
        except OSError as e:
            debug(
                "filewatch:_wait_for_file_stable:oserror",
                {"path": path, "attempt": attempt, "error": str(e)},
            )
            await asyncio.sleep(0.05)
            continue

    result = prev_size > 0
    debug(
        "filewatch:_wait_for_file_stable:end",
        {"path": path, "stable": result, "final_size": prev_size},
    )
    return result


async def _append_to_compliance(ctx: ProjectWatcherCtx, event: FileEvent):
    """Append file event to compliance log via compliance node."""
    debug(
        "filewatch:_append_to_compliance:start",
        {
            "project": ctx.project,
            "base_url": ctx.base_url,
            "has_token": bool(ctx.token),
            "event": {
                "filename": event.filename,
                "path": f"/api/nodes_compliance/{ctx.project}/filewatch/detect",
                "size": event.size,
                "timestamp": event.timestamp,
                "sha256": event.sha256,
            },
        },
    )
    import httpx

    payload = {
        "filename": event.filename,
        "path": event.path,
        "sha256": event.sha256,
        "size": event.size,
        "timestamp": event.timestamp,
    }

    body = {
        "project": ctx.project,
        "method": "FILE",
        "path": f"/api/nodes_compliance/{ctx.project}/filewatch/detect",
        "payload": payload,
        "status": 200,
        "ids": f"RawFile:{event.filename}",
    }

    headers: Dict[str, str] = {}
    if ctx.token:
        headers["Authorization"] = f"Bearer {ctx.token}"

    url = f"{ctx.base_url}/compliance/log/append"
    debug(
        "filewatch:_append_to_compliance:request",
        {"url": url, "headers_has_auth": "Authorization" in headers},
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, json=body, headers=headers)
        debug("filewatch:_append_to_compliance:response", {"status_code": r.status_code})
        if r.status_code >= 400:
            err_text = r.text[:200]
            debug(
                "filewatch:_append_to_compliance:error",
                {"status": r.status_code, "body": err_text},
            )
            raise RuntimeError(f"Compliance log failed: {r.status_code} {err_text}")

        event.logged = True
        debug("filewatch:_append_to_compliance:success", {"filename": event.filename})


async def _health_check(ctx: ProjectWatcherCtx) -> bool:
    """Check if we can reach the compliance API (uses /compliance/ping)."""
    debug(
        "filewatch:_health_check:start",
        {
            "project": ctx.project,
            "base_url": ctx.base_url,
            "has_token": bool(ctx.token),
        },
    )
    import httpx

    url = f"{ctx.base_url}/compliance/ping"
    params = {"project": ctx.project}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url, params=params)
            ok = r.status_code < 400
            debug(
                "filewatch:_health_check:response",
                {"status_code": r.status_code, "ok": ok},
            )
            return ok
    except Exception as e:
        debug("filewatch:_health_check:exception", {"error": str(e)})
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Background watch loop
# ──────────────────────────────────────────────────────────────────────────────
async def _watch_folder(ctx: ProjectWatcherCtx, folder: str):
    """Background task to watch a single folder"""
    debug(
        "filewatch:_watch_folder:start",
        {
            "project": ctx.project,
            "folder": folder,
            "watchfiles_available": _GIMS_FILEWATCH_AVAILABLE,
        },
    )

    if not _GIMS_FILEWATCH_AVAILABLE:
        status = ctx.watchers.get(folder)
        if status:
            status.last_error = "watchfiles package not installed"
            status.active = False
            debug(
                "filewatch:_watch_folder:no_watchfiles",
                {"folder": folder, "last_error": status.last_error},
            )
        return

    status = ctx.watchers.get(folder)
    if not status:
        debug("filewatch:_watch_folder:no_status_found", {"folder": folder})
        return

    status.active = True
    status.started_at = time.time()
    status.last_error = None
    debug(
        "filewatch:_watch_folder:initialized",
        {"folder": folder, "started_at": status.started_at},
    )

    try:
        async for changes in awatch(folder):
            debug(
                "filewatch:_watch_folder:changes_batch",
                {"folder": folder, "changes_count": len(changes)},
            )
            for change, path in changes:
                debug(
                    "filewatch:_watch_folder:change_seen",
                    {"folder": folder, "change": str(change), "path": path},
                )
                # Only process new files
                if change != Change.added:
                    debug(
                        "filewatch:_watch_folder:skip_change",
                        {"reason": "not_added", "change": str(change)},
                    )
                    continue

                # Skip directories
                if os.path.isdir(path):
                    debug("filewatch:_watch_folder:skip_directory", {"path": path})
                    continue

                try:
                    # Wait for file to finish writing
                    stable = await _wait_for_file_stable(path)
                    debug(
                        "filewatch:_watch_folder:file_stable_check",
                        {"path": path, "stable": stable},
                    )
                    if not stable:
                        raise RuntimeError("File did not stabilize")

                    # Compute hash and get metadata
                    size = os.path.getsize(path)
                    sha = await _compute_sha256(path)

                    event = FileEvent(
                        filename=os.path.basename(path),
                        path=os.path.abspath(path),
                        sha256=sha,
                        size=size,
                        timestamp=time.time(),
                    )
                    debug(
                        "filewatch:_watch_folder:event_created",
                        {
                            "filename": event.filename,
                            "path": f"/api/nodes_compliance/{ctx.project}/filewatch/detect",
                            "size": event.size,
                            "timestamp": event.timestamp,
                        },
                    )

                    status.files_detected += 1
                    status.last_event = event
                    status.last_error = None
                    debug(
                        "filewatch:_watch_folder:status_updated",
                        {
                            "folder": folder,
                            "files_detected": status.files_detected,
                            "files_logged": status.files_logged,
                        },
                    )

                    ctx.recent_events.insert(0, event)
                    if len(ctx.recent_events) > ctx.max_recent_events:
                        ctx.recent_events.pop()
                    debug(
                        "filewatch:_watch_folder:recent_events_updated",
                        {
                            "count": len(ctx.recent_events),
                            "max": ctx.max_recent_events,
                        },
                    )

                    try:
                        await _append_to_compliance(ctx, event)
                        status.files_logged += 1
                        debug(
                            "filewatch:_watch_folder:logged_to_compliance",
                            {
                                "folder": folder,
                                "files_logged": status.files_logged,
                            },
                        )
                    except Exception as e:
                        event.error = str(e)
                        status.last_error = f"Compliance log error: {str(e)}"
                        debug(
                            "filewatch:_watch_folder:compliance_error",
                            {"folder": folder, "error": status.last_error},
                        )

                except Exception as e:
                    status.last_error = f"File processing error: {str(e)}"
                    debug(
                        "filewatch:_watch_folder:file_processing_error",
                        {"folder": folder, "path": path, "error": status.last_error},
                    )

    except asyncio.CancelledError:
        status.active = False
        status.stopped_at = time.time()
        debug(
            "filewatch:_watch_folder:cancelled",
            {"folder": folder, "stopped_at": status.stopped_at},
        )
        raise

    except Exception as e:
        status.active = False
        status.last_error = f"Watcher crashed: {str(e)}"
        status.stopped_at = time.time()
        debug(
            "filewatch:_watch_folder:crashed",
            {"folder": folder, "error": status.last_error, "stopped_at": status.stopped_at},
        )


async def _stop_watcher(ctx: ProjectWatcherCtx, folder: str):
    """Stop watching a specific folder"""
    debug(
        "filewatch:_stop_watcher:start",
        {"project": ctx.project, "folder": folder, "tasks_keys": list(ctx.tasks.keys())},
    )
    task = ctx.tasks.get(folder)
    if task and not task.done():
        debug("filewatch:_stop_watcher:cancelling_task", {"folder": folder})
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            debug("filewatch:_stop_watcher:task_cancelled", {"folder": folder})
            pass

    ctx.tasks.pop(folder, None)

    status = ctx.watchers.get(folder)
    if status:
        status.active = False
        status.stopped_at = time.time()
        debug(
            "filewatch:_stop_watcher:status_updated",
            {"folder": folder, "stopped_at": status.stopped_at},
        )
    else:
        debug("filewatch:_stop_watcher:no_status_to_update", {"folder": folder})

    debug("filewatch:_stop_watcher:end", {"folder": folder})


# ──────────────────────────────────────────────────────────────────────────────
# API Endpoints for filewatch
# ──────────────────────────────────────────────────────────────────────────────
async def _safe_json(request: Request) -> Dict[str, _Any]:
    try:
        return await request.json()
    except Exception:
        return {}


@router.post("/{project}/filewatch/configure")
async def filewatch_configure(project: str, request: Request):
    """
    Configure the file watcher system for a project.

    Body JSON (from UI):
        { "folders": ["C:/NMR/Exports", "D:/Data/Results"] }

    This persists the folder list in nodes_db via SQLAlchemy and
    hydrates in-memory watcher status.
    """
    body = await _safe_json(request)
    debug("filewatch:filewatch_configure:start", {"project": project, "body": body})
    ctx = _get_ctx(project)

    # Update base URL and token
    ctx.base_url = f"{request.url.scheme}://{request.url.netloc}"
    ctx.token = _extract_bearer(request)
    debug(
        "filewatch:filewatch_configure:ctx_updated",
        {"base_url": ctx.base_url, "has_token": bool(ctx.token)},
    )

    folders = body.get("folders", [])
    debug("filewatch:filewatch_configure:folders_raw", {"folders": folders})
    if not isinstance(folders, list):
        debug(
            "filewatch:filewatch_configure:error_invalid_folders",
            {"type": type(folders).__name__},
        )
        return {"ok": False, "error": "'folders' must be a list"}

    # Persist config via SQLAlchemy
    _db_save_folders(project, folders)

    results = []

    # Replace ctx.watchers from persisted list
    ctx.watchers.clear()
    for folder in _db_load_folders(project):
        exists = os.path.isdir(folder)
        debug("filewatch:filewatch_configure:folder_entry", {"folder": folder, "exists": exists})
        ctx.watchers[folder] = WatcherStatus(
            folder=folder,
            exists=exists,
            active=False,
        )
        results.append({"folder": folder, "exists": exists, "valid": exists})

    # Health check against compliance/ping
    health_ok = await _health_check(ctx)
    debug("filewatch:filewatch_configure:health_check", {"health_ok": health_ok})

    for folder_status in ctx.watchers.values():
        folder_status.last_health_check = time.time()
        folder_status.health_check_ok = health_ok

    resp = {
        "ok": True,
        "project": project,
        "folders": results,
        "health_check_ok": health_ok,
        "watchfiles_available": _GIMS_FILEWATCH_AVAILABLE,
        "has_auth": ctx.token is not None,
    }
    debug("filewatch:filewatch_configure:end", resp)
    return resp


@router.post("/{project}/filewatch/start")
async def filewatch_start(project: str, request: Request):
    """
    Start watching one or more folders.

    Body JSON:
        { "folders": ["C:/NMR/Exports"] }  // Optional, starts all if not specified
    """
    body = await _safe_json(request)
    debug("filewatch:filewatch_start:start", {"project": project, "body": body})
    ctx = _get_ctx(project)

    folders_to_start = body.get("folders", [])
    if not folders_to_start:
        folders_to_start = list(ctx.watchers.keys())
        debug(
            "filewatch:filewatch_start:no_folders_specified_start_all",
            {"folders": folders_to_start},
        )
    else:
        debug("filewatch:filewatch_start:folders_specified", {"folders": folders_to_start})

    started = []
    errors = []

    for folder in folders_to_start:
        debug("filewatch:filewatch_start:process_folder", {"folder": folder})
        status = ctx.watchers.get(folder)

        if not status:
            err = "Not configured"
            errors.append({"folder": folder, "error": err})
            debug("filewatch:filewatch_start:error", {"folder": folder, "error": err})
            continue

        if not status.exists:
            err = "Folder does not exist"
            errors.append({"folder": folder, "error": err})
            debug("filewatch:filewatch_start:error", {"folder": folder, "error": err})
            continue

        if status.active:
            err = "Already running"
            errors.append({"folder": folder, "error": err})
            debug("filewatch:filewatch_start:error", {"folder": folder, "error": err})
            continue

        # Stop any existing task
        await _stop_watcher(ctx, folder)

        # Start new task
        loop = asyncio.get_running_loop()
        ctx.tasks[folder] = loop.create_task(_watch_folder(ctx, folder))
        debug("filewatch:filewatch_start:task_created", {"folder": folder})

        started.append(folder)

    resp = {
        "ok": True,
        "project": project,
        "started": started,
        "errors": errors if errors else None,
    }
    debug("filewatch:filewatch_start:end", resp)
    return resp


@router.post("/{project}/filewatch/stop")
async def filewatch_stop(project: str, request: Request):
    """
    Stop watching one or more folders.

    Body JSON:
        { "folders": ["C:/NMR/Exports"] }  // Optional, stops all if not specified
    """
    body = await _safe_json(request)
    debug("filewatch:filewatch_stop:start", {"project": project, "body": body})
    ctx = _get_ctx(project)

    folders_to_stop = body.get("folders", [])
    if not folders_to_stop:
        folders_to_stop = list(ctx.tasks.keys())
        debug(
            "filewatch:filewatch_stop:no_folders_specified_stop_all",
            {"folders": folders_to_stop},
        )
    else:
        debug("filewatch:filewatch_stop:folders_specified", {"folders": folders_to_stop})

    stopped = []

    for folder in folders_to_stop:
        debug("filewatch:filewatch_stop:process_folder", {"folder": folder})
        if folder in ctx.tasks:
            await _stop_watcher(ctx, folder)
            stopped.append(folder)
            debug("filewatch:filewatch_stop:stopped_folder", {"folder": folder})
        else:
            debug("filewatch:filewatch_stop:no_task_for_folder", {"folder": folder})

    resp = {
        "ok": True,
        "project": project,
        "stopped": stopped,
    }
    debug("filewatch:filewatch_stop:end", resp)
    return resp


@router.get("/{project}/filewatch/status")
async def filewatch_status(project: str, request: Request):
    """
    Get comprehensive status of all watchers.

    Returns detailed status plus:
      - watchfiles_available (for "System" indicator)
      - has_auth (for auth indicator)
    """
    debug("filewatch:filewatch_status:start", {"project": project})
    ctx = _get_ctx(project)

    # Initialize base_url and token if not already set
    if not ctx.base_url:
        ctx.base_url = f"{request.url.scheme}://{request.url.netloc}"
        ctx.token = _extract_bearer(request)
        debug(
            "filewatch:filewatch_status:ctx_initialized",
            {"base_url": ctx.base_url, "has_token": bool(ctx.token)},
        )

    # Automatically run health check if base_url is configured
    if ctx.base_url and ctx.watchers:
        health_ok = await _health_check(ctx)
        for status in ctx.watchers.values():
            status.last_health_check = time.time()
            status.health_check_ok = health_ok
        debug(
            "filewatch:filewatch_status:auto_health_check",
            {"health_ok": health_ok, "watcher_count": len(ctx.watchers)},
        )

    # Update folder existence
    for folder, status in ctx.watchers.items():
        old_exists = status.exists
        status.exists = os.path.isdir(folder)
        debug(
            "filewatch:filewatch_status:update_exists",
            {
                "folder": folder,
                "old_exists": old_exists,
                "new_exists": status.exists,
            },
        )

    watchers = []
    for folder, status in ctx.watchers.items():
        watcher_info: Dict[str, _Any] = {
            "folder": folder,
            "exists": status.exists,
            "active": status.active,
            "started_at": status.started_at,
            "stopped_at": status.stopped_at,
            "files_detected": status.files_detected,
            "files_logged": status.files_logged,
            "last_error": status.last_error,
            "health_check_ok": status.health_check_ok,
            "last_health_check": status.last_health_check,
        }

        if status.last_event:
            watcher_info["last_event"] = {
                "filename": status.last_event.filename,
                "size": status.last_event.size,
                "timestamp": status.last_event.timestamp,
                "logged": status.last_event.logged,
                "error": status.last_event.error,
            }

        watchers.append(watcher_info)
        debug("filewatch:filewatch_status:watcher_entry", watcher_info)

    recent_events = [
        {
            "filename": e.filename,
            "path": e.path,
            "size": e.size,
            "timestamp": e.timestamp,
            "logged": e.logged,
            "error": e.error,
        }
        for e in ctx.recent_events[:10]
    ]
    debug("filewatch:filewatch_status:recent_events", {"count": len(recent_events)})

    # Aggregate a simple API health for UI (any True → True, all False → False, none → None)
    health_values = [w["health_check_ok"] for w in watchers if w["health_check_ok"] is not None]
    if not health_values:
        health_check_ok: Optional[bool] = None
    else:
        health_check_ok = any(bool(v) for v in health_values)

    resp = {
        "ok": True,
        "project": ctx.project,
        "watchers": watchers,
        "recent_events": recent_events,
        "watchfiles_available": _GIMS_FILEWATCH_AVAILABLE,
        "has_auth": ctx.token is not None,
        "base_url": ctx.base_url,
        "health_check_ok": health_check_ok,
    }
    debug("filewatch:filewatch_status:end", resp)
    return resp


@router.delete("/{project}/filewatch/folder")
async def filewatch_remove_folder(project: str, request: Request):
    """
    Remove a folder from watch configuration.

    Body JSON:
        { "folder": "C:/NMR/Exports" }
    """
    body = await _safe_json(request)
    debug("filewatch:filewatch_remove_folder:start", {"project": project, "body": body})
    ctx = _get_ctx(project)

    folder = (body.get("folder") or "").strip()
    debug("filewatch:filewatch_remove_folder:folder_parsed", {"folder": folder})
    if not folder:
        debug("filewatch:filewatch_remove_folder:error_missing_folder")
        return {"ok": False, "error": "Missing 'folder'"}

    # Stop watcher if running
    if folder in ctx.tasks:
        debug("filewatch:filewatch_remove_folder:stopping_task", {"folder": folder})
        await _stop_watcher(ctx, folder)

    existed = folder in ctx.watchers
    ctx.watchers.pop(folder, None)

    # Remove from DB
    try:
        engine = _get_sa_engine(project)
        with engine.begin() as conn:
            conn.execute(
                delete(filewatch_folders).where(
                    (filewatch_folders.c.project == project) & (filewatch_folders.c.folder == folder)
                )
            )
    except Exception as e:
        debug(
            "filewatch:filewatch_remove_folder:db_error",
            {"project": project, "folder": folder, "error": str(e)},
        )

    debug("filewatch:filewatch_remove_folder:removed", {"folder": folder, "existed": existed})

    resp = {
        "ok": True,
        "project": project,
        "folder": folder,
        "removed": True,
    }
    debug("filewatch:filewatch_remove_folder:end", resp)
    return resp


@router.post("/{project}/filewatch/health-check")
async def filewatch_health_check(project: str):
    """
    Perform a health check to verify API connectivity (compliance/ping) and
    update watcher health flags.
    """
    debug("filewatch:filewatch_health_check:start", {"project": project})
    ctx = _get_ctx(project)

    if not ctx.base_url:
        debug("filewatch:filewatch_health_check:error_no_base_url")
        return {
            "ok": False,
            "error": "Not configured - call /filewatch/configure first",
        }

    health_ok = await _health_check(ctx)
    debug("filewatch:filewatch_health_check:health_result", {"health_ok": health_ok})

    for status in ctx.watchers.values():
        status.last_health_check = time.time()
        status.health_check_ok = health_ok
        debug(
            "filewatch:filewatch_health_check:update_status",
            {
                "folder": status.folder,
                "health_check_ok": status.health_check_ok,
                "last_health_check": status.last_health_check,
            },
        )

    resp = {
        "ok": True,
        "project": project,
        "health_check_ok": health_ok,
        "has_auth": ctx.token is not None,
        "base_url": ctx.base_url,
    }
    debug("filewatch:filewatch_health_check:end", resp)
    return resp
