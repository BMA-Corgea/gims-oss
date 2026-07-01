"""DB handle + backend metadata helpers (split verbatim from archive_workbench.py).

SQLite/Postgres-aware connection wrapper and table/column introspection. No
monkeypatch-seam names live here, so these move out of the package __init__.
"""
from __future__ import annotations
from typing import Any, Dict, List, Tuple

from api.storage_aws import normalize_pg_dsn as _normalize_for_psycopg
from utils.logger import get_logger
log = get_logger(__name__)


class _DBHandle:
    def __init__(self, kind: str, conn):
        self.kind = kind  # "pg" or "sqlite"
        self.conn = conn

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

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

def _db_table_exists(db: _DBHandle, table: str) -> bool:
    if db.kind == "sqlite":
        row = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        exists = bool(row)
        log.debug(f"[exists][sqlite] {table} -> {exists}")
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
            log.debug(f"[exists][pg] {table} -> {exists}")
            return exists
        except Exception as e:
            log.debug("[exists][pg] error:", repr(e))
            return False

def _db_columns_simple(db: _DBHandle, table: str) -> List[Tuple[str, str]]:
    """[(name, decl_type)]"""
    if not _db_table_exists(db, table):
        return []
    if db.kind == "sqlite":
        rows = db.conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        out = [(r["name"], (r["type"] or "TEXT")) for r in rows]
        log.debug("[cols][sqlite]", table, "->", out)
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
        log.debug("[cols][pg]", table, "->", out)
        return out
