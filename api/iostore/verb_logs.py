# api/iostore/verb_logs.py -- split out of api/i_o.py (wiring-neutral). Verb-group logs (SQL/RDS + JSONL).
from __future__ import annotations
import json
from pathlib import Path
from api.json_proxy import read_text
from api.manifest.resolver import resolve_path
from api.storage_aws import normalize_pg_dsn as _normalize_for_psycopg
from .writers import append_jsonl, replace_jsonl_entry
from utils.logger import get_logger

log = get_logger(__name__)

try:
    import psycopg  # psycopg[binary]
    _PSYCOPG_AVAILABLE = True
except Exception:
    _PSYCOPG_AVAILABLE = False

import sqlite3  # used for local objects.db too


def _get_objects_db_target(project_path: Path) -> tuple[str, str]:
    """
    Decide ('pg'|'sqlite', target) using resolver + RDS module, mirroring server behavior.
    """
    try:
        from api.manifest.resolver import get_db_uri
    except Exception:
        get_db_uri = None

    uri = None
    if get_db_uri:
        try:
            uri = get_db_uri("object_sql_db")
        except Exception:
            uri = None

    if uri and uri.startswith("postgresql"):
        return ("pg", _normalize_for_psycopg(uri))

    db_path = resolve_path(project_path, "object_sql_db")
    return ("sqlite", db_path.as_posix())

def _table_name(project: str) -> str:
    """Match the local convention: <project-with-hyphens>_verb_log (always quoted)."""
    return f"{project.replace('_','-')}_verb_log"

def get_verb_group_log_config(project_path: Path, verb_group: str) -> dict:
    """
    Load the verb-group log config using resolve_path.
    Map key: 'verb_group_log_config' => verbs/{verb_group}/{verb_group}_log_config.json
    (S3-AWARE via read_text)
    """
    cfg_path = resolve_path(project_path, "verb_group_log_config", verb_group=verb_group)
    try:
        payload = read_text(cfg_path, encoding="utf-8")
        return json.loads(payload) if payload else {}
    except FileNotFoundError:
        raise FileNotFoundError(f"{verb_group}_log_config.json not found for verb group '{verb_group}' at {cfg_path}")
    except Exception as e:
        log.debug(f"[get_verb_group_log_config] Failed to load {cfg_path}: {e!r}")
        raise

def _json_from_db_cell(cell):
    if isinstance(cell, (dict, list)):
        return cell
    try:
        return json.loads(cell)
    except Exception:
        return cell

def load_verb_group_log(project_path: Path, verb_group: str) -> list[dict]:
    """
    Load all entries for a verb group from SQL table, with S3-AWARE JSONL fallback.
    """
    kind, target = _get_objects_db_target(project_path)
    table = _table_name(project_path.name)

    try:
        if kind == "pg" and _PSYCOPG_AVAILABLE:
            with psycopg.connect(target) as conn, conn.cursor() as cur:
                cur.execute(f'SELECT data FROM public."{table}" WHERE verb_group=%s ORDER BY ts DESC;', (verb_group,))
                rows = cur.fetchall()
            return [_json_from_db_cell(r[0]) for r in rows]

        conn = sqlite3.connect(target)
        try:
            c = conn.cursor()
            c.execute(f'SELECT data FROM "{table}" WHERE verb_group=? ORDER BY ts DESC;', (verb_group,))
            rows = c.fetchall()
        finally:
            conn.close()
        return [_json_from_db_cell(r[0]) for r in rows]

    except Exception as e:
        log.debug(f"[load_verb_group_log] DB read failed -> JSONL fallback: {e}")

    # JSONL fallback (S3-AWARE)
    log_path = resolve_path(project_path, "verb_group_log", verb_group=verb_group)
    try:
        payload = read_text(log_path, encoding="utf-8")
        return [json.loads(line) for line in payload.splitlines() if line.strip()]
    except FileNotFoundError:
        return []

def append_to_verb_group_log(project_path: Path, verb_group: str, entry: dict):
    """
    Insert/upsert to SQL table, with S3-AWARE JSONL fallback.
    """
    cfg = get_verb_group_log_config(project_path, verb_group)
    primary_id_field = cfg.get("primary_id") or "run_id"
    pid_val = entry.get(primary_id_field)
    if not pid_val:
        raise ValueError(f"Entry missing primary ID field '{primary_id_field}'")
    verb = entry.get("test_type") or entry.get("verb")
    if not verb:
        raise ValueError("Entry missing 'test_type' or 'verb'")

    kind, target = _get_objects_db_target(project_path)
    table = _table_name(project_path.name)

    try:
        if kind == "pg" and _PSYCOPG_AVAILABLE:
            with psycopg.connect(target, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO public."{table}" (primary_id, verb_group, verb, data)
                    VALUES (%s, %s, %s, %s::jsonb)
                    ON CONFLICT (primary_id) DO UPDATE
                    SET verb_group=EXCLUDED.verb_group,
                        verb=EXCLUDED.verb,
                        data=EXCLUDED.data;
                    """,
                    (str(pid_val), verb_group, str(verb), json.dumps(entry)),
                )
            return

        conn = sqlite3.connect(target)
        try:
            c = conn.cursor()
            c.execute(
                f'UPDATE "{table}" SET verb_group=?, verb=?, data=? WHERE primary_id=?',
                (verb_group, str(verb), json.dumps(entry), str(pid_val)),
            )
            if c.rowcount == 0:
                c.execute(
                    f'INSERT INTO "{table}" (primary_id, verb_group, verb, data) VALUES (?, ?, ?, ?)',
                    (str(pid_val), verb_group, str(verb), json.dumps(entry)),
                )
            conn.commit()
        finally:
            conn.close()
        return

    except Exception as e:
        log.debug(f"[append_to_verb_group_log] DB write failed -> JSONL fallback: {e}")

    # JSONL fallback (S3-AWARE)
    log_path = resolve_path(project_path, "verb_group_log", verb_group=verb_group)
    append_jsonl(log_path, entry)

def replace_in_verb_group_log(project_path: Path, verb_group: str, new_entry: dict):
    """
    Replace in SQL table, with S3-AWARE JSONL fallback.
    """
    cfg = get_verb_group_log_config(project_path, verb_group)
    primary_id_field = cfg.get("primary_id") or "run_id"
    pid_val = new_entry.get(primary_id_field)
    if not pid_val:
        raise ValueError(f"New entry missing primary ID field '{primary_id_field}'")
    verb = new_entry.get("test_type") or new_entry.get("verb")
    if not verb:
        raise ValueError("Entry missing 'test_type' or 'verb'")

    kind, target = _get_objects_db_target(project_path)
    table = _table_name(project_path.name)

    try:
        if kind == "pg" and _PSYCOPG_AVAILABLE:
            with psycopg.connect(target, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(
                    f'UPDATE public."{table}" SET verb_group=%s, verb=%s, data=%s::jsonb WHERE primary_id=%s;',
                    (verb_group, str(verb), json.dumps(new_entry), str(pid_val)),
                )
                if cur.rowcount == 0:
                    raise ValueError(f"No entry found with primary_id='{pid_val}'")
            return

        conn = sqlite3.connect(target)
        try:
            c = conn.cursor()
            c.execute(
                f'UPDATE "{table}" SET verb_group=?, verb=?, data=? WHERE primary_id=?',
                (verb_group, str(verb), json.dumps(new_entry), str(pid_val)),
            )
            if c.rowcount == 0:
                raise ValueError(f"No entry found with primary_id='{pid_val}'")
            conn.commit()
        finally:
            conn.close()
        return

    except Exception as e:
        log.debug(f"[replace_in_verb_group_log] DB update failed -> JSONL fallback: {e}")

    log_path = resolve_path(project_path, "verb_group_log", verb_group=verb_group)

    def match(entry: dict):
        return entry.get(primary_id_field) == pid_val
    
    replace_jsonl_entry(log_path, match, new_entry)
