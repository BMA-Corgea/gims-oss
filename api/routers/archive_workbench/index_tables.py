"""Archive-side table ensure/DDL helpers (split verbatim from archive_workbench.py).

Idempotent creation of the *_archive_index tables, soft-archive columns, and
per-noun archive tables, plus the plan execution context. RDS/SQLite aware.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

from utils.logger import get_logger
log = get_logger(__name__)

from .db_meta import _DBHandle, _db_table_exists, _db_columns_simple

try:
    import psycopg  # pip install psycopg[binary]
    from psycopg import errors as pg_errors
    _PSYCOPG_AVAILABLE = True
except Exception as e:
    _PSYCOPG_AVAILABLE = False
    psycopg = None  # type: ignore
    pg_errors = None
    log.debug("psycopg not available:", repr(e))


class _ExecContext:
    def __init__(self, hot: _DBHandle, arc: _DBHandle):
        self.hot = hot
        self.arc = arc
        self.last_select_row: Optional[Dict[str, Any]] = None  # dict with column names

def _ensure_soft_columns(ctx: _ExecContext, table: str):
    log.debug("[_ensure_soft_columns] table:", table)
    if not _db_table_exists(ctx.hot, table):
        log.debug("[_ensure_soft_columns] table missing; skipping:", table)
        return
    cols = [c[0] for c in _db_columns_simple(ctx.hot, table)]
    if "archived" not in cols:
        if ctx.hot.kind == "sqlite":
            ctx.hot.conn.execute(f'ALTER TABLE "{table}" ADD COLUMN archived INTEGER DEFAULT 0')
        else:
            with ctx.hot.conn.cursor() as cur:
                cur.execute(f'ALTER TABLE "{table}" ADD COLUMN archived INTEGER DEFAULT 0')
        log.debug("[_ensure_soft_columns] added 'archived'")
    if "archived_at" not in cols:
        if ctx.hot.kind == "sqlite":
            ctx.hot.conn.execute(f'ALTER TABLE "{table}" ADD COLUMN archived_at TEXT')
        else:
            with ctx.hot.conn.cursor() as cur:
                cur.execute(f'ALTER TABLE "{table}" ADD COLUMN archived_at TEXT')
        log.debug("[_ensure_soft_columns] added 'archived_at'")

def _ensure_aux_archive_tables(db: _DBHandle):
    """
    Ensure archive-side index tables exist (idempotent), add schema_hash to noun_archive_index
    if missing, and ensure a 'project' column exists on both index tables.
    Uses session timeouts already set on connection to avoid indefinite hangs.
    """
    log.debug(f"[_ensure_aux_archive_tables] START: Ensuring index tables on {db.kind} DB")
    
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
            log.debug("[_ensure_aux_archive_tables][sqlite] Executing DDL1 (noun_archive_index)")
            db.conn.execute(ddl1)
            log.debug("[_ensure_aux_archive_tables][sqlite] Executing DDL2 (runs_archive_index)")
            db.conn.execute(ddl2)
            cols = db.conn.execute('PRAGMA table_info("noun_archive_index")').fetchall()
            have = {r["name"] for r in cols}
            if "schema_hash" not in have:
                log.debug("[_ensure_aux_archive_tables][sqlite] Adding schema_hash column. DDL3:", ddl3)
                db.conn.execute(ddl3)
            else:
                log.debug("[_ensure_aux_archive_tables][sqlite] schema_hash column already exists.")
            for tname in ("noun_archive_index", "runs_archive_index"):
                cols = db.conn.execute(f'PRAGMA table_info("{tname}")').fetchall()
                have = {r["name"] for r in cols}
                if "project" not in have:
                    db.conn.execute(f'ALTER TABLE "{tname}" ADD COLUMN "project" TEXT')
                    log.debug(f"[_ensure_aux_archive_tables][sqlite] Added project column to {tname}")
                else:
                    log.debug(f"[_ensure_aux_archive_tables][sqlite] project column already exists on {tname}")
        except Exception as e:
            log.debug(f"[_ensure_aux_archive_tables][sqlite] !!! EXECUTION FAILED !!! ERROR: {e}")
            raise e
        log.debug("[_ensure_aux_archive_tables][sqlite] FINISHED")
        return

    try:
        with db.conn.cursor() as cur:
            try:
                cur.execute("SET lock_timeout = '3s';")
            except Exception:
                pass
            try:
                log.debug("[_ensure_aux_archive_tables][pg] Executing DDL1 (noun_archive_index)")
                cur.execute(ddl1)
            except psycopg.errors.LockNotAvailable:
                log.debug("[_ensure_aux_archive_tables][pg] DDL1 skipped: lock timeout (already exists or locked)")
            except Exception as e:
                log.debug("[_ensure_aux_archive_tables][pg] DDL1 error:", e)

            try:
                log.debug("[_ensure_aux_archive_tables][pg] Executing DDL2 (runs_archive_index)")
                cur.execute(ddl2)
            except psycopg.errors.LockNotAvailable:
                log.debug("[_ensure_aux_archive_tables][pg] DDL2 skipped: lock timeout (already exists or locked)")
            except Exception as e:
                log.debug("[_ensure_aux_archive_tables][pg] DDL2 error:", e)

            log.debug("[_ensure_aux_archive_tables][pg] Checking existing columns for noun_archive_index")
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name='noun_archive_index'
                """
            )
            have_noun = {r[0] for r in cur.fetchall()}
            log.debug(f"[_ensure_aux_archive_tables][pg] Existing columns (noun_archive_index): {have_noun}")

            if "schema_hash" not in have_noun:
                try:
                    log.debug("[_ensure_aux_archive_tables][pg] Adding schema_hash column. DDL3:", ddl3)
                    cur.execute(ddl3)
                except psycopg.errors.DuplicateColumn:
                    log.debug("[_ensure_aux_archive_tables][pg] schema_hash already exists (race condition)")
                except psycopg.errors.LockNotAvailable:
                    log.debug("[_ensure_aux_archive_tables][pg] DDL3 skipped: lock timeout (already locked)")
                except Exception as e:
                    log.debug("[_ensure_aux_archive_tables][pg] DDL3 error:", e)
            else:
                log.debug("[_ensure_aux_archive_tables][pg] schema_hash column already exists.")

            for tname in ("noun_archive_index", "runs_archive_index"):
                log.debug(f"[_ensure_aux_archive_tables][pg] Checking for project column on {tname}")
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
                        log.debug(f"[_ensure_aux_archive_tables][pg] Added project column to {tname}")
                    except psycopg.errors.DuplicateColumn:
                        log.debug(f"[_ensure_aux_archive_tables][pg] project already exists on {tname}")
                    except psycopg.errors.LockNotAvailable:
                        log.debug(f"[_ensure_aux_archive_tables][pg] project add skipped due to lock on {tname}")
                    except Exception as e:
                        log.debug(f"[_ensure_aux_archive_tables][pg] project column add failed ({tname}):", e)
                else:
                    log.debug(f"[_ensure_aux_archive_tables][pg] project column already exists on {tname}")

    except Exception as e:
        log.debug(f"[_ensure_aux_archive_tables][pg] !!! EXECUTION FAILED !!! ERROR: {e}")
        raise

def _ensure_archive_table(ctx: _ExecContext, src_table: str, dst_table: str, columns: List[Tuple[str, str]], include_meta: bool):
    """
    Ensure the archive table exists *and* has all required columns.
    If the table already exists, add any missing hot or meta columns via ALTER TABLE.
    """
    log.debug(f"[_ensure_archive_table] START: ensuring archive table '{dst_table}'")

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
        
        log.debug("[_ensure_archive_table] Table does not exist. Executing CREATE:")
        log.debug(f"[_ensure_archive_table] DDL: {ddl}")

        if arc.kind == "sqlite":
            arc.conn.execute(ddl)
        else:
            try:
                with arc.conn.cursor() as cur:
                    cur.execute(ddl)
            except Exception as e:
                log.debug("[_ensure_archive_table][pg] !!! CREATE TABLE FAILED !!!")
                log.debug(f"[_ensure_archive_table][pg] ERROR: {e}")
                log.debug(f"[_ensure_archive_table][pg] FAILED DDL: {ddl}")
                raise e
        log.debug(f"[_ensure_archive_table] CREATE TABLE successful for '{dst_table}'")
        return

    log.debug(f"[_ensure_archive_table] Table '{dst_table}' exists. Checking for missing columns...")
    existing = {name: ctype for name, ctype in _db_columns_simple(arc, dst_table)}

    for name, ctype in (columns or []):
        if name not in existing:
            ddl = f'ALTER TABLE "{dst_table}" ADD COLUMN "{name}" {ctype or "TEXT"}'
            log.debug(f"[_ensure_archive_table] Adding missing hot column. DDL: {ddl}")
            if arc.kind == "sqlite":
                arc.conn.execute(ddl)
            else:
                try:
                    with arc.conn.cursor() as cur:
                        cur.execute(ddl)
                except Exception as e:
                    log.debug("[_ensure_archive_table][pg] !!! ALTER TABLE (hot col) FAILED !!!")
                    log.debug(f"[_ensure_archive_table][pg] ERROR: {e}")
                    log.debug(f"[_ensure_archive_table][pg] FAILED DDL: {ddl}")
                    raise e

    for name, ctype in meta_cols:
        if name not in existing:
            ddl = f'ALTER TABLE "{dst_table}" ADD COLUMN "{name}" {ctype}'
            log.debug(f"[_ensure_archive_table] Adding missing meta column. DDL: {ddl}")
            if arc.kind == "sqlite":
                arc.conn.execute(ddl)
            else:
                try:
                    with arc.conn.cursor() as cur:
                        cur.execute(ddl)
                except Exception as e:
                    log.debug("[_ensure_archive_table][pg] !!! ALTER TABLE (meta col) FAILED !!!")
                    log.debug(f"[_ensure_archive_table][pg] ERROR: {e}")
                    log.debug(f"[_ensure_archive_table][pg] FAILED DDL: {ddl}")
                    raise e
    
    log.debug(f"[_ensure_archive_table] FINISHED ensuring table '{dst_table}'")
