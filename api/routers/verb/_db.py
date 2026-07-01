# api/routers/verb/_db.py
#
# objects_db (RDS-aware) target resolution, unified verb-log table creation,
# and the SQL helpers used by verb-group migration. Moved VERBATIM from
# api/routers/verb.py (no logic changes).
#
# SCOPE NOTE: these verb-log SQL helpers are intentionally kept LOCAL to this
# package (not extracted into core/storage) per the package-ize-only mandate.

from pathlib import Path
from typing import List, Tuple
import sqlite3

# Optional Postgres (psycopg v3)
try:
    import psycopg  # pip install psycopg[binary]
    _PSYCOPG_AVAILABLE = True
except Exception:
    _PSYCOPG_AVAILABLE = False

from api.i_o import get_verb_group_log_config
from api.manifest.resolver import resolve_path, get_db_uri
from api.storage_aws import normalize_pg_dsn as _normalize_for_psycopg

from ._log import log
from ._compat import ensure_prefix


# ─────────────────────────────────────────────────────────────
# DB helpers (objects_db) — RDS-aware
# ─────────────────────────────────────────────────────────────
def _get_objects_db_target(project_path: Path) -> Tuple[str, str]:
    """
    Returns (kind, target_uri_or_sqlite_path)
      kind: "pg" or "sqlite"
    """
    try:
        uri = get_db_uri("object_sql_db")
    except Exception:
        uri = None

    if uri and uri.startswith("postgresql"):
        return ("pg", _normalize_for_psycopg(uri))

    # SQLite fallback: per-project DB path
    db_path = resolve_path(project_path, "object_sql_db")
    return ("sqlite", db_path.as_posix())

def _table_name(project: str) -> str:
    """
    Return the unified verb log table name for a given project.
    Example: LIMS-System_verb_log
    (Preserves hyphen in project name, since we always quote in SQL)
    """
    return f"{project.replace('_', '-')}_verb_log"

def _ensure_verb_table(project_path: Path) -> None:
    """
    Ensure per-project unified verb log table exists.
    """
    kind, target = _get_objects_db_target(project_path)
    table = _table_name(project_path.name)

    log.debug("[_ensure_verb_table]", {"project": project_path.name, "kind": kind, "table": table})

    # PostgreSQL branch
    if kind == "pg" and _PSYCOPG_AVAILABLE:
        with psycopg.connect(target, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS public."{table}" (
                        row_id BIGSERIAL PRIMARY KEY,
                        primary_id TEXT UNIQUE,
                        verb_group TEXT,
                        verb TEXT,
                        ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        data JSONB NOT NULL
                    );
                """)
                cur.execute(f'CREATE INDEX IF NOT EXISTS "{table}__verb_idx" ON public."{table}" (verb);')
                cur.execute(f'CREATE INDEX IF NOT EXISTS "{table}__group_idx" ON public."{table}" (verb_group);')
        log.debug("ensure_verb_table(pg):", {"table": table})
        return

    # SQLite branch
    ensure_prefix(Path(target).parent)
    conn = sqlite3.connect(target, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    try:
        c = conn.cursor()
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS "{table}" (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                primary_id TEXT UNIQUE,
                verb_group TEXT,
                verb TEXT,
                ts TEXT NOT NULL DEFAULT (datetime('now')),
                data TEXT NOT NULL
            );
        """)
        c.execute(f'CREATE INDEX IF NOT EXISTS "{table}__verb_idx" ON "{table}" (verb);')
        c.execute(f'CREATE INDEX IF NOT EXISTS "{table}__group_idx" ON "{table}" (verb_group);')
        conn.commit()
    finally:
        conn.close()
    log.debug("ensure_verb_table(sqlite):", {"table": table})


# ─────────────────────────────────────────────────────────────
# SQL helpers for group migration
# ─────────────────────────────────────────────────────────────
def _select_run_ids_for_group(project_path: Path, verb_name: str, old_group: str) -> List[str]:
    """
    Return list of primary IDs for rows in this verb table that currently belong to old_group.

    NOTE:
      • Legacy log configs may define arbitrary "primary_id" field names, but the SQL schema
        always uses the unified column name 'primary_id'.
      • We therefore read the config only for UI or legacy reference, but always query the
        actual 'primary_id' column in SQL.

    Args:
        project_path: Path to the project directory.
        verb_name: Verb whose rows we’re moving.
        old_group: The existing verb_group name.

    Returns:
        List[str]: primary_id values from the unified verb log table.
    """
    # Determine table name and DB target
    table = _table_name(project_path.name)
    kind, target = _get_objects_db_target(project_path)

    # Determine legacy field (for debug/UI only)
    try:
        cfg = get_verb_group_log_config(project_path, old_group)
        legacy_field = cfg.get("primary_id", "run_id")
    except FileNotFoundError:
        legacy_field = "run_id"

    # Always query the actual column in the unified table
    id_field = "primary_id"

    log.debug("[_select_run_ids_for_group]", {
        "table": table,
        "id_field": id_field,
        "legacy_field": legacy_field,
        "group": old_group,
        "kind": kind
    })

    # PostgreSQL branch
    if kind == "pg" and _PSYCOPG_AVAILABLE:
        with psycopg.connect(target, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f'SELECT "{id_field}" FROM public."{table}" WHERE verb = %s AND verb_group = %s',
                    (verb_name, old_group),
                )
                rows = [r[0] for r in cur.fetchall() if r[0] is not None]
        return rows

    # SQLite branch
    conn = sqlite3.connect(target)
    try:
        c = conn.cursor()
        c.execute(
            f'SELECT "{id_field}" FROM "{table}" WHERE verb = ? AND verb_group = ?',
            (verb_name, old_group),
        )
        rows = [r[0] for r in c.fetchall() if r[0] is not None]
    finally:
        conn.close()

    return rows


def _update_rows_change_group(project_path: Path, verb_name: str, old_group: str, new_group: str) -> int:
    """Update rows in unified table to move them from old_group to new_group; return affected count."""
    if old_group == new_group:
        return 0
    table = _table_name(project_path.name)
    kind, target = _get_objects_db_target(project_path)

    if kind == "pg" and _PSYCOPG_AVAILABLE:
        with psycopg.connect(target, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f'UPDATE public."{table}" SET verb_group = %s WHERE verb = %s AND verb_group = %s',
                    (new_group, verb_name, old_group),
                )
                return cur.rowcount or 0

    conn = sqlite3.connect(target)
    try:
        c = conn.cursor()
        c.execute(
            f'UPDATE "{table}" SET verb_group = ? WHERE verb = ? AND verb_group = ?',
            (new_group, verb_name, old_group),
        )
        conn.commit()
        return c.rowcount or 0
    finally:
        conn.close()
