# gui/investigation_gui.py

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict, Any, Tuple

from fastapi import APIRouter, HTTPException, Body

# Core I/O + layout
from api.manifest.resolver import resolve_path
from api.i_o import (
    load_schema,
    get_noun_schema,
    get_noun_items,
    list_verb_groups,
    load_verb_group_log,
    get_verb_group_log_config,
    resolve_run_id_to_test_type,
    resolve_verb_group_from_test_type,
    io_list_projects,
)

# Lineage + status
from core.core_investigation import get_lineage
from core.status import get_status_breakdown_core

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# Debug
# ─────────────────────────────────────────────────────────────────────────────
DEBUG_ENABLED = False
def debug(*args, **kwargs):
    if DEBUG_ENABLED:
        print("[investigation_gui]", *args, **kwargs, flush=True)

# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

_COMPLETE_STATES = {"Uploaded", "Complete", "Parsed", "Manually Completed"}

def _classic_percent(breakdown: dict) -> Tuple[int, list]:
    """
    Return (percent, zones) where zones = [{key,label,value,ok}]
    for the classic 4 zones.
    """
    keys = [("raw_data", "Raw Data"),
            ("data_entry", "Data Entry"),
            ("interpretation", "Interpretation"),
            ("adverb_info", "Adverbs")]
    zones = []
    done = 0
    total = 0
    for k, label in keys:
        if k not in breakdown:
            continue
        total += 1
        val = str(breakdown.get(k, "Pending"))
        ok = val in _COMPLETE_STATES
        if ok:
            done += 1
        zones.append({"key": k, "label": label, "value": val, "ok": ok})
    total = max(total, 1)
    pct = int((done / total) * 100)
    return pct, zones

def _linear_percent(breakdown: dict) -> Tuple[int, dict]:
    steps_done = int(breakdown.get("linear_steps_completed", 0))
    steps_total = max(int(breakdown.get("linear_steps_total", 0) or 0), 1)
    pct = int((steps_done / steps_total) * 100)
    details = breakdown.get("details") or {}
    step_list = details.get("breakdown") or []
    return pct, {
        "steps_total": steps_total,
        "steps_completed": steps_done,
        "breakdown": step_list,
        "first_incomplete": breakdown.get("first_incomplete"),
        "progress_text": breakdown.get("linear_progress", f"{steps_done}/{steps_total}"),
    }

def _safe_str(x):
    try:
        return str(x)
    except Exception:
        return ""

# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/investigation/projects", response_model=List[str])
def list_projects() -> List[str]:
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception as e:
        # Optional: return empty list on failure instead of 500
        print(f"[list_projects] list_projects failed: {e!r}")
        return []

@router.get("/investigation/nouns/{project}", response_model=List[str])
def list_noun_types(project: str) -> List[str]:
    """Return all noun types for a project."""
    proj_root = resolve_path(Path(), "project_root") / project
    schema = load_schema(proj_root, "noun")
    return list(schema.keys())

@router.get("/investigation/items/{project}/{noun_type}", response_model=List[Dict[str, Any]])
def get_items(project: str, noun_type: str) -> List[Dict[str, Any]]:
    """Load noun items (SQL preferred, JSONL fallback)."""
    proj_root = resolve_path(Path(), "project_root") / project
    return get_noun_items(proj_root, noun_type)

@router.post("/investigation/format_table/{project}/{noun_type}", response_model=Dict[str, Any])
def format_table(project: str, noun_type: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    Very light 'format' passthrough to keep your existing table loader happy.
    We keep the columns = noun schema fields + optional _runID if present,
    and rows = values as strings (ID kept as-is for client-side rendering).
    """
    records = payload.get("records", [])
    if not isinstance(records, list):
        raise HTTPException(status_code=400, detail="Expected list of records")

    proj_root = resolve_path(Path(), "project_root") / project
    noun_schema = get_noun_schema(proj_root, noun_type)
    if not noun_schema:
        raise HTTPException(status_code=404, detail=f"Missing noun schema for {noun_type}")

    pk = noun_schema.get("primary_id_field", f"{noun_type.lower()}_id")
    base = list(noun_schema.get("fields", {}).keys())
    show_run = any("_runID" in r for r in records)
    cols = base + (["_runID"] if show_run else [])

    rows = []
    for r in records:
        out = []
        for c in cols:
            v = r.get(c, "")
            if v is None:
                v = ""
            elif isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            else:
                v = _safe_str(v)
            out.append(v)
        rows.append(out)

    return {"columns": cols, "rows": rows, "primary_id_field": pk}

@router.post("/investigation/lineage_ui/{project}/{noun_type}", response_model=Dict[str, Any])
def lineage_ui(project: str, noun_type: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """
    UI-focused lineage endpoint.
    - Resolves lineage via core.get_lineage()
    - Enriches each run with get_status_breakdown_core() (classic + linear)
    - Returns structured data for front-end rendering
    """
    record = payload.get("record")
    if not isinstance(record, dict):
        raise HTTPException(status_code=400, detail="Missing or invalid 'record'")

    proj_root = resolve_path(Path(), "project_root") / project

    # Load noun + global schemas
    noun_schema = get_noun_schema(proj_root, noun_type)
    if not noun_schema:
        raise HTTPException(status_code=404, detail=f"Missing noun schema for {noun_type}")

    items = get_noun_items(proj_root, noun_type)
    all_noun_schemas = load_schema(proj_root, "noun")
    adjective_schemas = load_schema(proj_root, "adjective")
    verb_schemas = load_schema(proj_root, "verb")

    # Pre-load all noun items (fast path: skip missing JSONL/SQL)
    all_noun_items: Dict[str, List[dict]] = {}
    for n in all_noun_schemas.keys():
        try:
            all_noun_items[n] = get_noun_items(proj_root, n)
        except Exception:
            pass

    # Build a minimal in-memory run map (group, run_id) -> DataEntry rows
    verb_data: Dict[tuple, list[dict]] = {}
    run_id_map: Dict[str, str] = {}

    try:
        groups = list_verb_groups(proj_root)
    except Exception:
        groups = []

    for g in groups:
        try:
            cfg = get_verb_group_log_config(proj_root, g) or {}
            pid = (cfg.get("primary_id") or "").strip()
        except Exception:
            pid = ""

        try:
            entries = load_verb_group_log(proj_root, g)
        except Exception:
            entries = []

        for e in entries:
            rid = e.get(pid) if pid else (e.get("run_ID") or e.get("runId"))
            if not rid:
                continue
            test_type = e.get("test_type") or e.get("verb")
            if test_type:
                run_id_map[_safe_str(rid)] = _safe_str(test_type)

    # Compose lineage
    lineage = get_lineage(
        project_path=proj_root,
        noun_type=noun_type,
        record=record,
        items=items,
        noun_schema=noun_schema,
        all_noun_schemas=all_noun_schemas,
        all_noun_items=all_noun_items,
        verb_schemas=verb_schemas,
        adjective_schemas=adjective_schemas,
        run_id_map=run_id_map,
        override_entries=[],  # not needed here, status shows overrides; lineage already resolves retests
        verb_data=verb_data,
        precomputed_verb_group_map={
            vt: vs.get("verb_group", "Tests") for vt, vs in verb_schemas.items()
        },
        precomputed_noun_type_map={
            vt: (vs.get("data_entry_schema", {}) or {}).get("set_up_inputs", {}).get("noun_type_ref")
            for vt, vs in verb_schemas.items()
        },
    )

    # Enrich each run with status breakdown → UI model
    ui_runs = []
    for run in lineage.get("runs", []):
        run_id = _safe_str(run.get("run_id"))
        verb_name = run.get("verb") or ""
        if not verb_name:
            try:
                verb_name = resolve_run_id_to_test_type(proj_root, run_id) or ""
            except Exception:
                verb_name = ""

        try:
            verb_group = resolve_verb_group_from_test_type(proj_root, verb_name) or "Tests"
        except Exception:
            verb_group = "Tests"

        # Status computation (classic OR linear)
        breakdown = get_status_breakdown_core(proj_root, run_id) or {}
        mode = "linear" if breakdown.get("mode") == "linear" else "classic"

        if mode == "linear":
            pct, linear_model = _linear_percent(breakdown)
            ui_runs.append({
                "run_id": run_id,
                "verb": verb_name,
                "verb_group": verb_group,
                "mode": "linear",
                "percent": pct,
                "progress_text": linear_model.get("progress_text"),
                "linear": linear_model,
                "override_status": breakdown.get("override_status"),
                "referencing_nouns": run.get("referencing_nouns", []),
            })
        else:
            pct, zones = _classic_percent(breakdown)
            ui_runs.append({
                "run_id": run_id,
                "verb": verb_name,
                "verb_group": verb_group,
                "mode": "classic",
                "percent": pct,
                "zones": zones,
                "override_status": breakdown.get("override_status"),
                "referencing_nouns": run.get("referencing_nouns", []),
            })

    # Assemble minimal noun header/meta for UI
    pk_field = noun_schema.get("primary_id_field", f"{noun_type.lower()}_id")
    noun = lineage.get("noun") or {}
    display_id = noun.get(pk_field, "(no id)")

    return {
        "noun_type": noun_type,
        "primary_id_field": pk_field,
        "display_id": display_id,
        "noun": noun,
        "runs": ui_runs,
        "parents": lineage.get("parents", []),
        "retests": lineage.get("retests", []),
    }

# ─────────────────────────────────────────────────────────────────────────────
# Back-compat (optional): simple text rendering, if something still calls it.
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/investigation/render_lineage", response_model=Dict[str, str])
def render_lineage_text_backcompat(payload: Dict[str, Any] = Body(...)) -> Dict[str, str]:
    # keep a tiny placeholder so old callers don't 500 — UI now renders client-side
    return {"text": "This endpoint has been superseded by /investigation/lineage_ui. The new UI renders structured lineage and status visually."}
