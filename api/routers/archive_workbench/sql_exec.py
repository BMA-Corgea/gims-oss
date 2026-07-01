"""Plan SQLStep execution + safe commit/rollback (split verbatim from archive_workbench.py).

Runs a single SQLStep against the hot or archive DB, adapting SQLite SQL to
Postgres where needed. RDS/SQLite aware.
"""
from __future__ import annotations
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.archive_workbench import SQLStep

from utils.logger import get_logger
log = get_logger(__name__)

from .db_meta import _convert_qmarks_to_pg
from .index_tables import _ExecContext

try:
    import psycopg  # pip install psycopg[binary]
    from psycopg import errors as pg_errors
    _PSYCOPG_AVAILABLE = True
except Exception as e:
    _PSYCOPG_AVAILABLE = False
    psycopg = None  # type: ignore
    pg_errors = None
    log.debug("psycopg not available:", repr(e))


def _exec_sql_step(ctx: _ExecContext, step: SQLStep):
    """
    Execute a single SQLStep on either the hot or archive DB.
    Automatically adapts SQLite SQL to PostgreSQL syntax and
    rewrites INSERT OR REPLACE for compatibility.

    Additionally, suppresses legacy inserts into runs_archive_index and
    noun_archive_index (we write those canonically elsewhere).
    """
    log.debug("[_exec_sql_step] target:", step.target)
    db = ctx.hot if step.target == "hot" else ctx.arc
    sql = step.sql
    params = list(step.params or [])

    if ctx.last_select_row and any(
        isinstance(x, str) and x.startswith("<") and x.endswith(">")
        for x in params
    ):
        log.debug("[_exec_sql_step] param substitution from last_select_row")
        for i, p in enumerate(params):
            if isinstance(p, str) and p.startswith("<") and p.endswith(">"):
                key = p[1:-1]
                params[i] = ctx.last_select_row.get(key)

    log.debug("[_exec_sql_step] SQL:", sql)
    log.debug("[_exec_sql_step] params:", params)

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
                    log.debug("[_exec_sql_step] SKIP legacy runs_archive_index insert without project/verb (handled canonically later)")
                    return
            else:
                log.debug("[_exec_sql_step] SKIP bare runs_archive_index insert (no column list detected)")
                return
    except Exception as e:
        log.debug("[_exec_sql_step] runs_archive_index suppression check failed (non-fatal):", repr(e))

    try:
        sql_l = sql.strip().lower()
        if sql_l.startswith("insert"):
            if re.search(
                r'insert\s+(?:or\s+replace\s+)?into\s+"?noun_archive_index"?\s*\(',
                sql_l,
                re.IGNORECASE | re.DOTALL,
            ):
                log.debug("[_exec_sql_step] SKIP plan-driven noun_archive_index insert (canonical writer handles it)")
                return
    except Exception as e:
        log.debug("[_exec_sql_step] noun_archive_index suppression check failed (non-fatal):", repr(e))

    if db.kind == "sqlite":
        cur = db.conn.execute(sql, tuple(params))
        if sql.strip().lower().startswith("select"):
            row = cur.fetchone()
            ctx.last_select_row = dict(row) if row else None
            log.debug(
                "[_exec_sql_step] last_select_row keys:",
                list(ctx.last_select_row.keys()) if ctx.last_select_row else None,
            )
        return

    import re as _re

    sql_pg = _convert_qmarks_to_pg(sql, len(params))
    sql_pg_stripped = sql_pg.strip().lower()

    if sql_pg_stripped.startswith("insert or replace"):
        log.debug("[_exec_sql_step][pg] Detected 'INSERT OR REPLACE' — rewriting for Postgres")
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
            log.debug("[_exec_sql_step][pg] Rewritten INSERT OR REPLACE → ON CONFLICT:")
            log.debug(sql_pg)

    log.debug(f"[_exec_sql_step][pg] TRANSLATED SQL: {sql_pg}")
    log.debug(f"[_exec_sql_step][pg] PARAMS: {tuple(params)}")

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
                log.debug(
                    "[_exec_sql_step][pg] last_select_row keys:",
                    list(ctx.last_select_row.keys()) if ctx.last_select_row else None,
                )
    except Exception as e:
        log.debug("[_exec_sql_step][pg] !!! EXECUTION FAILED !!!")
        log.debug(f"[_exec_sql_step][pg] ERROR: {e}")
        log.debug(f"[_exec_sql_step][pg] FAILED SQL: {sql_pg}")
        log.debug(f"[_exec_sql_step][pg] FAILED PARAMS: {tuple(params)}")
        raise e

def _safe_commit(conn):
    try:
        conn.commit()
    except Exception as e:
        if not (pg_errors and isinstance(e, pg_errors.AdminShutdown)):
            log.debug("[commit] failed:", repr(e))
            raise

def _safe_rollback(conn):
    try:
        conn.rollback()
    except Exception as e:
        if not (pg_errors and isinstance(e, pg_errors.AdminShutdown)):
            log.debug("[rollback] failed:", repr(e))
