# api/routers/runlog_workbench/grid.py
"""Glide Grid endpoints (ported & primary-id agnostic)."""

import traceback
from pathlib import Path
from datetime import datetime

from fastapi import Body

from ._router import router
from ._shared import (
    AppError,
    get_project_path,
    resolve_path,
    resolve_data_dump_contents,
    load_data,
    save_json,
    resolve_run_id_to_test_type,
    get_verb_schema,
    get_noun_schema,
    fs_mkdirs,
    _list_run_ids,
    _normalize_to_grid,
    log,
)

# -----------------------------------------------------------------------------
# NEW: Glide Grid endpoints (ported & primary-id agnostic)
# -----------------------------------------------------------------------------

@router.get("/grid/runs/{project}/{verb_group}")
def grid_runs(project: str, verb_group: str):
    proj = get_project_path(project)
    return {"project": project, "verb_group": verb_group, "runs": _list_run_ids(proj, verb_group)}

@router.get("/grid/load/{project}/{verb_group}/{run_id}")
def grid_load(project: str, verb_group: str, run_id: str):
    proj = get_project_path(project)
    path = resolve_path(proj, "data_entry", verb_group=verb_group, run_id=run_id)
    try:
        data = load_data(path) or []
    except Exception as e:
        raise AppError("GRID_LOAD_FAILED", f"Load error: {e}", status=404,
                       details={"project": project, "verb_group": verb_group, "run_id": run_id})
    return _normalize_to_grid(data)

@router.post("/gui/grid/save/{project}/{verb_group}/{run_id}")
def grid_save(
    project: str,
    verb_group: str,
    run_id: str,
    payload: dict = Body(...),
    storage_backend: str = "both"  # "jsonl", "sql", or "both"
):
    proj_path = get_project_path(project)
    headers = list(payload.get("headers") or [])
    rows    = list(payload.get("rows") or [])

    data_entry_path = resolve_path(proj_path, "data_entry", verb_group=verb_group, run_id=run_id)
    dump_dir = data_entry_path.parent
    fs_mkdirs(dump_dir)

    # Trace via the logger only (was also tee'd to a per-run grid_save_debug.log on every save —
    # dropped: it dirtied the run dir on each keystroke-autosave and duplicated the logger output).
    rid = datetime.utcnow().strftime("%Y%m%dT%H%M%S.%fZ")
    def dbg(msg: str):
        log.debug(f"[grid_save {rid}] {msg}")

    dbg(f"start project={project} verb_group={verb_group} run_id={run_id} backend={storage_backend}")
    dbg(f"incoming payload: headers={len(headers)} rows={len(rows)}")

    try:
        existing_shape = None
        try:
            existing_shape = load_data(data_entry_path)
        except Exception:
            existing_shape = None

        # Captured BEFORE the write so the SQL block below can restore it on failure (R4:
        # the DataEntry.json snapshot + the instances update commit together, or neither).
        de_prior_snapshot = existing_shape
        de_existed = existing_shape is not None

        if isinstance(existing_shape, dict) and "headers" in existing_shape and "rows" in existing_shape:
            save_json(data_entry_path, {"headers": headers, "rows": rows})
            dbg("DataEntry.json write OK (headers+rows)")
        else:
            save_json(data_entry_path, rows)
            dbg("DataEntry.json write OK (list-only)")
    except Exception as e:
        dbg(f"ERROR writing DataEntry.json: {e!r}")
        raise AppError("GRID_SAVE_FAILED", f"Save error: {e}", status=400,
                       details={"project": project, "verb_group": verb_group, "run_id": run_id})

    try:
        test_type = resolve_run_id_to_test_type(proj_path, run_id)
        if not test_type:
            dbg("Skipping items/SQL: no test type found for run_id.")
            return {"status": "DataEntry.json saved, items/SQL skipped (no test type)."}

        verb_schema = get_verb_schema(proj_path, test_type) or {}
        noun_type_ref = verb_schema.get("data_entry_schema", {}).get("set_up_inputs", {}).get("noun_type_ref")
        if not noun_type_ref:
            dbg("Skipping items/SQL: no noun_type_ref in verb schema.")
            return {"status": "DataEntry.json saved, items/SQL skipped (no noun reference)."}

        noun_schema = get_noun_schema(proj_path, noun_type_ref) or {}
        pid_field = noun_schema.get("primary_id_field") or "id"
        _pkey = noun_schema.get("primary_id_field") or "_rowid"

        current_run_items_with_id = []
        for r in rows:
            if isinstance(r, dict) and str(r.get(pid_field, "")).strip():
                r["_runID"] = run_id
                current_run_items_with_id.append(r)

        dbg(f"Resolved noun '{noun_type_ref}'. Found {len(current_run_items_with_id)} items with a primary ID to save.")

    except Exception as e:
        dbg(f"ERROR during schema lookup: {e!r}")
        raise AppError("SCHEMA_RESOLUTION_FAILED", f"Schema resolution failed: {e}", status=500,
                       details={"project": project, "verb_group": verb_group, "run_id": run_id})

    # Persist this run's noun items to the unified instances store (SQL-only). Matches the legacy
    # "replace this run's rows": drop this run's previously-saved items that are no longer present,
    # then upsert the current set. (DataEntry.json above remains the run snapshot.)
    try:
        from core.storage.factory import collection_for_noun, get_record_store
        store = get_record_store(proj_path)
        coll = collection_for_noun(noun_type_ref)
        new_keys = {str(it.get(pid_field)) for it in current_run_items_with_id
                    if str(it.get(pid_field, "")).strip()}
        # Unit of work (R4): drop this run's stale rows + upsert the current set atomically —
        # commit together or roll back together, so a mid-update failure never leaves a partial
        # instances state alongside the DataEntry.json snapshot.
        with store.transaction() as txn:
            for existing in txn.list_records(coll):
                if existing.get("_runID") == run_id and str(existing.get(pid_field)) not in new_keys:
                    txn.delete_record(coll, pid_field, existing.get(pid_field))
            for it in current_run_items_with_id:
                if str(it.get(pid_field, "")).strip():
                    txn.put_record(coll, pid_field, {k: (None if v == "" else v) for k, v in it.items()})
        dbg(f"instances store updated ({len(current_run_items_with_id)} items for run {run_id})")
    except Exception as e:
        # The instances transaction rolled back; restore the DataEntry.json snapshot to its prior
        # state so the run is left exactly as before (FS + SQL together, or neither). A brand-new
        # run keeps its just-written snapshot — a valid DataEntry-only state, as in the skip cases.
        if de_existed:
            try:
                save_json(data_entry_path, de_prior_snapshot)
                dbg("rolled DataEntry.json back to its prior snapshot after instances failure")
            except Exception as re:
                dbg(f"DataEntry.json rollback FAILED: {re!r}")
        dbg(f"ERROR updating instances store: {e!r}\n{traceback.format_exc()}")
        raise AppError("INSTANCES_UPDATE_FAILED", f"instances update failed: {e!r}", status=500,
                       details={"project": project, "verb_group": verb_group, "run_id": run_id,
                                "noun_type": noun_type_ref})

    return {
        "status": "Save successful",
        "rows_written_to_items": len(current_run_items_with_id),
        "storage_backend": storage_backend
    }

@router.get("/grid/dump/{project}/{verb_group}/{run_id}")
def grid_dump(project: str, verb_group: str, run_id: str):
    proj = get_project_path(project)
    try:
        dump = resolve_data_dump_contents(proj, verb_group=verb_group, run_id=run_id)
    except Exception as e:
        raise AppError("DATA_DUMP_NOT_FOUND", str(e), status=404,
                       details={"project": project, "verb_group": verb_group, "run_id": run_id})

    def _p(x: Path) -> str: return str(x)
    files = {
        "data_entry": _p(dump["files"]["data_entry"]),
        "status": _p(dump["files"]["status"]),
        "adverbs": _p(dump["files"]["adverbs"]),
        "other_files": {k: _p(v) for k, v in dump["files"]["other_files"].items()},
    }
    folders = {k: {"path": _p(v["path"]), "files": [_p(f) for f in v["files"]]} for k, v in dump["folders"].items()}
    return {"project": project, "verb_group": verb_group, "run_id": run_id, "files": files, "folders": folders}
