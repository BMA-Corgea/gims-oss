# api/routers/archive_workbench/routes.py
#
# Route handlers for the Archive Workbench, split out of the package __init__.
# Moved VERBATIM except the 5 pytest-patched seams (resolve_path / load_schema /
# get_verb_schema / get_verb_group_log_config / load_verb_group_log) which are now
# read via qualified `_seams.<name>` access so a monkeypatch on the _seams module
# is observed at call time. All other infra/service helpers are imported by name.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Literal

from fastapi import Body, Query
from core.errors import AppError

from . import _seams
from ._seams import (
    router,
    log,
    i_o,
    _archive_noun_ids,
    _archived_noun_ids,
    _collect_linked_noun_ids,
    _collect_linked_noun_ids_by_scan,
    _execute_plan,
    _insert_noun_archive_index_rows,
    _insert_runs_archive_index_row,
    _jp_list_dirnames,
    _jp_prefix_exists,
    _load_policy,
    _noun_pf,
    _open_db,
    _resolve_project_path,
    _restore_noun_ids,
    _safe_commit,
    _serialize_plan,
    _split_noun_ids_by_actual_archive,
    collection_for_noun,
    ensure_prefix,
    get_record_store,
    plan_apply_archive_policy_for_nouns,
    plan_archive_runs_hard,
    plan_archive_runs_soft,
    plan_restore_runs,
    write_text,
)


# ------------------------------------------------------------------------------
# Endpoints (DB-agnostic implementations)
# ------------------------------------------------------------------------------

@router.get("/{project}/nouns/archived")
def list_archived_candidates(
    project: str,
    noun: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None, pattern="^(soft|hard)$"),
    limit: int = Query(200, ge=1, le=5000)
):
    """
    Return IDs currently in the archive for each noun (soft->hot.archived=1, hard->archive table).
    """
    log.debug("[list_archived_candidates]", project, "noun=", noun, "strategy=", strategy)
    project_path = _resolve_project_path(project)
    policy = _load_policy(project_path)
    noun_types = _seams.load_schema(project_path, "noun")  # dict of noun_type -> schema
    if noun:
        noun_types = {noun: noun_types.get(noun)} if noun in noun_types else {}

    out = {}
    for noun_type, entry in (noun_types or {}).items():
        if not entry:
            continue
        pf = _noun_pf(project_path, noun_type, entry)
        strat = strategy or (policy.get("nouns", {}).get(noun_type, {}).get("strategy", "soft"))
        ids, count = _archived_noun_ids(project_path, noun_type, pf, strat, limit)
        out[noun_type] = {
            "strategy": strat,
            "primary_field": pf,
            "count": int(count),
            "ids": ids
        }
    return out

@router.post("/{project}/nouns/restore/preview")
def preview_noun_restore(
    project: str,
    selection: Any = Body(...),
    strategy: Optional[str] = Query(None, pattern="^(soft|hard)$")
):
    """
    Build a restore plan for the provided IDs.
    For hard strategy: include schema drift detection (via stored schema_hash if present).
    """
    log.debug("[preview_noun_restore]", project, "items:", {k: len(v) for k, v in (selection or {}).items()})
    project_path = _resolve_project_path(project)
    policy = _load_policy(project_path)
    noun_types = _seams.load_schema(project_path, "noun")

    out = {}
    for noun_type, ids in (selection or {}).items():
        entry = noun_types.get(noun_type)
        if not entry or not ids:
            continue

        _pf = _noun_pf(project_path, noun_type, entry)
        strat = strategy or (policy.get("nouns", {}).get(noun_type, {}).get("strategy", "soft"))

        plan = {
            "description": f"Restore {noun_type} ({strat})",
            "steps": [{"op": "restore", "strategy": strat, "id": str(i)} for i in ids],
        }

        out[noun_type] = {
            "strategy": strat,
            "ids": ids,
            "plan": plan,
            # No schema drift in the instances model.
            **({"drift_detected": False, "drift_report": None} if strat == "hard" else {})
        }
    return out

@router.post("/{project}/nouns/restore/apply")
def apply_noun_restore(
    project: str,
    selection: Any = Body(...),
    strategy: Optional[str] = Query(None, pattern="^(soft|hard)$")
):
    """
    Execute restore (soft: clear flags; hard: copy from archive to hot).
    On hard: writes archive-only columns into hot.__legacy_data JSON.
    """
    log.debug("[apply_noun_restore]", project, "items:", {
        k: len(v) for k, v in (selection or {}).items() if isinstance(v, (list, tuple))
    })
    project_path = _resolve_project_path(project)
    policy = _load_policy(project_path)
    noun_types = _seams.load_schema(project_path, "noun")

    results: Dict[str, Any] = {}
    for noun_type, ids in (selection or {}).items():
        entry = noun_types.get(noun_type)
        if not entry or not ids:
            results[noun_type] = {"ok": True, "affected": 0}
            continue

        pf = _noun_pf(project_path, noun_type, entry)
        strat = strategy or (policy.get("nouns", {}).get(noun_type, {}).get("strategy", "soft"))
        affected = _restore_noun_ids(project_path, noun_type, pf, ids, strat)
        results[noun_type] = {"ok": True, "affected": affected, "strategy": strat}

    log.debug("[apply_noun_restore] done")
    return results

@router.get("/{project}/noun_types")
def list_noun_types(project: str):
    log.debug("[noun_types] start", project)
    project_path = _resolve_project_path(project)
    try:
        data = _seams.load_schema(project_path, "noun")
        names = sorted(data.keys())
        log.debug("[noun_types] ->", len(names), "items")
        return names
    except FileNotFoundError as e:
        log.debug("[noun_types] FileNotFoundError:", str(e))
        raise AppError("NOUN_SCHEMA_NOT_FOUND", str(e), status=404, details={"project": project})
    except Exception as e:
        log.debug("[noun_types] Error:", str(e))
        raise AppError("NOUN_TYPES_LOAD_FAILED", f"Failed to load noun types: {str(e)}", status=500, details={"project": project})

@router.get("/{project}/verb_types")
def list_verb_types(project: str):
    log.debug("[verb_types] start", project)
    project_path = _resolve_project_path(project)
    try:
        data = _seams.load_schema(project_path, "verb")
        names = sorted(data.keys())
        log.debug("[verb_types] ->", len(names), "items")
        return names
    except FileNotFoundError as e:
        log.debug("[verb_types] FileNotFoundError:", str(e))
        raise AppError("VERB_SCHEMA_NOT_FOUND", str(e), status=404, details={"project": project})
    except Exception as e:
        log.debug("[verb_types] Error:", str(e))
        raise AppError("VERB_TYPES_LOAD_FAILED", f"Failed to load verb types: {str(e)}", status=500, details={"project": project})

@router.get("/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    return i_o.list_projects_safe()

@router.get("/{project}/policy")
def get_policy(project: str):
    log.debug("[get_policy]", project)
    project_path = _resolve_project_path(project)
    return _load_policy(project_path)

@router.post("/{project}/policy")
def save_policy(project: str, body: Dict[str, Any] = Body(...)):
    log.debug("[save_policy]", project)
    project_path = _resolve_project_path(project)
    policy_path = _seams.resolve_path(project_path, "archive_policy")
    ensure_prefix(policy_path.parent)  # S3/FS-safe
    write_text(policy_path, json.dumps(body, indent=2), encoding="utf-8")  # S3-aware
    log.debug("[save_policy] saved to", policy_path)
    return {"ok": True}

@router.get("/{project}/nouns/preview")
def preview_noun_archive(project: str):
    """
    Build a plan-per-noun using archive_policy.json and current SQL stats.
    Returns a serialized summary (does not execute).
    """
    log.debug("[preview_noun_archive] start", project)
    try:
        project_path = _resolve_project_path(project)
        policy = _load_policy(project_path)
        noun_types = _seams.load_schema(project_path, "noun")  # dict
        if not noun_types:
            log.debug("[preview_noun_archive] ! no noun types found in schema")
            return {}
    except Exception as e:
        log.debug(f"[preview_noun_archive] ! ERROR during init: {e}")
        raise AppError("PROJECT_CONFIG_LOAD_FAILED", f"Failed to load project config: {e}", status=500, details={"project": project})

    log.debug(f"[preview_noun_archive] reading record store for project: {project}")
    noun_tables: Dict[str, Dict[str, Any]] = {}
    for noun_type, entry in (noun_types or {}).items():
        log.debug("\n[preview_noun_archive] --- noun_type:", noun_type, "---")
        try:
            coll = collection_for_noun(noun_type)
            recs = get_record_store(project_path).list_records(coll)

            pf = _noun_pf(project_path, noun_type, entry)
            if not pf:
                log.debug("[preview_noun_archive] ! no primary field found, skipping noun type")
                continue

            log.debug(f"[preview_noun_archive] primary_field='{pf}'")

            pol = (policy.get("nouns") or {}).get(noun_type, {})
            date_field = pol.get("date_field")
            log.debug(f"[preview_noun_archive] policy='{pol}', date_field='{date_field}'")

            total_count = len(recs)
            ordered_ids = [
                str(r.get(pf))
                for r in sorted(
                    recs,
                    key=lambda r: (str(r.get(date_field) or "") if date_field else "", str(r.get(pf) or ""))
                )
                if r.get(pf) is not None
            ]
            age_eval_rows = [{pf: r.get(pf), date_field: r.get(date_field)} for r in recs] if date_field else []
            hot_cols = [(k, "TEXT") for k in sorted({k for r in recs for k in r.keys()})]
            arc_cols: List[Tuple[str, str]] = []

            log.debug(f"[preview_noun_archive] total_count={total_count}, len(ordered_ids)={len(ordered_ids)}, len(age_eval_rows)={len(age_eval_rows)}")
            log.debug(f"[preview_noun_archive] hot_cols_count={len(hot_cols)}, arc_cols_count={len(arc_cols)}")

            noun_tables[noun_type] = {
                "table": noun_type,
                "primary_field": pf,
                "total_count": total_count,
                "ordered_oldest_ids": ordered_ids,
                "date_field": date_field,
                "rows_for_age_eval": age_eval_rows,
                "hot_columns": hot_cols,
                "archive_columns": arc_cols
            }
        except Exception as e:
            log.debug(f"[preview_noun_archive] ! ERROR processing noun '{noun_type}': {e}")
            continue

    log.debug(f"\n[preview_noun_archive] processed {len(noun_tables)} noun types, building plans via core...")
    try:
        plan_map = plan_apply_archive_policy_for_nouns(policy, noun_tables)
        log.debug(f"[preview_noun_archive] core returned {len(plan_map)} plans")
    except Exception as e:
        log.debug(f"[preview_noun_archive] ! ERROR during core plan generation: {e}")
        raise AppError("CORE_PLANNING_FAILED", f"Core planning failed: {e}", status=500, details={"project": project})

    summary = {
        k: {
            "strategy": v["strategy"],
            "eligible_ids": v["eligible_ids"],
            "plan": _serialize_plan(v["plan"])
        } for k, v in plan_map.items()
    }
    log.debug("[preview_noun_archive] done, returning summary")
    return summary

# ---------------------- noun instance ID listing (RDS-aware) ------------------

@router.get("/{project}/nouns/ids")
def list_noun_instance_ids(
    project: str,
    type: str = Query(..., description="Noun type, e.g. 'Glove'"),
    q: Optional[str] = Query(None, description="Case-insensitive substring filter on primary ID"),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> List[str]:
    """
    Return primary-key values (IDs) for the given noun type from the *hot* nouns DB.
    RDS: <Project>_noun_<Name> + ILIKE; SQLite: noun_<Name> + LIKE.
    """
    log.debug("[list_noun_instance_ids]", project, "type=", type, "q=", q, "limit=", limit, "offset=", offset)
    project_path = _resolve_project_path(project)
    coll = collection_for_noun(type)
    recs = get_record_store(project_path).list_records(coll)
    pf = _noun_pf(project_path, type)
    ids = sorted({str(r.get(pf)) for r in recs if r.get(pf) is not None})
    if q:
        ids = [i for i in ids if q.lower() in i.lower()]
    out = ids[offset:offset + limit]
    log.debug("[list_noun_instance_ids] ->", len(out), "ids")
    return out

@router.get("/{project}/nouns/ids/{type}")
def list_noun_instance_ids_path(
    project: str,
    type: str,
    q: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> List[str]:
    return list_noun_instance_ids(project, type=type, q=q, limit=limit, offset=offset)

# ------------------------------------------------------------------------------
# Archive apply (write schema_hash in hard mode; RDS-aware)
# ------------------------------------------------------------------------------

@router.post("/{project}/nouns/apply")
def apply_noun_archive(project: str, selection: Any = Body(None)):
    """
    Execute archive for provided noun IDs (or derive from policy if selection omitted).
    For hard archive, index schema_hash of HOT table at time of archive.
    """
    log.debug("[apply_noun_archive]", project)
    project_path = _resolve_project_path(project)
    policy = _load_policy(project_path)
    noun_types = _seams.load_schema(project_path, "noun")  # dict

    results: Dict[str, Any] = {}
    for noun_type, entry in (noun_types or {}).items():
        ids = (selection or {}).get(noun_type, [])
        log.debug("\n[apply_noun_archive] noun:", noun_type, "requested_ids:", ids)

        pf = _noun_pf(project_path, noun_type, entry)

        if not ids:
            log.debug("[apply_noun_archive] deriving from policy via preview")
            coll = collection_for_noun(noun_type)
            recs = get_record_store(project_path).list_records(coll)
            pol = (policy.get("nouns") or {}).get(noun_type, {})
            date_field = pol.get("date_field")
            ordered_ids = [
                str(r.get(pf))
                for r in sorted(
                    recs,
                    key=lambda r: (str(r.get(date_field) or "") if date_field else "", str(r.get(pf) or ""))
                )
                if r.get(pf) is not None
            ]
            age_eval_rows = [{pf: r.get(pf), date_field: r.get(date_field)} for r in recs] if date_field else []
            hot_cols = [(k, "TEXT") for k in sorted({k for r in recs for k in r.keys()})]
            noun_tables = {
                noun_type: {
                    "table": noun_type,
                    "primary_field": pf,
                    "total_count": len(recs),
                    "ordered_oldest_ids": ordered_ids,
                    "date_field": date_field,
                    "rows_for_age_eval": age_eval_rows,
                    "hot_columns": hot_cols,
                    "archive_columns": []
                }
            }
            plan_map = plan_apply_archive_policy_for_nouns(policy, noun_tables)
            ids = plan_map.get(noun_type, {}).get("eligible_ids", [])

        if not ids:
            log.debug("[apply_noun_archive] nothing to do for", noun_type)
            results[noun_type] = {"ok": True, "affected": 0}
            continue

        pol = (policy.get("nouns") or {}).get(noun_type, {})
        strategy = pol.get("strategy") or policy.get("default", {}).get("strategy", "soft")

        affected = _archive_noun_ids(project_path, noun_type, pf, ids, strategy)
        log.debug("[apply_noun_archive] archived", affected, "records for", noun_type)

        if strategy == "hard":
            try:
                with _open_db(project_path, "archive_sql_db") as index_arc:
                    _insert_noun_archive_index_rows(
                        index_arc, project_path.name, noun_type, collection_for_noun(noun_type), pf, ids, strategy, ""
                    )
                    _safe_commit(index_arc.conn)
            except Exception as e:
                log.debug("[apply_noun_archive] warning: failed to write noun archive index rows:", e)

        results[noun_type] = {"ok": True, "affected": affected, "strategy": strategy}

    log.debug("\n[apply_noun_archive] all done")
    return results

# ------------------------------------------------------------------------------
# Runs archive/restore (file ops + linked nouns; RDS-aware for nouns)
# ------------------------------------------------------------------------------

@router.post("/{project}/runs/archive/preview")
def preview_run_archive(
    project: str,
    items: List[Dict[str, str]] = Body(...),
    strategy: str = Query("hard", pattern="^(soft|hard)$")
):
    log.debug("[preview_run_archive]", project, "strategy=", strategy, "items=", len(items))
    project_path = _resolve_project_path(project)
    norm_items = _normalize_run_items(project_path, items)
    if strategy == "soft":
        plan = plan_archive_runs_soft(norm_items)
    else:
        plan = plan_archive_runs_hard(norm_items)
    return _serialize_plan(plan)

@router.post("/{project}/runs/archive/apply")
def apply_run_archive(
    project: str,
    payload: Any = Body(...),
    strategy: str = Query("hard", pattern="^(soft|hard)$")
):
    if isinstance(payload, dict) and "verb_group" in payload and "run_ids" in payload:
        vg = payload.get("verb_group")
        items: List[Dict[str, str]] = [{"run_id": str(rid), "verb_group": vg} for rid in (payload.get("run_ids") or [])]
    elif isinstance(payload, list):
        items = payload
    else:
        raise AppError("INVALID_REQUEST_BODY", "Body must be a list of items or {'verb_group','run_ids'}.", status=422)

    log.debug("[apply_run_archive]", project, "strategy=", strategy, "items=", len(items))
    project_path = _resolve_project_path(project)

    norm_items = _normalize_run_items(project_path, items)

    try:
        for it in norm_items:
            _ensure_verb_archive_exists(project_path, it["verb_group"])
    except Exception as e:
        raise AppError("ARCHIVE_FOLDER_ENSURE_FAILED", f"Failed to ensure archive folder(s): {e}", status=500, details={"project": project})

    plan = plan_archive_runs_hard(norm_items) if strategy == "hard" else plan_archive_runs_soft(norm_items)

    runs_result = _execute_plan(project_path, plan)

    try:
        with _open_db(project_path, "archive_sql_db") as arc:
            for it in norm_items:
                _insert_runs_archive_index_row(
                    arc=arc,
                    project=project_path.name,
                    run_id=it["run_id"],
                    verb=it.get("test_type") or it.get("verb"),
                    verb_group=it.get("verb_group"),
                    archive_path=it.get("dst_dir"),
                    strategy=strategy,
                )
            _safe_commit(arc.conn)
    except Exception as e:
        log.debug("[apply_run_archive] ERROR: Failed to write to runs_archive_index:", e)
        runs_result["index_error"] = str(e)

    policy = _load_policy(project_path)
    noun_types = _seams.load_schema(project_path, "noun")  # dict

    selection: Dict[str, List[str]] = {}
    for it in norm_items:
        hinted_nt, ids = _collect_linked_noun_ids(project_path, it.get("test_type"), it["run_id"])
        if hinted_nt and ids:
            selection.setdefault(hinted_nt, []).extend(ids)
        else:
            scan_map = _collect_linked_noun_ids_by_scan(project_path, it["run_id"])
            if not scan_map:
                log.debug(f"[apply_run_archive] no linked nouns found for run_id={it['run_id']} (this is normal).")
            for nt, idlist in scan_map.items():
                selection.setdefault(nt, []).extend(idlist)

    for nt in list(selection.keys()):
        selection[nt] = sorted(set(selection[nt]))

    noun_results: Dict[str, Any] = {}

    for noun_type, ids in selection.items():
        if not ids:
            noun_results[noun_type] = {"ok": True, "affected": 0}
            continue

        entry = noun_types.get(noun_type)
        if not entry:
            noun_results[noun_type] = {"ok": False, "error": "Unknown noun type"}
            continue

        pf = _noun_pf(project_path, noun_type, entry)
        pol = (policy.get("nouns") or {}).get(noun_type, {})
        noun_strategy = pol.get("strategy", "soft")

        affected = _archive_noun_ids(project_path, noun_type, pf, ids, noun_strategy)

        if noun_strategy == "hard":
            try:
                with _open_db(project_path, "archive_sql_db") as index_arc:
                    _insert_noun_archive_index_rows(
                        index_arc, project_path.name, noun_type, collection_for_noun(noun_type), pf, ids, noun_strategy, ""
                    )
                    _safe_commit(index_arc.conn)
            except Exception as e:
                log.debug("[apply_run_archive] warning: failed to write noun archive index rows for noun:", noun_type, e)

        noun_results[noun_type] = {
            "ok": True,
            "affected": affected,
            "strategy": noun_strategy
        }

    return {"ok": runs_result.get("ok", True), "runs": runs_result, "nouns": noun_results}

@router.post("/{project}/runs/restore/apply")
def apply_run_restore(
    project: str,
    payload: Any = Body(...)
):
    """
    Restores run folders AND their linked nouns (soft+hard).
    """
    log.debug("\n=== [apply_run_restore] START ===")
    log.debug("[apply_run_restore] raw payload:", payload)

    if isinstance(payload, dict) and "verb_group" in payload and "run_ids" in payload:
        vg = payload.get("verb_group")
        items: List[Dict[str, Any]] = [{"run_id": str(rid), "verb_group": vg} for rid in (payload.get("run_ids") or [])]
        log.debug("[apply_run_restore] normalized simple payload -> items:", items)
    elif isinstance(payload, list):
        items = payload
        log.debug("[apply_run_restore] using advanced payload -> items:", items)
    else:
        raise AppError("INVALID_REQUEST_BODY", "Body must be a list of items or {'verb_group','run_ids'}.", status=422)

    log.debug("[apply_run_restore] project:", project, "items count:", len(items))
    project_path = _resolve_project_path(project)
    log.debug("[apply_run_restore] project_path:", project_path)

    norm_items = _normalize_restore_items(project_path, items)
    log.debug("[apply_run_restore] norm_items:", norm_items)

    try:
        for it in norm_items:
            log.debug("[apply_run_restore] ensuring dirs for verb_group:", it["verb_group"])
            _ensure_verb_archive_exists(project_path, it["verb_group"])
            _ensure_hot_dump_parent_exists(project_path, it["verb_group"])
    except Exception as e:
        log.debug("[apply_run_restore] X Failed to ensure dirs:", e)
        raise AppError("RUN_DIRECTORY_ENSURE_FAILED", f"Failed to ensure run directories: {e}", status=500, details={"project": project})

    log.debug("[apply_run_restore] building restore plan for runs...")
    runs_plan = plan_restore_runs(norm_items)
    log.debug("[apply_run_restore] runs_plan steps:", len(runs_plan.steps))
    try:
        runs_result = _execute_plan(project_path, runs_plan)
        log.debug("[apply_run_restore] runs_result:", runs_result)
    except Exception as e:
        log.debug("[apply_run_restore] ERROR executing runs plan:", repr(e))
        raise AppError("RUN_RESTORE_FAILED", f"Failed to restore runs: {str(e)}", status=500, details={"project": project})

    log.debug("[apply_run_restore] beginning noun restore...")
    noun_schema = _seams.load_schema(project_path, "noun")  # dict

    selection: Dict[str, List[str]] = {}
    for it in norm_items:
        log.debug("[apply_run_restore] checking linked nouns for run:", it["run_id"])
        hinted_nt, ids = _collect_linked_noun_ids(project_path, it.get("test_type"), it["run_id"])
        if hinted_nt and ids:
            log.debug("[apply_run_restore] linked via schema ->", hinted_nt, ids)
            selection.setdefault(hinted_nt, []).extend(ids)
        else:
            log.debug(f"[apply_run_restore] no schema hint; scanning all tables for run_id={it['run_id']} (0 linked is fine).")
            scan_map = _collect_linked_noun_ids_by_scan(project_path, it["run_id"])
            log.debug(f"[apply_run_restore] scan_map: {len(scan_map)} noun type(s) linked (0 is fine).")
            for nt, idlist in scan_map.items():
                selection.setdefault(nt, []).extend(idlist)

    for nt in list(selection.keys()):
        before = selection[nt]
        selection[nt] = sorted(set(selection[nt]))
        log.debug(f"[apply_run_restore] dedup {nt}: before={before}, after={selection[nt]}")

    noun_results: Dict[str, Any] = {}

    for noun_type, ids in selection.items():
        log.debug(f"\n[apply_run_restore] noun_type={noun_type}, candidate_ids={ids}")
        if not ids:
            noun_results[noun_type] = {"ok": True, "affected": 0}
            log.debug(f"[apply_run_restore] noun_type={noun_type} -> no IDs, skip")
            continue

        entry = noun_schema.get(noun_type) or {}
        pf = _noun_pf(project_path, noun_type, entry)
        log.debug(f"[apply_run_restore] noun_type={noun_type}, primary_field={pf}")

        soft_ids, hard_ids = _split_noun_ids_by_actual_archive(project_path, noun_type, ids)
        log.debug(f"[apply_run_restore] split: soft_ids={soft_ids}, hard_ids={hard_ids}")

        _restore_noun_ids(project_path, noun_type, pf, soft_ids, "soft")
        _restore_noun_ids(project_path, noun_type, pf, hard_ids, "hard")

        noun_results[noun_type] = {
            "ok": True,
            "affected": len(soft_ids) + len(hard_ids),
            "restored_soft": len(soft_ids),
            "restored_hard": len(hard_ids)
        }
        log.debug(f"[apply_run_restore] noun_results[{noun_type}] ->", noun_results[noun_type])

    final = {"ok": runs_result.get("ok", True), "runs": runs_result, "nouns": noun_results}
    log.debug(f"[apply_run_restore] FINAL RESULT: runs_ok={final.get('ok', True)}, noun_types_restored={len(final.get('nouns', {}))} (0 is fine)")
    log.debug("=== [apply_run_restore] END ===\n")
    return final

@router.post("/{project}/runs/restore/preview")
def preview_run_restore(
    project: str,
    items: List[Dict[str, Any]] = Body(...)
):
    log.debug("[preview_run_restore]", project, "items=", len(items))
    project_path = _resolve_project_path(project)
    norm_items = _normalize_restore_items(project_path, items)
    for it in norm_items:
        _ensure_verb_archive_exists(project_path, it["verb_group"])
        _ensure_hot_dump_parent_exists(project_path, it["verb_group"])
    plan = plan_restore_runs(norm_items)
    return _serialize_plan(plan)

@router.get("/{project}/verb_groups")
def list_verb_groups(
    project: str,
    include_hidden: bool = Query(False, description="Include folders beginning with '.'")
) -> List[str]:
    """
    S3-aware folder listing for verb groups via json_proxy; FS fallback.
    """
    log.debug("[list_verb_groups] start", project)
    project_path = _resolve_project_path(project)
    try:
        verbs_root = _seams.resolve_path(project_path, "verbs_dir")
        log.debug("[list_verb_groups] verbs_root:", verbs_root)
        groups = _jp_list_dirnames(verbs_root.as_posix(), include_hidden=include_hidden)
        log.debug("[list_verb_groups] ->", groups)
        return groups
    except Exception as e:
        log.debug("[list_verb_groups] Error:", str(e))
        raise AppError("VERB_GROUPS_LIST_FAILED", f"Failed to list verb groups: {e}", status=500, details={"project": project})

@router.get("/{project}/runs/list")
def list_runs(
    project: str,
    verb_group: str = Query(..., description="Name of the verb group (folder under verbs_dir)"),
    where: Literal["active", "archived"] = Query("active"),
):
    """
    List runs for a verb group by reading from the verb group log (DB or JSONL).
    For 'active' runs, filters out those that exist in the archive folder.
    For 'archived' runs, returns only those that exist in the archive folder.
    Archive presence is detected via S3 prefix listing or FS folder listing.
    """
    log.debug(f"[list_runs] project={project}, verb_group={verb_group}, where={where}")
    project_path = _resolve_project_path(project)

    try:
        # Use S3-aware helpers from i_o (DB-first; JSONL fallback)
        cfg = _seams.get_verb_group_log_config(project_path, verb_group)
        primary_id_field = cfg.get("primary_id")
        log.debug(f"[list_runs] primary_id_field: {primary_id_field}")

        if not primary_id_field:
            log.debug("[list_runs] no primary_id field in config")
            return {"runs": []}

        entries = _seams.load_verb_group_log(project_path, verb_group) or []
        all_runs = []
        for entry in entries:
            rid = entry.get(primary_id_field)
            if rid is not None:
                all_runs.append(str(rid))

        log.debug(f"[list_runs] found {len(all_runs)} total runs in log")

        archive_base = _seams.resolve_path(project_path, "data_dump_archive", verb_group=verb_group).as_posix()
        archived_dirs = set(_jp_list_dirnames(archive_base))

        if where == "active":
            runs = [r for r in all_runs if r not in archived_dirs]
            log.debug(f"[list_runs] active runs (not in archive): {runs}")
        else:
            runs = [r for r in all_runs if r in archived_dirs]
            log.debug(f"[list_runs] archived runs (in archive): {runs}")

        return {"runs": runs}

    except Exception as e:
        log.debug(f"[list_runs] error: {e}")
        import traceback
        log.debug(f"[list_runs] traceback: {traceback.format_exc()}")
        return {"runs": []}

# ------------------------------------------------------------------------------
# Archive table ensure (RDS-aware)
# ------------------------------------------------------------------------------


# ------------------------------------------------------------------------------
# Run helpers for paths
# ------------------------------------------------------------------------------

def _normalize_run_items(project_path: Path, items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for it in (items or []):
        rid = it.get("run_id") or it.get("run") or it.get("id")
        if not rid:
            raise AppError("RUN_ID_REQUIRED", "Each run item must include 'run_id'.", status=400)
        
        test_type = it.get("test_type") or it.get("verb") or it.get("verb_name")
        verb_group = it.get("verb_group") or _resolve_verb_group(project_path, test_type) or "Tests"
        
        if not test_type:
            try:
                test_type = _lookup_verb_from_run_log(project_path, verb_group, rid)
                log.debug(f"[_normalize_run_items] Looked up test_type from log: {test_type}")
            except Exception as e:
                log.debug(f"[_normalize_run_items] Could not look up test_type for run {rid}: {e}")

        try:
            src_dir = it.get("src_dir") or str(_seams.resolve_path(project_path, "data_dump_dir", verb_group=verb_group, run_id=rid))
        except Exception as e:
            raise AppError("SRC_DIR_RESOLVE_FAILED", f"Could not resolve src_dir for run {rid}: {e}", status=400, details={"run_id": rid})

        try:
            dst_root = _seams.resolve_path(project_path, "data_dump_archive", verb_group=verb_group)
            dst_dir = it.get("dst_dir") or str((dst_root / rid))
        except Exception as e:
            raise AppError("DST_DIR_RESOLVE_FAILED", f"Could not resolve dst_dir for run {rid}: {e}", status=400, details={"run_id": rid})

        log.debug(f"[_normalize_run_items] Normalized: run_id={rid}, test_type={test_type}, verb_group={verb_group}")

        out.append({
            **it,
            "run_id": rid,
            "test_type": test_type,
            "verb": test_type,
            "verb_group": verb_group,
            "src_dir": src_dir,
            "dst_dir": dst_dir
        })
    return out

def _lookup_verb_from_run_log(project_path: Path, verb_group: str, run_id: str) -> Optional[str]:
    """
    DB-first (S3-aware) lookup of verb (test_type) via the verb group log.
    """
    try:
        cfg = _seams.get_verb_group_log_config(project_path, verb_group)
        primary_id_field = cfg.get("primary_id")
        verb_field = cfg.get("verb_field") or "test_type"
        if not primary_id_field:
            return None
        entries = _seams.load_verb_group_log(project_path, verb_group) or []
        for entry in entries:
            if str(entry.get(primary_id_field)) == str(run_id):
                return entry.get(verb_field) or entry.get("test_type") or entry.get("verb") or entry.get("verb_name")
        return None
    except Exception as e:
        log.debug(f"[_lookup_verb_from_run_log] Error: {e}")
        return None

def _ensure_verb_archive_exists(project_path: Path, verb_group: str):
    vg_path = _seams.resolve_path(project_path, "verb_group", verb_group=verb_group)
    ensure_prefix(vg_path)  # S3/FS-safe
    arc_root = _seams.resolve_path(project_path, "data_dump_archive", verb_group=verb_group)
    ensure_prefix(arc_root)  # S3/FS-safe

def _ensure_hot_dump_parent_exists(project_path: Path, verb_group: str):
    vg = _seams.resolve_path(project_path, "verb_group", verb_group=verb_group)
    hot_parent = vg / "data_dumps"
    ensure_prefix(hot_parent)  # S3/FS-safe

def _normalize_restore_items(project_path: Path, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    norm: List[Dict[str, Any]] = []
    for it in (items or []):
        rid = it.get("run_id") or it.get("run") or it.get("id")
        if not rid:
            raise AppError("RUN_ID_REQUIRED", "Each restore item must include 'run_id'.", status=400)
        test_type = it.get("test_type") or it.get("verb") or it.get("verb_name")
        verb_group = it.get("verb_group") or _resolve_verb_group(project_path, test_type) or "Tests"

        arc_root = _seams.resolve_path(project_path, "data_dump_archive", verb_group=verb_group)
        arc_dir = Path(it.get("arc_dir") or (arc_root / rid))
        hot_dir = Path(it.get("hot_dir") or _seams.resolve_path(project_path, "data_dump_dir", verb_group=verb_group, run_id=rid))

        # S3-aware existence (prefix_exists) with FS fallback
        has_hard = _jp_prefix_exists(arc_dir.as_posix())
        has_soft = _jp_prefix_exists(hot_dir.as_posix()) and not has_hard

        norm.append({
            **it,
            "run_id": rid,
            "test_type": test_type,
            "verb_group": verb_group,
            "arc_dir": str(arc_dir),
            "hot_dir": str(hot_dir),
            "has_hard": bool(it.get("has_hard", has_hard)),
            "has_soft": bool(it.get("has_soft", has_soft)),
        })
    return norm

def _resolve_verb_group(project_path: Path, test_type: Optional[str], fallback: Optional[str] = None) -> str:
    try:
        if test_type:
            vs = _seams.get_verb_schema(project_path, test_type) or {}
            vg = vs.get("verb_group")
            if vg:
                return vg
    except Exception:
        pass
    return fallback or "Tests"
