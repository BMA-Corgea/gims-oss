"""runs/noun *_archive_index row writers (split verbatim from archive_workbench.py).

Canonical writers + legacy-row promotion for the archive index tables. These
never touch the monkeypatch seam, so they live outside the package __init__.
"""
from __future__ import annotations
from datetime import datetime
from typing import List, Optional

from utils.logger import get_logger
log = get_logger(__name__)

from .db_meta import _DBHandle
from .index_tables import _ensure_aux_archive_tables


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
    log.debug(f"[_insert_noun_archive_index_rows] Inserting {len(ids)} rows with project='{project}', noun_type='{noun_type}', strategy='{strategy}'")
    if arc.kind == "sqlite":
        for pid in ids:
            try:
                arc.conn.execute(
                    'INSERT INTO noun_archive_index (project, noun_type, primary_id, table_name, archived_at, strategy, notes, schema_hash) VALUES (?,?,?,?,?,?,?,?)',
                    (project, noun_type, str(pid), table, now, strategy, None, schema_hash)
                )
                log.debug(f"[_insert_noun_archive_index_rows][sqlite] Inserted row for pid={pid}")
            except Exception as e:
                log.debug(f"[_insert_noun_archive_index_rows][sqlite] ERROR inserting pid={pid}: {e}")
                raise
        return
    with arc.conn.cursor() as cur:
        for pid in ids:
            try:
                cur.execute(
                    'INSERT INTO noun_archive_index (project, noun_type, primary_id, table_name, archived_at, strategy, notes, schema_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                    (project, noun_type, str(pid), table, now, strategy, None, schema_hash)
                )
                log.debug(f"[_insert_noun_archive_index_rows][pg] Inserted row for pid={pid}")
            except Exception as e:
                log.debug(f"[_insert_noun_archive_index_rows][pg] ERROR inserting pid={pid}: {e}")
                log.debug(f"[_insert_noun_archive_index_rows][pg] Values: project={project}, noun_type={noun_type}, table={table}, strategy={strategy}")
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
            log.debug("[runs_index] upgraded legacy row with missing project:", project, run_id, strategy, archive_path)
            return
    except Exception as e:
        log.debug("[runs_index] legacy upgrade attempt failed (non-fatal):", repr(e))

    if _runs_index_row_exists(arc, project, run_id, strategy, archive_path):
        log.debug("[runs_index] skip duplicate:", project, run_id, strategy, archive_path)
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

    log.debug(f"[_insert_runs_archive_index_row] Column order: {col_names}")

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

    log.debug(f"[_insert_runs_archive_index_row] Inserting: {dict(zip(insert_cols, insert_vals))}")

    if arc.kind == "sqlite":
        placeholders = ",".join(["?"] * len(insert_vals))
        sql = f'INSERT INTO runs_archive_index ({cols_str}) VALUES ({placeholders})'
        arc.conn.execute(sql, tuple(insert_vals))
    else:
        placeholders = ",".join(["%s"] * len(insert_vals))
        sql = f'INSERT INTO runs_archive_index ({cols_str}) VALUES ({placeholders})'
        with arc.conn.cursor() as cur:
            cur.execute(sql, tuple(insert_vals))

    log.debug(f"[_insert_runs_archive_index_row] Successfully inserted run_id={run_id}, project={project}, verb={verb}")
