# api/iostore/nouns.py -- split out of api/i_o.py (wiring-neutral). Noun items + verb-group resolution.
from __future__ import annotations
import json
import re
from pathlib import Path
from api.json_proxy import read_text
from api.manifest.resolver import resolve_path
from .schema import load_schema, get_verb_schema
from .verb_logs import _get_objects_db_target, get_verb_group_log_config, load_verb_group_log
from .fs_shims import fs_exists, fs_is_dir, fs_iterdir
from utils.logger import get_logger

log = get_logger(__name__)

try:
    import psycopg  # psycopg[binary]
    _PSYCOPG_AVAILABLE = True
except Exception:
    _PSYCOPG_AVAILABLE = False


def resolve_verb_group_from_test_type(project_path: Path, test_type: str) -> str | None:
    schema = load_schema(project_path, "verb")
    verb_schema = schema.get(test_type)
    if not verb_schema:
        return None
    return verb_schema.get("verb_group", "Tests")

def get_noun_items(project_path: Path, noun_type: str) -> list[dict]:
    """
    Load entries for a noun (RDS + SQLite + S3-AWARE JSONL fallback).
    """
    import sqlite3
    sanitized = re.sub(r"\W+", "_", str(noun_type)).strip("_")
    base_table = f"noun_{sanitized}"
    project_name = project_path.name
    full_table = f"{project_name.replace('_','-')}_{base_table}"
    kind, target = _get_objects_db_target(project_path)
    log.debug(f"[get_noun_items] noun={noun_type!r} kind={kind} target={target}")

    def _normalize_row(d: dict) -> dict:
        fixed = dict(d)
        for k in list(d.keys()):
            if "_" in k:
                spaced = k.replace("_", " ")
                fixed.setdefault(spaced, d[k])
            if " " in k:
                underscored = k.replace(" ", "_")
                fixed.setdefault(underscored, d[k])
        return fixed

    # 0) Unified instances store (the SQL-only target). Authoritative once populated; if a
    #    collection has no rows yet (a fresh/temp project pre-migration) fall through to the legacy
    #    per-noun table / JSONL reads below, so existing data + the temp-project tests are unaffected.
    try:
        from core.storage.factory import collection_for_noun, get_record_store
        _rows = get_record_store(project_path).list_records(collection_for_noun(noun_type))
        if _rows:
            out = [_normalize_row(r) if isinstance(r, dict) else r for r in _rows]
            log.debug(f"[get_noun_items] ✓ returning {len(out)} rows from unified instances store")
            return out
    except Exception as e:
        log.debug(f"[get_noun_items] instances read failed -> legacy fallback: {e}")

    # 1) RDS
    if kind == "pg" and _PSYCOPG_AVAILABLE:
        try:
            with psycopg.connect(target) as conn, conn.cursor() as cur:
                # Check existence
                cur.execute("SELECT to_regclass(%s);", (f'public."{full_table}"',))
                exists = cur.fetchone()[0]
                if not exists:
                    log.debug(f"[get_noun_items] X RDS table '{full_table}' not found.")
                    raise Exception("no_table")

                # Fetch all rows now that we know it exists
                cur.execute(f'SELECT * FROM public."{full_table}" ORDER BY 1;')
                colnames = [desc.name for desc in cur.description]
                rows = cur.fetchall()

            out = [_normalize_row(dict(zip(colnames, r))) for r in rows]
            log.debug(f"[get_noun_items] ✓ returning {len(out)} rows from RDS table {full_table}")
            return out
        except Exception as e:
            log.debug(f"[get_noun_items] RDS read failed -> fallback: {e}")

    # 2) SQLite
    if kind == "sqlite":
        try:
            conn = sqlite3.connect(target)
            conn.row_factory = sqlite3.Row
            try:
                c = conn.cursor()
                table_names = [full_table, base_table]
                for table in table_names:
                    exists = c.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)
                    ).fetchone()
                    if exists:
                        rows = c.execute(f'SELECT * FROM "{table}"').fetchall()
                        out = [_normalize_row(dict(r)) for r in rows]
                        log.debug(f"[get_noun_items] ✓ returning {len(out)} rows from SQLite table {table}")
                        return out
                log.debug(f"[get_noun_items] no SQLite table found for {noun_type}")
            finally:
                conn.close()
        except Exception as e:
            log.debug(f"[get_noun_items] SQLite read failed -> fallback: {e}")

    # 3) JSONL fallback
    jsonl_path = resolve_path(project_path, "noun_items", noun_type=noun_type)
    try:
        payload = read_text(jsonl_path, encoding="utf-8")
        items = [json.loads(line) for line in payload.splitlines() if line.strip()]
    except FileNotFoundError:
        log.debug("[get_noun_items] X no SQL and no JSONL -> []")
        return []

    out = [_normalize_row(i) if isinstance(i, dict) else i for i in items]
    log.debug(f"[get_noun_items] ✓ returning {len(out)} rows (JSONL fallback)")
    return out

def _noun_key_field(project_path: Path, noun_type: str, fallback: str = "id") -> str:
    """The noun's primary-id field (from noun_types.json), defaulting to ``id``."""
    try:
        schema = (load_schema(project_path, "noun") or {}).get(noun_type) or {}
        return schema.get("primary_id_field") or fallback
    except Exception:
        return fallback

def put_noun_item(project_path: Path, noun_type: str, record: dict, key_field: str | None = None) -> str:
    """Create-or-update a noun instance in the unified ``instances`` store. Returns the key field used."""
    from core.storage.factory import collection_for_noun, get_record_store
    kf = key_field or _noun_key_field(project_path, noun_type)
    get_record_store(project_path).put_record(collection_for_noun(noun_type), kf, record)
    return kf

def resolve_noun_type_from_override(project_path: Path, override: dict) -> str | None:
    """
    Return noun_type for an override entry via verb schema.
    """
    run_id = override.get("run")
    verb = override.get("verb")
    if not run_id or not verb:
        return None
    try:
        verb_schema = get_verb_schema(project_path, verb)
    except Exception:
        log.debug(f"[resolve_noun_type_from_override] get_verb_schema failed for verb={verb!r}", exc_info=True)
        return None
    noun_type = (
        verb_schema.get("data_entry_schema", {})
        .get("set_up_inputs", {})
        .get("noun_type_ref")
    )
    return noun_type  # <- FIXED: previously missing return

def resolve_run_id_to_test_type(project_path: Path, run_id: str) -> str:
    """
    Resolve a run_id -> test_type (or 'verb') by scanning verb-group logs.
    Uses resolve_path-backed helpers (list_verb_groups + load_verb_group_log)
    and each group's {verb_group}_log_config to determine the primary id field.
    (S3/RDS-aware through the helpers)
    """
    # Iterate all verb groups discovered via resolver-backed listing
    groups: list[str] = []
    try:
        groups = list_verb_groups(project_path)
    except Exception as e:
        log.debug(f"[resolve_run_id_to_test_type] list_verb_groups failed: {e!r}")
        groups = []

    for group in groups:
        # Determine primary id key from the group's config (fallbacks included)
        try:
            cfg = get_verb_group_log_config(project_path, group)
            primary_id = (
                cfg.get("primary_id")
                or cfg.get("primaryId")
                or cfg.get("primary_id_field")
                or "run_id"  # sensible default
            )
        except Exception:
            primary_id = "run_id"

        # Build a small set of candidate keys to tolerate space/underscore variants
        cand_keys = {
            primary_id,
            primary_id.replace("_", " "),
            primary_id.replace(" ", "_"),
            "run_id", "run_ID", "Run ID"  # extra safety nets for legacy rows
        }

        # Load entries from SQL (RDS/SQLite) with JSONL fallback via resolver
        try:
            entries = load_verb_group_log(project_path, group)
        except Exception as e:
            log.debug(f"[resolve_run_id_to_test_type] load_verb_group_log({group}) failed: {e!r}")
            entries = []

        # Scan rows for a matching run id
        for row in entries:
            if not isinstance(row, dict):
                continue
            # quick normalization pass to allow both spaced and underscored access
            # without mutating original dict
            norm = dict(row)
            for k, v in list(row.items()):
                if isinstance(k, str):
                    if " " in k:
                        norm.setdefault(k.replace(" ", "_"), v)
                    if "_" in k:
                        norm.setdefault(k.replace("_", " "), v)

            # compare as strings to avoid type surprises
            for k in cand_keys:
                if k in norm and str(norm.get(k)) == str(run_id):
                    verb_name = norm.get("test_type") or norm.get("verb")
                    if verb_name:
                        return str(verb_name)

    # Nothing matched
    raise ValueError(f"Run ID {run_id!r} not found in any verb-group log.")

def list_verb_groups(project_path: Path) -> list[str]:
    """
    S3-aware listing of verb groups.
    """
    verbs_dir = resolve_path(project_path, "verbs_dir")
    if not fs_exists(verbs_dir) or not fs_is_dir(verbs_dir):
        raise FileNotFoundError(f"X verbs_dir not found at {verbs_dir}")
    return [Path(d).name for d in fs_iterdir(verbs_dir) if fs_is_dir(d)]
