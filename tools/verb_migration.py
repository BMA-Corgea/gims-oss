#!/usr/bin/env python3
"""
Migrate legacy JSONL verb logs into the unified <project>_verb_log table
in either the local objects.db or RDS (PostgreSQL), depending on resolver state.

Usage:
    python tools/verb_migration.py --project LIMS-System --group Tests --jsonl verbs/Tests/Tests_log.jsonl
"""

from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

# --- ensure project root is importable ---
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- imports from your project ---
from api.manifest.resolver import (
    resolve_path,
    get_db_uri,
    RDS_ENABLED,
    is_rds_key,
    rds_resolver_module,
)
from api.i_o import get_verb_group_log_config

# Optional Postgres client
try:
    import psycopg  # psycopg[binary]
    _PSYCOPG_AVAILABLE = True
except Exception:
    _PSYCOPG_AVAILABLE = False

import sqlite3


# ---------- helpers ----------

def _normalize_for_psycopg(url: str) -> str:
    """Normalize DSN for psycopg like the rest of the app."""
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("postgresql+", 1)[1]
    if "?ssl=require" in url:
        url = url.replace("?ssl=require", "?sslmode=require")
    return url.replace("postgresql://asyncpg://", "postgresql://")


def _get_objects_db_target(project_path: Path) -> Tuple[str, str]:
    """RDS-aware resolver identical to verbs."""
    try:
        # Ask resolver for the URI
        uri = get_db_uri("object_sql_db")
    except Exception:
        uri = None

    # If RDS is enabled and we have a resolver module, prefer RDS DSN
    if RDS_ENABLED and rds_resolver_module and is_rds_key("object_sql_db"):
        try:
            uri = rds_resolver_module.resolve_rds_uri("object_sql_db")
        except Exception:
            pass

    if uri and uri.startswith("postgresql"):
        return ("pg", _normalize_for_psycopg(uri))

    # fallback to local sqlite
    db_path = resolve_path(project_path, "object_sql_db")
    return ("sqlite", db_path.as_posix())


def _table_name(project: str) -> str:
    return f"{project.replace('_', '-')}_verb_log"


def _ensure_verb_table(project_path: Path) -> None:
    """Create unified table matching local schema."""
    kind, target = _get_objects_db_target(project_path)
    table = _table_name(project_path.name)

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
        print(f"[verb_migration] ensured RDS table public.\"{table}\"")
        return

    # SQLite fallback
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
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
        print(f"[verb_migration] ensured local SQLite table \"{table}\" at {target}")
    finally:
        conn.close()


def _tolerant_get(d: Dict[str, Any], key: str) -> Optional[Any]:
    """Find key or underscore/space variants."""
    if key in d:
        return d[key]
    for alt in {key.replace(" ", "_"), key.replace("_", " ")}:
        if alt in d:
            return d[alt]
    return None


def _iter_jsonl(p: Path) -> Iterable[Dict[str, Any]]:
    """Yield dicts from a JSONL file."""
    with p.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
                if isinstance(rec, dict):
                    yield rec
            except Exception as e:
                print(f"[WARN] {p.name}:{line_no}: bad JSON, skipping: {e}", file=sys.stderr)


# ---------- migration core ----------

def migrate_jsonl(project: str, verb_group: str, jsonl_relpath: str) -> None:
    """Migrate one JSONL file into <project>_verb_log (RDS or local)."""
    project_root = resolve_path(Path(), "project_root") / project
    if not project_root.exists():
        print(f"[ERR] Project '{project}' not found at {project_root}", file=sys.stderr)
        sys.exit(1)

    cfg = get_verb_group_log_config(project_root, verb_group) or {}
    primary_id_field = cfg.get("primary_id") or "run_id"

    _ensure_verb_table(project_root)
    table = _table_name(project_root.name)
    jsonl_path = (project_root / jsonl_relpath).resolve()
    if not jsonl_path.exists():
        print(f"[ERR] JSONL not found: {jsonl_path}", file=sys.stderr)
        sys.exit(1)

    kind, target = _get_objects_db_target(project_root)
    inserted = updated = skipped = 0

    if kind == "pg" and _PSYCOPG_AVAILABLE:
        print(f"[verb_migration] migrating via RDS: {target}")
        with psycopg.connect(target, autocommit=True) as conn, conn.cursor() as cur:
            for rec in _iter_jsonl(jsonl_path):
                test_type = rec.get("test_type")
                pid_val = _tolerant_get(rec, primary_id_field)
                if not test_type or not pid_val:
                    skipped += 1
                    continue
                payload = dict(rec)
                payload["test_type"] = test_type
                cur.execute(
                    f"""
                    INSERT INTO public."{table}" (primary_id, verb_group, verb, data)
                    VALUES (%s, %s, %s, %s::jsonb)
                    ON CONFLICT (primary_id) DO UPDATE
                    SET verb_group = EXCLUDED.verb_group,
                        verb       = EXCLUDED.verb,
                        data       = EXCLUDED.data
                    RETURNING (xmax = 0) AS inserted;
                    """,
                    (str(pid_val), verb_group, str(test_type), json.dumps(payload)),
                )
                was_insert = cur.fetchone()[0]
                inserted += int(was_insert)
                updated += int(not was_insert)
    else:
        print(f"[verb_migration] migrating locally into SQLite at {target}")
        conn = sqlite3.connect(target)
        try:
            c = conn.cursor()
            for rec in _iter_jsonl(jsonl_path):
                test_type = rec.get("test_type")
                pid_val = _tolerant_get(rec, primary_id_field)
                if not test_type or not pid_val:
                    skipped += 1
                    continue
                payload = dict(rec)
                payload["test_type"] = test_type
                c.execute(
                    f'UPDATE "{table}" SET verb_group=?, verb=?, data=? WHERE primary_id=?',
                    (verb_group, str(test_type), json.dumps(payload), str(pid_val)),
                )
                if c.rowcount > 0:
                    updated += 1
                else:
                    try:
                        c.execute(
                            f'INSERT INTO "{table}" (primary_id, verb_group, verb, data) VALUES (?, ?, ?, ?)',
                            (str(pid_val), verb_group, str(test_type), json.dumps(payload)),
                        )
                        inserted += 1
                    except sqlite3.IntegrityError:
                        c.execute(
                            f'UPDATE "{table}" SET verb_group=?, verb=?, data=? WHERE primary_id=?',
                            (verb_group, str(test_type), json.dumps(payload), str(pid_val)),
                        )
                        updated += 1
            conn.commit()
        finally:
            conn.close()

    print(f"[DONE] project={project} table={table}")
    print(f"  inserted: {inserted}")
    print(f"  updated : {updated}")
    print(f"  skipped : {skipped}")


def main():
    ap = argparse.ArgumentParser(description="Migrate JSONL verb logs to unified RDS/local table")
    ap.add_argument("--project", required=True)
    ap.add_argument("--group", required=True)
    ap.add_argument("--jsonl", required=True)
    args = ap.parse_args()
    migrate_jsonl(args.project, args.group, args.jsonl)


if __name__ == "__main__":
    main()
