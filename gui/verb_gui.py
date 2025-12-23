# gui/verb_gui.py

from fastapi import APIRouter, HTTPException, Body, Query
from pathlib import Path
from typing import Any, Dict, List, Tuple
import shutil
from copy import deepcopy
import sqlite3

# Optional Postgres (psycopg v3)
try:
    import psycopg  # pip install psycopg[binary]
    _PSYCOPG_AVAILABLE = True
except Exception:
    _PSYCOPG_AVAILABLE = False

# ─────────────────────────────────────────────────────────────
# S3-aware helpers (from json_proxy)
# ensure_prefix / touch make visible "folders" & empty files in S3.
# We also import internal helpers to support prefix copy on S3.
# If json_proxy isn't available (local-only env), we fall back to local ops.
# ─────────────────────────────────────────────────────────────
try:
    from api.json_proxy import ensure_prefix, touch  # S3-aware mkdir/touch
    from api.json_proxy import _is_s3_path as _s3_is_path
    from api.json_proxy import _get_client as _s3_client
    from api.json_proxy import _key_from_path as _s3_key_from_path
    from api.json_proxy import s3_manifest as _S3_MANIFEST
    _HAS_S3 = True
except Exception:
    _HAS_S3 = False

    def ensure_prefix(path: Path) -> bool:
        path.mkdir(parents=True, exist_ok=True)
        return True

    def touch(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    def _s3_is_path(_):  # type: ignore
        return False

    def _s3_client():  # type: ignore
        raise RuntimeError("S3 unavailable")

    def _s3_key_from_path(p):  # type: ignore
        return str(p)

from api.i_o import (
    load_schema,
    save_schema,
    get_verb_group_log_config,
    save_json,
    io_list_projects,
)
from core.handlers.core_verb import (
    create_new_verb,
    update_description,
    update_status_values,
    update_data_entry_schema,
    update_adverb_schema,
    assign_verb_group,
    filter_valid_noun_type_refs,
)
from api.manifest.resolver import resolve_path, get_db_uri

# -------------------------
# Debug block
# -------------------------
DEBUG_ENABLED = False  # Change to False to silence debug logs

def debug(*args, **kwargs):
    if DEBUG_ENABLED:
        print("[verb_gui]", *args, **kwargs)

router = APIRouter()

# ─────────────────────────────────────────────────────────────
# DB helpers (objects_db) — RDS-aware
# ─────────────────────────────────────────────────────────────
def _normalize_for_psycopg(url: str) -> str:
    # 'postgresql+asyncpg://' → 'postgresql://'
    # '?ssl=require' → '?sslmode=require'
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("postgresql+", 1)[1]
    if "?ssl=require" in url:
        url = url.replace("?ssl=require", "?sslmode=require")
    return url.replace("postgresql://asyncpg://", "postgresql://")

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

def _get_primary_id_field(proj: Path, group: str) -> str:
    """Read verb_group_log_config to determine the primary ID field name (fallback 'run_id')."""
    try:
        cfg = get_verb_group_log_config(proj, group)
        return cfg.get("primary_id", "run_id")
    except FileNotFoundError:
        return "run_id"

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

    debug("[_ensure_verb_table]", {"project": project_path.name, "kind": kind, "table": table})

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
        debug("ensure_verb_table(pg):", {"table": table})
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
    debug("ensure_verb_table(sqlite):", {"table": table})

# ─────────────────────────────────────────────────────────────
# S3 data-dump helpers
# ─────────────────────────────────────────────────────────────
def _s3_copy_prefix(src_dir: Path, dst_dir: Path, delete_src: bool = True) -> Dict[str, Any]:
    """
    Copy all S3 objects under src_dir/ → dst_dir/.
    """
    if not _HAS_S3:
        return {"copied": 0, "deleted": 0, "note": "S3 helpers unavailable"}

    client = _s3_client()
    bucket = _S3_MANIFEST.get("bucket_name")
    src_prefix = _s3_key_from_path(src_dir).rstrip("/") + "/"
    dst_prefix = _s3_key_from_path(dst_dir).rstrip("/") + "/"

    ensure_prefix(dst_dir)

    copied = 0
    deleted = 0
    continuation = None
    while True:
        kw = {"Bucket": bucket, "Prefix": src_prefix}
        if continuation:
            kw["ContinuationToken"] = continuation
        resp = client.list_objects_v2(**kw)
        contents = resp.get("Contents", [])
        for obj in contents:
            key = obj["Key"]
            # Skip the folder marker if present
            if key.endswith("/") and key == src_prefix:
                continue
            suffix = key[len(src_prefix):]
            new_key = dst_prefix + suffix
            client.copy_object(
                Bucket=bucket,
                CopySource={"Bucket": bucket, "Key": key},
                Key=new_key,
            )
            copied += 1
        if not resp.get("IsTruncated"):
            break
        continuation = resp.get("NextContinuationToken")

    if delete_src and copied:
        # Collect keys again for deletion
        continuation = None
        to_delete = []
        while True:
            kw = {"Bucket": bucket, "Prefix": src_prefix}
            if continuation:
                kw["ContinuationToken"] = continuation
            resp = client.list_objects_v2(**kw)
            contents = resp.get("Contents", [])
            for obj in contents:
                to_delete.append({"Key": obj["Key"]})
                if len(to_delete) == 1000:
                    client.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
                    deleted += len(to_delete)
                    to_delete = []
            if not resp.get("IsTruncated"):
                break
            continuation = resp.get("NextContinuationToken")
        if to_delete:
            client.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
            deleted += len(to_delete)

    return {"copied": copied, "deleted": deleted}

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

    debug("[_select_run_ids_for_group]", {
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

# ─────────────────────────────────────────────────────────────
# Data-dump folder migration (local & S3)
# ─────────────────────────────────────────────────────────────
def _move_data_dump_folder(proj: Path, old_group: str, new_group: str, run_id: str) -> Dict[str, Any]:
    """
    Move/merge the data dump folder named after run_id from old_group to new_group.
    """
    src = resolve_path(proj, "data_dump_dir", verb_group=old_group, run_id=str(run_id))
    dst_parent = resolve_path(proj, "data_dump_entry", verb_group=new_group)
    dst = dst_parent / str(run_id)

    # S3 path? Use prefix copy.
    if _s3_is_path(str(src)) or _s3_is_path(str(dst_parent)):
        ensure_prefix(dst_parent)
        info = _s3_copy_prefix(src, dst, delete_src=True)
        debug("[_move_data_dump_folder][s3]", {"src": str(src), "dst": str(dst), **info})
        # In S3 we don't "exist()" check; report best-effort stats
        return {"run_id": run_id, "src_exists": True, "dst_exists": True, "moved": info.get("copied", 0) > 0, "merged": False, "s3": True}

    # Local filesystem branch
    ensure_prefix(dst_parent)

    if not src.exists():
        debug("[_move_data_dump_folder] source missing", {"src": str(src)})
        return {"run_id": run_id, "src_exists": False, "dst_exists": dst.exists(), "moved": False, "merged": False}

    moved = False
    merged = False

    if not dst.exists():
        shutil.move(str(src), str(dst))
        moved = True
        debug("[_move_data_dump_folder] moved", {"src": str(src), "dst": str(dst)})
    else:
        for item in src.iterdir():
            s = item
            d = dst / item.name
            if item.is_dir():
                shutil.copytree(str(s), str(d), dirs_exist_ok=True)
            else:
                ensure_prefix(d.parent)
                shutil.copy2(str(s), str(d))
        shutil.rmtree(str(src))
        merged = True
        debug("[_move_data_dump_folder] merged", {"src": str(src), "dst": str(dst)})

    return {"run_id": run_id, "src_exists": True, "dst_exists": True, "moved": moved, "merged": merged}

def _migrate_group_sql_and_dumps(proj: Path, verb_name: str, old_group: str, new_group: str) -> Dict[str, Any]:
    """
    SQL-native migration when a verb changes groups:
      1) Gather run_ids for rows where (verb=verb_name AND verb_group=old_group)
      2) UPDATE those rows to set verb_group=new_group
      3) Move corresponding data_dump folders by run_id from old_group to new_group
    Returns a summary dict.
    """
    if not old_group or not new_group or old_group == new_group:
        return {"updated": 0, "run_ids": [], "data_dumps": {"moved": 0, "merged": 0, "missing": 0}}

    # ensure destination scaffold exists (S3 aware)
    _ensure_group_scaffold(proj, new_group)

    run_ids = _select_run_ids_for_group(proj, verb_name, old_group)
    debug("[_migrate_group_sql_and_dumps] candidates", {"count": len(run_ids)})

    updated_rows = _update_rows_change_group(proj, verb_name, old_group, new_group)

    # Move data-dump folders for those run_ids
    dd_moved = 0
    dd_merged = 0
    dd_missing = 0
    for pid in run_ids:
        res = _move_data_dump_folder(proj, old_group, new_group, pid)
        if not res.get("src_exists"):
            dd_missing += 1
        elif res.get("merged"):
            dd_merged += 1
        elif res.get("moved"):
            dd_moved += 1

    return {
        "updated": updated_rows,
        "run_ids": run_ids,
        "data_dumps": {"moved": dd_moved, "merged": dd_merged, "missing": dd_missing},
    }


def _ensure_group_scaffold(proj: Path, group_name: str) -> None:
    """
    Keep the folder/config scaffold for UI compatibility (data_dumps + log_config).
    Persistence of run rows is SQL-only; JSONL is not used.
    S3-aware: uses ensure_prefix/touch so the console shows the structure.
    """
    log_file = resolve_path(proj, "verb_group_log", verb_group=group_name)  # legacy .jsonl (empty)
    cfg_path = resolve_path(proj, "verb_group_log_config", verb_group=group_name)
    dumps_root = resolve_path(proj, "data_dump_entry", verb_group=group_name)

    ensure_prefix(log_file.parent)
    ensure_prefix(dumps_root)

    # Always guarantee a zero-byte object for the legacy log file (or a local file)
    touch(log_file)
    debug("[_ensure_group_scaffold] touched legacy log file", {"log_file": str(log_file)})

    # Ensure a config exists (save_json is S3-aware via i_o/json_proxy)
    need_default = False
    if not _s3_is_path(str(cfg_path)) and not cfg_path.exists():
        need_default = True
    else:
        try:
            _ = get_verb_group_log_config(proj, group_name)
        except FileNotFoundError:
            need_default = True

    if need_default:
        default_schema = {
            "primary_id": "run_ID",
            "fields": {
                "run_ID": {"type": "string", "required": True},
                "date_tested": {"type": "date", "required": True},
                "test_type": {"type": "string", "required": True},
            }
        }
        save_json(cfg_path, default_schema)
        debug("[_ensure_group_scaffold] wrote default log config", {"config_path": str(cfg_path)})

# ─────────────────────────────────────────────────────────────
# Validation / Normalization for linear_status
# ─────────────────────────────────────────────────────────────
_ALLOWED_STEP_TYPES = {
    "data_entry",
    "raw_upload",
    "interpretation",
    "adverb",
    "gate",
    "report",
}

def _normalize_bool(x: Any, default: bool) -> bool:
    return bool(x) if isinstance(x, (bool, int)) else default

def _validate_unique_ids(steps: List[dict]) -> List[str]:
    seen = set()
    dups = []
    for s in steps or []:
        sid = str(s.get("id", "")).strip()
        if not sid:
            dups.append("(blank id)")
            continue
        if sid in seen:
            dups.append(sid)
        seen.add(sid)
    return dups

def _index_schema_bits(verb_def: dict) -> dict:
    des = verb_def.get("data_entry_schema", {}) or {}
    adverbs = verb_def.get("adverb_schema", {}) or {}

    raw_inputs = list(des.get("raw_data_inputs", []) or [])
    interp = des.get("interpretation", {}) or {}
    interp_tabs = list(interp.get("tabs", []) or [])
    parsers = list(interp.get("parsers", []) or [])
    adverb_keys = sorted(list(adverbs.keys()))

    return {
        "raw_inputs": raw_inputs,
        "interp_tabs": interp_tabs,
        "parsers": parsers,
        "adverb_keys": adverb_keys,
    }

def _validate_linear_status_block(verb_def: dict, block: dict) -> dict:
    debug("[validate_linear_status] begin", {"verb_name": verb_def.get("verb_name")})

    if not isinstance(block, dict):
        debug("[validate_linear_status] invalid type", {"type": type(block).__name__})
        raise HTTPException(status_code=400, detail="linear_status must be an object")

    enabled = _normalize_bool(block.get("enabled", True), True)
    allow_manual_completion = _normalize_bool(block.get("allow_manual_completion", False), False)

    steps = block.get("steps", [])
    if not isinstance(steps, list):
        debug("[validate_linear_status] steps not a list")
        raise HTTPException(status_code=400, detail="linear_status.steps must be a list")

    dups = _validate_unique_ids(steps)
    if dups:
        debug("[validate_linear_status] duplicate step ids", {"dups": dups})
        raise HTTPException(status_code=400, detail=f"Duplicate or blank step id(s): {', '.join(dups)}")

    anchors = _index_schema_bits(verb_def)
    errors: List[str] = []

    normalized_steps: List[dict] = []
    for idx, s in enumerate(steps):
        ctx = {"index": idx, "id": s.get("id")}
        debug("[validate_linear_status] step", {**ctx, "raw": s})

        if not isinstance(s, dict):
            errors.append(f"Step[{idx}] must be an object")
            continue

        sid = str(s.get("id", "")).strip()
        if not sid:
            errors.append(f"Step[{idx}] missing 'id'")
        stype = str(s.get("type", "")).strip()
        if stype not in _ALLOWED_STEP_TYPES:
            errors.append(f"Step[{sid or idx}] invalid type '{stype}'")

        label = s.get("label")
        if label is not None and not isinstance(label, str):
            errors.append(f"Step[{sid}] label must be a string if provided")

        required = _normalize_bool(s.get("required", True), True)

        source = s.get("source")
        parser = s.get("parser")
        roles = s.get("roles")

        if stype == "raw_upload":
            if not isinstance(source, str) or not source.strip():
                errors.append(f"Step[{sid}] raw_upload requires 'source' (one of raw_data_inputs)")
            elif source not in anchors["raw_inputs"]:
                errors.append(f"Step[{sid}] source '{source}' not found in raw_data_inputs {anchors['raw_inputs']}")
        elif stype == "interpretation":
            if not isinstance(source, str) or not source.strip():
                errors.append(f"Step[{sid}] interpretation requires 'source' (one of interpretation.tabs)")
            elif source not in anchors["interp_tabs"]:
                errors.append(f"Step[{sid}] source '{source}' not found in interpretation.tabs {anchors['interp_tabs']}")
            if parser is not None:
                if not isinstance(parser, str):
                    errors.append(f"Step[{sid}] parser must be a string")
                elif anchors["parsers"] and parser not in anchors["parsers"]:
                    errors.append(f"Step[{sid}] parser '{parser}' not in defined parsers {anchors['parsers']}")
        elif stype == "adverb":
            if not isinstance(source, str) or not source.strip():
                errors.append(f"Step[{sid}] adverb requires 'source' (an adverb key)")
            elif source not in anchors["adverb_keys"]:
                errors.append(f"Step[{sid}] adverb source '{source}' not in adverb_schema keys {anchors['adverb_keys']}")
        elif stype == "gate":
            if roles is not None and not (isinstance(roles, list) and all(isinstance(r, str) for r in roles)):
                errors.append(f"Step[{sid}] gate.roles must be a list of strings if provided")

        norm = {
            "id": sid,
            "type": stype,
            "label": label if isinstance(label, str) else None,
            "required": required,
        }
        if stype in ("raw_upload", "interpretation", "adverb") and isinstance(source, str) and source.strip():
            norm["source"] = source
        if stype == "interpretation" and isinstance(parser, str):
            norm["parser"] = parser
        if stype == "gate" and isinstance(roles, list):
            norm["roles"] = roles

        normalized_steps.append(norm)
        debug("[validate_linear_status] step normalized", {**ctx, "norm": norm})

    if errors:
        debug("[validate_linear_status] errors", {"errors": errors})
        raise HTTPException(status_code=400, detail={"message": "Validation failed", "errors": errors})

    normalized = {
        "enabled": enabled,
        "allow_manual_completion": allow_manual_completion,
        "steps": normalized_steps,
    }
    debug("[validate_linear_status] ok", {"enabled": enabled, "allow_manual_completion": allow_manual_completion, "count": len(normalized_steps)})
    return normalized

def _propose_linear_status(verb_def: dict) -> dict:
    debug("[propose_linear_status] begin", {"verb_name": verb_def.get("verb_name")})
    anchors = _index_schema_bits(verb_def)
    steps: List[dict] = []

    # 1) data entry
    steps.append({
        "id": "data_entry",
        "type": "data_entry",
        "label": "Data Entry",
        "required": True,
    })

    # 2) raw uploads in declared order
    for raw in anchors["raw_inputs"]:
        steps.append({
            "id": f"raw_{raw}",
            "type": "raw_upload",
            "label": raw,
            "source": raw,
            "required": True,
        })

    # 3) interpretation tabs
    for tab in anchors["interp_tabs"]:
        step = {
            "id": f"interp_{tab}",
            "type": "interpretation",
            "label": tab,
            "source": tab,
            "required": True,
        }
        matching = [p for p in anchors["parsers"] if p == tab]
        if matching:
            step["parser"] = matching[0]
        steps.append(step)

    # 4) adverbs
    for adv in anchors["adverb_keys"]:
        steps.append({
            "id": f"adv_{adv}",
            "type": "adverb",
            "label": adv,
            "source": adv,
            "required": False,
        })

    proposal = {
        "enabled": True,
        "allow_manual_completion": False,
        "steps": steps,
    }
    debug("[propose_linear_status] built", {"count": len(steps)})
    return proposal

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _get_project_path(project: str) -> Path:
    """Get project path using the resolver."""
    project_root = resolve_path(Path(), "project_root")
    path = project_root / project
    debug("[_get_project_path]", {"root": str(project_root), "project": project, "resolved": str(path)})
    if not path.exists():
        # In S3-first deployments the local dir may not exist; keep 404 for now to match existing behavior.
        raise HTTPException(status_code=404, detail=f"Project {project} not found")
    return path

def _load_verb(proj: Path, verb_name: str) -> Tuple[dict, dict]:
    """Load full verb_types.json and return (all_verbs, verb_def) with checks."""
    debug("[_load_verb] start", {"project": str(proj), "verb": verb_name})
    verbs = load_schema(proj, "verb")
    if verb_name not in verbs:
        debug("[_load_verb] 404", {"verb": verb_name})
        raise HTTPException(status_code=404, detail=f"Verb {verb_name} not found")
    debug("[_load_verb] ok")
    return verbs, verbs[verb_name]

def _save_verb(proj: Path, verbs: dict, verb_name: str, updated: dict) -> None:
    """Persist updated verb_def back to verb_types.json."""
    debug("[_save_verb] start", {"verb": verb_name})
    verbs = dict(verbs)
    verbs[verb_name] = updated
    save_schema(proj, "verb", verbs)
    debug("[_save_verb] saved", {"verb": verb_name})

# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────
@router.get("/verb/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception as e:
        # Optional: return empty list on failure instead of 500
        print(f"[list_projects] list_projects failed: {e!r}")
        return []

@router.get("/verb/{project}")
def list_verbs(project: str):
    """Return all verb definitions in the project."""
    proj = _get_project_path(project)
    verbs = load_schema(proj, "verb")
    debug("[list_verbs]", {"project": project, "count": len(verbs)})
    return verbs

@router.get("/verb/{project}/{verb_name}")
def get_verb(project: str, verb_name: str):
    """Return a single verb definition."""
    proj = _get_project_path(project)
    verbs = load_schema(proj, "verb")
    if verb_name not in verbs:
        debug("[get_verb] 404", {"verb": verb_name})
        raise HTTPException(status_code=404, detail=f"Verb {verb_name} not found")
    debug("[get_verb] ok", {"verb": verb_name})
    return verbs[verb_name]

@router.post("/verb/{project}/{verb_name}")
def create_verb(project: str, verb_name: str, data: dict = Body(...)):
    """
    Create a new verb entry, ensure:
      • unified SQL table exists in objects_db
      • group scaffold (config & data_dumps) exists (S3-aware)
    """
    proj = _get_project_path(project)
    verbs = load_schema(proj, "verb")
    if verb_name in verbs:
        debug("[create_verb] already exists", {"verb": verb_name})
        raise HTTPException(status_code=400, detail=f"Verb {verb_name} already exists")

    # Build base definition
    new_def = create_new_verb(verb_name)
    debug("[create_verb] base", new_def)

    if "description" in data:
        new_def = update_description(new_def, data["description"])
    if "status_values" in data:
        new_def = update_status_values(new_def, data["status_values"])
    if "data_entry_schema" in data:
        new_def = update_data_entry_schema(new_def, data["data_entry_schema"])
    if "adverb_schema" in data:
        new_def = update_adverb_schema(new_def, data["adverb_schema"])
    if "verb_group" in data:
        new_def = assign_verb_group(new_def, data["verb_group"])

    # allow linear_status on create
    if "linear_status" in data:
        debug("[create_verb] validating linear_status")
        ls_norm = _validate_linear_status_block(new_def, data["linear_status"])
        new_def["linear_status"] = ls_norm
        debug("[create_verb] linear_status attached", {"steps": len(ls_norm.get("steps", []))})

    # Safeguard: noun_type_ref must be present
    noun_ref = (
        new_def.get("data_entry_schema", {})
        .get("set_up_inputs", {})
        .get("noun_type_ref")
    )
    if not noun_ref:
        debug("[create_verb] missing noun_type_ref")
        raise HTTPException(status_code=400, detail="[X] noun_type_ref is required when creating a verb")

    # Save into verb_types.json
    verbs[verb_name] = new_def
    save_schema(proj, "verb", verbs)
    debug("[create_verb] saved schema")

    # Ensure SQL table for this project
    _ensure_verb_table(proj)

    # Ensure group scaffold for UI/dumps (S3 aware)
    group_name = new_def["verb_group"]
    _ensure_group_scaffold(proj, group_name)

    return {"status": "created", "verb": verb_name}

@router.put("/verb/{project}/{verb_name}")
def update_verb(project: str, verb_name: str, data: dict = Body(...)):
    """
    Update an existing verb entry.
    If verb_group changes, migrate existing SQL rows by updating their verb_group
    and move any run_id-named data_dumps from old_group → new_group.
    """
    proj = _get_project_path(project)
    verbs = load_schema(proj, "verb")
    if verb_name not in verbs:
        debug("[update_verb] 404", {"verb": verb_name})
        raise HTTPException(status_code=404, detail=f"Verb {verb_name} not found")

    verb_def_original = deepcopy(verbs[verb_name])
    old_group = verb_def_original.get("verb_group")
    debug("[update_verb] start", {"verb": verb_name, "old_group": old_group})

    verb_def = deepcopy(verb_def_original)

    if "description" in data:
        verb_def = update_description(verb_def, data["description"])
        debug("[update_verb] description set")

    if "status_values" in data:
        verb_def = update_status_values(verb_def, data["status_values"])
        debug("[update_verb] status_values set")

    if "data_entry_schema" in data:
        verb_def = update_data_entry_schema(verb_def, data["data_entry_schema"])
        debug("[update_verb] data_entry_schema set")

    if "adverb_schema" in data:
        verb_def = update_adverb_schema(verb_def, data["adverb_schema"])
        debug("[update_verb] adverb_schema set")

    new_group = old_group
    group_changed = False
    if "verb_group" in data:
        verb_def = assign_verb_group(verb_def, data["verb_group"])
        new_group = verb_def.get("verb_group")
        group_changed = (new_group != old_group)
        debug("[update_verb] verb_group set", {"new_group": new_group, "changed": group_changed})

    # linear_status full replacement (validate against the *updated* verb_def)
    if "linear_status" in data:
        debug("[update_verb] validating linear_status")
        ls_norm = _validate_linear_status_block(verb_def, data["linear_status"])
        verb_def["linear_status"] = ls_norm
        debug("[update_verb] linear_status attached", {"steps": len(ls_norm.get("steps", []))})

    # Persist schema first (so future reads reflect the change)
    _save_verb(proj, verbs, verb_name, verb_def)

    # Ensure SQL table exists (no-op if present)
    _ensure_verb_table(proj)

    migration_summary = None
    if group_changed and old_group and new_group:
        try:
            migration_summary = _migrate_group_sql_and_dumps(proj, verb_name, old_group, new_group)
        except Exception as e:
            debug("[update_verb] SQL+dump migration failed", {"error": str(e)})
            raise HTTPException(
                status_code=500,
                detail=f"Verb group changed, but updating SQL rows or moving data dumps failed: {e}"
            )

    out = {"status": "updated", "verb": verb_name}
    if migration_summary is not None:
        out["migration"] = {
            "from": old_group,
            "to": new_group,
            **migration_summary
        }
    return out

@router.delete("/verb/{project}/{verb_name}")
def delete_verb(project: str, verb_name: str):
    """Delete a verb definition (does not drop SQL table)."""
    proj = _get_project_path(project)
    verbs = load_schema(proj, "verb")
    if verb_name not in verbs:
        debug("[delete_verb] 404", {"verb": verb_name})
        raise HTTPException(status_code=404, detail=f"Verb {verb_name} not found")
    del verbs[verb_name]
    save_schema(proj, "verb", verbs)
    debug("[delete_verb] deleted", {"verb": verb_name})
    return {"status": "deleted", "verb": verb_name}

@router.get("/noun/valid-refs/{project}")
def list_valid_noun_refs(project: str):
    """
    Return noun types that can be used as noun_type_ref in verbs.
    Only includes noun types that have a Reference or ReferenceList adjective.
    """
    proj = _get_project_path(project)

    try:
        noun_schema = load_schema(proj, "noun")
    except FileNotFoundError:
        debug("[list_valid_noun_refs] noun_types.json not found")
        raise HTTPException(status_code=404, detail="noun_types.json not found")

    valid_refs = filter_valid_noun_type_refs(noun_schema)
    debug("[list_valid_noun_refs]", {"count": len(valid_refs)})
    return {"valid_noun_types": valid_refs}

# ─────────────────────────────────────────────────────────────
# Log Schema routes (config remains file-based for UI editors)
# ─────────────────────────────────────────────────────────────
@router.get("/verb/log-schema/{project}/{group}")
def get_log_schema(project: str, group: str):
    proj = _get_project_path(project)
    try:
        cfg = get_verb_group_log_config(proj, group)
        debug("[get_log_schema] ok", {"group": group, "primary_id": cfg.get("primary_id")})
        return cfg
    except FileNotFoundError:
        debug("[get_log_schema] missing config", {"group": group})
        return {"primary_id": None, "fields": {}}

@router.post("/verb/log-schema/{project}/{group}")
def save_log_schema(project: str, group: str, schema: dict = Body(...)):
    proj = _get_project_path(project)
    config_path = resolve_path(proj, "verb_group_log_config", verb_group=group)
    log_file = resolve_path(proj, "verb_group_log", verb_group=group)

    # ensure folders (S3-aware)
    ensure_prefix(config_path.parent)
    ensure_prefix(log_file.parent)

    # write schema file via i_o helper (S3-aware)
    save_json(config_path, schema)
    debug("[save_log_schema] wrote", {"config_path": str(config_path)})

    # touch the legacy .jsonl path (compat only; not used for data)
    touch(log_file)
    debug("[save_log_schema] touched legacy log file", {"log_file": str(log_file)})

    return {"status": "saved", "log_file": str(log_file), "config_file": str(config_path)}

# ─────────────────────────────────────────────────────────────
# Linear Status Workflow Endpoints
# ─────────────────────────────────────────────────────────────
@router.get("/verb/status-workflow/step-types")
def get_linear_status_step_types():
    """
    Introspection endpoint to help UIs build editors.
    """
    debug("[get_linear_status_step_types] start")
    out = {
        "allowed_types": sorted(list(_ALLOWED_STEP_TYPES)),
        "field_requirements": {
            "data_entry": ["id", "type", "label?", "required?"],
            "raw_upload": ["id", "type", "source", "label?", "required?"],
            "interpretation": ["id", "type", "source", "parser?", "label?", "required?"],
            "adverb": ["id", "type", "source", "label?", "required?"],
            "gate": ["id", "type", "roles?", "label?", "required?"],
            "report": ["id", "type", "label?", "required?"],
        },
        "notes": [
            "source for raw_upload must match data_entry_schema.raw_data_inputs",
            "source for interpretation must match data_entry_schema.interpretation.tabs",
            "parser (if provided) should match data_entry_schema.interpretation.parsers",
            "source for adverb must be a key in adverb_schema",
            "gate.roles are validated as a list of strings (permissions live elsewhere)",
        ],
    }
    debug("[get_linear_status_step_types] ok", {"allowed": out["allowed_types"]})
    return out

@router.get("/verb/status-workflow/{project}/{verb_name}")
def get_linear_status(project: str, verb_name: str, propose_if_missing: bool = Query(True)):
    """
    Read the linear_status block. If absent and propose_if_missing, also return a 'proposal'.
    """
    proj = _get_project_path(project)
    verbs, verb_def = _load_verb(proj, verb_name)

    ls = verb_def.get("linear_status")
    debug("[get_linear_status] loaded", {"has_linear_status": bool(ls)})

    out = {"verb": verb_name, "linear_status": ls or None}
    if not ls and propose_if_missing:
        debug("[get_linear_status] building proposal (missing linear_status)")
        out["proposal"] = _propose_linear_status(verb_def)
    return out

@router.put("/verb/status-workflow/{project}/{verb_name}")
def put_linear_status(project: str, verb_name: str, payload: dict = Body(...)):
    """
    Create/replace the linear_status block after validation.
    Body: { linear_status: {...} }
    """
    proj = _get_project_path(project)
    verbs, verb_def = _load_verb(proj, verb_name)

    debug("[put_linear_status] begin", {"verb": verb_name})
    block = payload.get("linear_status")
    if block is None:
        debug("[put_linear_status] missing linear_status key")
        raise HTTPException(status_code=400, detail="Body must include 'linear_status' object")

    # validate against current verb_def
    normalized = _validate_linear_status_block(verb_def, block)

    # persist
    new_def = deepcopy(verb_def)
    new_def["linear_status"] = normalized
    _save_verb(proj, verbs, verb_name, new_def)

    debug("[put_linear_status] saved", {"steps": len(normalized.get("steps", []))})
    return {"status": "saved", "verb": verb_name, "steps": len(normalized.get("steps", []))}

@router.post("/verb/status-workflow/migrate/{project}/{verb_name}")
def migrate_linear_status(project: str, verb_name: str, persist: bool = Query(False)):
    """
    Build a linear_status proposal from the current bucketed schema.
    If persist=true, it will be validated and saved as the verb's linear_status.
    """
    proj = _get_project_path(project)
    verbs, verb_def = _load_verb(proj, verb_name)

    debug("[migrate_linear_status] propose", {"verb": verb_name, "persist": persist})
    proposal = _propose_linear_status(verb_def)
    if not persist:
        debug("[migrate_linear_status] returning proposal only")
        return {"status": "proposed", "verb": verb_name, "linear_status": proposal}

    # validate proposal and persist
    normalized = _validate_linear_status_block(verb_def, proposal)
    new_def = deepcopy(verb_def)
    new_def["linear_status"] = normalized
    _save_verb(proj, verbs, verb_name, new_def)

    debug("[migrate_linear_status] saved")
    return {"status": "saved", "verb": verb_name, "steps": len(normalized.get("steps", []))}

@router.delete("/verb/status-workflow/{project}/{verb_name}")
def delete_linear_status(project: str, verb_name: str):
    """
    Remove the linear_status block from a verb (fallback to bucket behavior).
    """
    proj = _get_project_path(project)
    verbs, verb_def = _load_verb(proj, verb_name)

    debug("[delete_linear_status] begin", {"verb": verb_name})
    if "linear_status" not in verb_def:
        debug("[delete_linear_status] no-op; already missing")
        return {"status": "no-op", "verb": verb_name}

    new_def = deepcopy(verb_def)
    del new_def["linear_status"]
    _save_verb(proj, verbs, verb_name, new_def)
    debug("[delete_linear_status] removed")
    return {"status": "deleted", "verb": verb_name}
