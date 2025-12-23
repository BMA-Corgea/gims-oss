# api/gui/verb_workbench_gui.py

from fastapi import APIRouter, HTTPException, Body
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import json
import sqlite3

# Optional Postgres (psycopg v3)
try:
    import psycopg  # pip install psycopg[binary]
    _PSYCOPG_AVAILABLE = True
except Exception:
    _PSYCOPG_AVAILABLE = False

from api.i_o import (
    load_schema,
    get_verb_group_log_config,   # still file-based for field defs + primary_id key
    io_list_projects,
)
from api.manifest.resolver import resolve_path, get_db_uri

# -------------------------
# Debug block
# -------------------------
DEBUG_ENABLED = False  # flip to True to see noisy logs

def debug(*args, **kwargs):
    if DEBUG_ENABLED:
        print("[verb_workbench_gui]", *args, **kwargs)

router = APIRouter(prefix="/api/verb_workbench", tags=["VerbWorkbench"])

# ─────────────────────────────────────────────────────────────
# DB helpers (objects_db) — same pattern as gui/verb_gui.py
#   • one unified per-project table: <project>_verb_log
#   • Postgres JSONB / SQLite TEXT
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

    db_path = resolve_path(project_path, "object_sql_db")
    return ("sqlite", db_path.as_posix())

def _table_name(project: str) -> str:
    """
    Unified per-project table name.
    Example: LIMS-System_verb_log
    (Keep hyphens; we always quote)
    """
    return f"{project.replace('_','-')}_verb_log"

def _ensure_verb_table(project_path: Path) -> None:
    """
    Ensure unified per-project verb log table exists.
    Columns:
      row_id PK, primary_id TEXT UNIQUE, verb_group TEXT, verb TEXT, ts, data JSONB/TEXT
    """
    kind, target = _get_objects_db_target(project_path)
    table = _table_name(project_path.name)
    debug("[_ensure_verb_table]", {"project": project_path.name, "kind": kind, "table": table})

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
        return

    # SQLite
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
    finally:
        conn.close()

def _projects_root() -> Path:
    return resolve_path(Path(), "project_root")

def _project_path(project: str) -> Path:
    pp = (_projects_root() / project).resolve()
    if not pp.exists():
        raise HTTPException(404, f"Project '{project}' not found.")
    return pp

def _verb_types(pp: Path) -> Dict[str, Any]:
    return load_schema(pp, "verb")

def _group_name(pp: Path, verb: str) -> str:
    vt = _verb_types(pp)
    if verb not in vt:
        raise HTTPException(404, f"Verb '{verb}' not found.")
    g = vt[verb].get("verb_group")
    if not g:
        raise HTTPException(400, f"Verb '{verb}' missing verb_group.")
    return g

def _log_config(pp: Path, group: str) -> Dict[str, Any]:
    cfg = get_verb_group_log_config(pp, group)
    return cfg or {}

def _get_primary_id_field(pp: Path, group: str) -> str:
    try:
        return _log_config(pp, group).get("primary_id", "run_id")
    except FileNotFoundError:
        return "run_id"

def _json_loads_safe(s: str) -> Dict[str, Any]:
    try:
        return json.loads(s) if isinstance(s, str) else (s or {})
    except Exception:
        return {}

# ─────────────────────────────────────────────────────────────
# SQL accessors
# ─────────────────────────────────────────────────────────────

def _select_rows_for_verb(pp: Path, verb: str) -> List[Dict[str, Any]]:
    """Return rows for a given verb, newest first."""
    _ensure_verb_table(pp)
    table = _table_name(pp.name)
    kind, target = _get_objects_db_target(pp)

    if kind == "pg" and _PSYCOPG_AVAILABLE:
        with psycopg.connect(target) as conn, conn.cursor() as cur:
            cur.execute(f'SELECT primary_id, verb_group, verb, ts, data FROM public."{table}" WHERE verb=%s ORDER BY ts DESC', (verb,))
            rows = cur.fetchall()
        out = []
        for pid, group, v, ts, data in rows:
            # psycopg returns JSONB as dict already; if not, coerce
            data_dict = data if isinstance(data, dict) else _json_loads_safe(data)
            out.append({"primary_id": pid, "verb_group": group, "verb": v, "ts": str(ts), "data": data_dict})
        return out

    conn = sqlite3.connect(target)
    try:
        c = conn.cursor()
        c.execute(f'SELECT primary_id, verb_group, verb, ts, data FROM "{table}" WHERE verb=? ORDER BY ts DESC', (verb,))
        rows = c.fetchall()
    finally:
        conn.close()
    return [
        {"primary_id": r[0], "verb_group": r[1], "verb": r[2], "ts": r[3], "data": _json_loads_safe(r[4])}
        for r in rows
    ]

def _select_one_run(pp: Path, verb: str, run_primary_id: str) -> Optional[Dict[str, Any]]:
    _ensure_verb_table(pp)
    table = _table_name(pp.name)
    kind, target = _get_objects_db_target(pp)

    if kind == "pg" and _PSYCOPG_AVAILABLE:
        with psycopg.connect(target) as conn, conn.cursor() as cur:
            cur.execute(
                f'SELECT primary_id, verb_group, verb, ts, data FROM public."{table}" WHERE verb=%s AND primary_id=%s',
                (verb, run_primary_id),
            )
            row = cur.fetchone()
        if not row:
            return None
        pid, group, v, ts, data = row
        data_dict = data if isinstance(data, dict) else _json_loads_safe(data)
        return {"primary_id": pid, "verb_group": group, "verb": v, "ts": str(ts), "data": data_dict}

    conn = sqlite3.connect(target)
    try:
        c = conn.cursor()
        c.execute(
            f'SELECT primary_id, verb_group, verb, ts, data FROM "{table}" WHERE verb=? AND primary_id=?',
            (verb, run_primary_id),
        )
        row = c.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {"primary_id": row[0], "verb_group": row[1], "verb": row[2], "ts": row[3], "data": _json_loads_safe(row[4])}

def _insert_run(pp: Path, verb: str, group: str, primary_id_value: str, payload_dict: Dict[str, Any]) -> None:
    """Insert a new run row; raises on unique conflict (primary_id is UNIQUE)."""
    _ensure_verb_table(pp)
    table = _table_name(pp.name)
    kind, target = _get_objects_db_target(pp)

    # ensure payload has the test_type echoed
    payload = dict(payload_dict or {})
    payload["test_type"] = verb

    if kind == "pg" and _PSYCOPG_AVAILABLE:
        jd = json.dumps(payload)
        with psycopg.connect(target, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                f'INSERT INTO public."{table}" (primary_id, verb_group, verb, data) VALUES (%s, %s, %s, %s::jsonb)',
                (primary_id_value, group, verb, jd),
            )
        return

    conn = sqlite3.connect(target)
    try:
        c = conn.cursor()
        c.execute(
            f'INSERT INTO "{table}" (primary_id, verb_group, verb, data) VALUES (?, ?, ?, ?)',
            (primary_id_value, group, verb, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()

def _update_run(pp: Path, verb: str, primary_id_value: str, merged_payload: Dict[str, Any]) -> int:
    """Update a run's data JSON by primary_id; returns affected rowcount."""
    _ensure_verb_table(pp)
    table = _table_name(pp.name)
    kind, target = _get_objects_db_target(pp)

    payload = dict(merged_payload or {})
    payload["test_type"] = verb

    if kind == "pg" and _PSYCOPG_AVAILABLE:
        jd = json.dumps(payload)
        with psycopg.connect(target, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                f'UPDATE public."{table}" SET data=%s::jsonb WHERE verb=%s AND primary_id=%s',
                (jd, verb, primary_id_value),
            )
            return cur.rowcount or 0

    conn = sqlite3.connect(target)
    try:
        c = conn.cursor()
        c.execute(
            f'UPDATE "{table}" SET data=? WHERE verb=? AND primary_id=?',
            (json.dumps(payload), verb, primary_id_value),
        )
        conn.commit()
        return c.rowcount or 0
    finally:
        conn.close()

def _resolve_run_id_to_test_type_sql(pp: Path, run_primary_id: str) -> Optional[str]:
    """Find which verb a given primary_id belongs to (searches unified table)."""
    _ensure_verb_table(pp)
    table = _table_name(pp.name)
    kind, target = _get_objects_db_target(pp)

    if kind == "pg" and _PSYCOPG_AVAILABLE:
        with psycopg.connect(target) as conn, conn.cursor() as cur:
            cur.execute(f'SELECT verb FROM public."{table}" WHERE primary_id=%s', (run_primary_id,))
            row = cur.fetchone()
            return row[0] if row else None

    conn = sqlite3.connect(target)
    try:
        c = conn.cursor()
        c.execute(f'SELECT verb FROM "{table}" WHERE primary_id=?', (run_primary_id,))
        row = c.fetchone()
    finally:
        conn.close()
    return row[0] if row else None

# ─────────────────────────────────────────────────────────────
# Status.json scaffolding (LEAN runtime), unchanged from your version
# ─────────────────────────────────────────────────────────────

def _build_linear_status_block(verb_def: dict) -> Optional[dict]:
    ls = (verb_def or {}).get("linear_status") or {}
    if not bool(ls.get("enabled")):
        return None

    allow_manual = bool(ls.get("allow_manual_completion", False))
    steps_src = ls.get("steps") or []

    steps_out: List[dict] = []
    for s in steps_src:
        step_rec = {
            "id": s.get("id"),
            "type": s.get("type"),
            "label": s.get("label"),
            "required": bool(s.get("required", False)),
            "completed": False,
        }
        if "source" in s and s.get("source") is not None:
            step_rec["source"] = s.get("source")
        if "parser" in s and s.get("parser") is not None:
            step_rec["parser"] = s.get("parser")
        if "roles" in s and s.get("roles") is not None and s.get("type") == "gate":
            step_rec["roles"] = s.get("roles")
        steps_out.append(step_rec)

    return {
        "enabled": True,
        "allow_manual_completion": allow_manual,
        "current_index": 0,
        "steps": steps_out,
    }

def _write_initial_status(pp: Path, group: str, verb: str, run_id: str):
    vt = _verb_types(pp)
    verb_def = vt.get(verb, {}) or {}

    status_path = resolve_path(pp, "status_file", verb_group=group, run_id=str(run_id))
    status_path.parent.mkdir(parents=True, exist_ok=True)

    ls_block = _build_linear_status_block(verb_def)
    if ls_block:
        doc: dict = {"linear_status": ls_block}
        debug("[_write_initial_status] linear_status initialized", {"run_id": run_id, "steps": len(ls_block.get("steps", []))})
    else:
        doc = {"linear_status": {"enabled": False, "steps": []}}
        debug("[_write_initial_status] linear_status disabled for verb", verb)

    status_path.write_text(json.dumps(doc, indent=2))
    debug("[_write_initial_status] wrote Status.json", str(status_path))

def _ensure_dump_scaffold(pp: Path, group: str, verb: str, run_id: str):
    vt = _verb_types(pp)
    verb_def = vt.get(verb, {})
    verb_schema = verb_def.get("data_entry_schema", {}) or {}
    adv_schema = verb_def.get("adverb_schema", {}) or {}

    dump_root = resolve_path(pp, "data_dump_dir", verb_group=group, run_id=str(run_id))
    dump_root.mkdir(parents=True, exist_ok=True)
    debug("[_ensure_dump_scaffold] dump_root:", str(dump_root))

    # Instructions.md
    instructions = verb_schema.get("instructions", []) or []
    md = "# Instructions\n\n"
    if instructions:
        md += "\n".join(f"{i+1}. {line}" for i, line in enumerate(instructions)) + "\n"
    else:
        md += "(No instructions defined)\n"
    (dump_root / "Instructions.md").write_text(md)

    # Raw data pockets
    for pocket in verb_schema.get("raw_data_inputs", []) or []:
        (dump_root / pocket).mkdir(parents=True, exist_ok=True)

    # DataEntry.json (empty list initial)
    (dump_root / "DataEntry.json").write_text(json.dumps([], indent=2))

    # Interpretation CSV stubs
    tabs = verb_schema.get("interpretation", {}).get("tabs", [])
    if isinstance(tabs, dict):
        tabs = list(tabs.keys())
    for t in tabs:
        (dump_root / f"{t}.csv").write_text("")

    # Adverbs.json defaults
    adv_data = {}
    for nm, cfg in (adv_schema or {}).items():
        cls = (cfg or {}).get("adverb_class") or (cfg or {}).get("type")
        adv_data[nm] = [] if cls == "ReferenceList" else ""
    (dump_root / "adverbs.json").write_text(json.dumps(adv_data, indent=2))

    # Status.json (LEAN)
    _write_initial_status(pp, group, verb, run_id)

# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

@router.get("/projects", summary="List available projects")
def list_projects() -> List[str]:
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception as e:
        # Optional: return empty list on failure instead of 500
        print(f"[list_projects] list_projects failed: {e!r}")
        return []

@router.get("/{project}", summary="List verbs in a project")
def list_verbs(project: str) -> List[str]:
    pp = _project_path(project)
    return sorted(list(_verb_types(pp).keys()))

@router.get("/{project}/{verb}/log_config", summary="Fetch log config (fields + primary id)")
def get_log_config(project: str, verb: str):
    pp = _project_path(project)
    group = _group_name(pp, verb)
    cfg = _log_config(pp, group)
    return {"group": group, "primary_id": cfg.get("primary_id"), "fields": cfg.get("fields", {})}

@router.get("/{project}/{verb}/runs", summary="List existing runs (from SQL)")
def list_runs(project: str, verb: str):
    pp = _project_path(project)
    group = _group_name(pp, verb)
    pid_field = _get_primary_id_field(pp, group)

    rows = _select_rows_for_verb(pp, verb)
    # Rehydrate response rows to look like legacy JSONL entries:
    out = []
    for r in rows:
        data = dict(r["data"] or {})
        # ensure test_type and pid field present
        data["test_type"] = verb
        data.setdefault(pid_field, r["primary_id"])
        out.append(data)
    debug("[list_runs]", {"verb": verb, "group": group, "rows": len(out)})
    return out

@router.get("/{project}/{verb}/run/{run_id}", summary="Load a run by primary id (from SQL)")
def get_run(project: str, verb: str, run_id: str):
    pp = _project_path(project)
    group = _group_name(pp, verb)
    pid_field = _get_primary_id_field(pp, group)

    row = _select_one_run(pp, verb, run_id)
    if not row:
        debug("[get_run] not found", {"pid": pid_field, "run_id": run_id})
        raise HTTPException(404, f"Run '{run_id}' not found.")

    data = dict(row["data"] or {})
    data["test_type"] = verb
    data[pid_field] = row["primary_id"]
    debug("[get_run] found", {"pid": pid_field, "run_id": run_id})
    return data

@router.post("/{project}/{verb}/validate", summary="Validate a run payload (required fields only)")
def validate_payload(project: str, verb: str, payload: Dict[str, Any] = Body(...)):
    pp = _project_path(project)
    group = _group_name(pp, verb)
    cfg = _log_config(pp, group)
    fields = cfg.get("fields", {})
    errors: List[str] = []
    for name, finfo in fields.items():
        if name == "test_type":
            continue
        if finfo.get("required") and (not str(payload.get(name, "")).strip()):
            errors.append(f"'{name}' is required.")
    ok = len(errors) == 0
    debug("[validate_payload]", {"ok": ok, "errors": errors})
    return {"ok": ok, "errors": errors}

@router.post("/{project}/{verb}/create", summary="Create a new run (insert into SQL + scaffold dump + init LEAN status)")
def create_run(project: str, verb: str, payload: Dict[str, Any] = Body(...)):
    pp = _project_path(project)
    group = _group_name(pp, verb)
    pid_field = _get_primary_id_field(pp, group)

    if pid_field not in payload or not str(payload.get(pid_field, "")).strip():
        raise HTTPException(400, f"Payload must include primary id field '{pid_field}'.")

    v = validate_payload(project, verb, payload)
    if not v["ok"]:
        debug("[create_run] validation failed", v["errors"])
        return v

    primary_id_value = str(payload.get(pid_field)).strip()

    # duplicate check via SELECT
    if _select_one_run(pp, verb, primary_id_value) is not None:
        debug("[create_run] duplicate", {"pid": pid_field, "val": primary_id_value})
        raise HTTPException(409, f"Duplicate primary id '{primary_id_value}' for verb '{verb}'.")

    # insert
    _insert_run(pp, verb, group, primary_id_value, payload)
    debug("[create_run] inserted", {"pid": pid_field, "val": primary_id_value})

    # scaffold dump + Status.json
    _ensure_dump_scaffold(pp, group, verb, primary_id_value)

    return {"ok": True, "id": primary_id_value}

@router.post("/{project}/{verb}/update/{run_id}", summary="Update an existing run (SQL)")
def update_run(project: str, verb: str, run_id: str, payload: Dict[str, Any] = Body(...)):
    pp = _project_path(project)
    group = _group_name(pp, verb)
    pid_field = _get_primary_id_field(pp, group)

    existing = _select_one_run(pp, verb, run_id)
    if not existing:
        debug("[update_run] not found", {"pid": pid_field, "run_id": run_id})
        raise HTTPException(404, f"Run '{run_id}' not found.")

    merged = dict(existing["data"] or {})
    merged.update(payload or {})
    # enforce correct identifiers
    merged["test_type"] = verb
    merged[pid_field] = run_id

    rc = _update_run(pp, verb, run_id, merged)
    if rc <= 0:
        raise HTTPException(500, "Update failed unexpectedly.")

    debug("[update_run] replaced in SQL", {"pid": pid_field, "id": run_id})
    return {"ok": True, "id": run_id}

# ─────────────────────────────────────────────────────────────
# Re-seed/refresh Status.json for an existing run using the run's ACTUAL verb schema
# ─────────────────────────────────────────────────────────────

@router.post("/{project}/{verb}/status/refresh/{run_id}", summary="Re-seed Status.json for a run from its actual verb's schema (LEAN, SQL resolver).")
def refresh_status_for_run(
    project: str,
    verb: str,   # kept for routing compatibility; ignored for schema selection
    run_id: str,
):
    """
    - Resolve the run's verb via SQL (unified table).
    - Load that verb's schema and build LEAN Status.json.
    """
    pp = _project_path(project)

    actual_verb = _resolve_run_id_to_test_type_sql(pp, run_id)
    if not actual_verb:
        debug("[refresh_status_for_run] not found in SQL", {"run_id": run_id})
        raise HTTPException(404, f"Run '{run_id}' not found in SQL.")

    if actual_verb != verb:
        debug("[refresh_status_for_run] verb param differs from resolved", {"param": verb, "actual": actual_verb})

    vt = _verb_types(pp)
    if actual_verb not in vt:
        raise HTTPException(404, f"Verb schema for '{actual_verb}' not found.")

    group = vt[actual_verb].get("verb_group")
    if not group:
        raise HTTPException(400, f"Verb '{actual_verb}' missing verb_group.")

    # (Optional) ensure the run exists for that verb (it should, as we just resolved it)
    row = _select_one_run(pp, actual_verb, run_id)
    if not row:
        debug("[refresh_status_for_run] unexpected: row missing on resolved verb", {"actual": actual_verb, "run_id": run_id})

    # Seed Status.json strictly from the resolved verb's linear_status
    _write_initial_status(pp, group, actual_verb, run_id)

    status_path = resolve_path(pp, "status_file", verb_group=group, run_id=str(run_id))
    try:
        doc = json.loads(status_path.read_text())
        steps = len(doc.get("linear_status", {}).get("steps", []))
    except Exception:
        doc, steps = {}, 0

    return {
        "ok": True,
        "project": project,
        "verb_param": verb,
        "verb_resolved": actual_verb,
        "group": group,
        "run_id": run_id,
        "status_path": str(status_path),
        "steps": steps,
        "linear_status_enabled": bool(doc.get("linear_status", {}).get("enabled", False)),
    }
