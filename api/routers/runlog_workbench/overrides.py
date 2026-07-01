# api/routers/runlog_workbench/overrides.py
"""Overrides editor endpoints (primary-id aware where relevant)."""

from fastapi import Body

from ._router import router
from ._shared import (
    AppError,
    get_project_path,
    _group_pid_field,
    resolve_path,
    fs_exists,
    load_data,
    save_json,
    load_override,
    save_override,
    resolve_run_id_to_test_type,
    load_verb_group_log,
    load_schema,
)

# -----------------------------------------------------------------------------
# Overrides editor endpoints (primary-id aware where relevant)
# -----------------------------------------------------------------------------

@router.post("/runlog/{project}/{group}/{run_id}/override/update")
def update_conjunctions(project: str, group: str, run_id: str, payload: dict = Body(...)):
    project_path = get_project_path(project)

    status_file = resolve_path(project_path, "status_file", verb_group=group, run_id=run_id)
    if not fs_exists(status_file):
        raise AppError("STATUS_FILE_NOT_FOUND", f"Status file not found: {status_file}", status=404,
                       details={"project": project, "group": group, "run_id": run_id,
                                "status_file": str(status_file)})

    current_status = load_data(status_file) or {}
    incoming = payload.get("overrides", []) or []
    current_status["conjunctions"] = incoming
    save_json(status_file, current_status)

    override_path = resolve_path(project_path, "override_file")
    existing = load_override(project_path)  # list[dict] or []
    verb = resolve_run_id_to_test_type(project_path, run_id) or payload.get("verb")

    new_rows = []
    for row in incoming:
        entry = dict(row)
        entry.setdefault("run", run_id)
        if verb:
            entry.setdefault("verb", verb)
        new_rows.append(entry)

    kept = [row for row in existing if str(row.get("run")) != str(run_id)]
    updated = kept + new_rows
    save_override(project_path, updated)

    return {
        "status": "success",
        "status_conjunctions": len(incoming),
        "override_rows_written": len(new_rows),
        "override_file": str(override_path),
    }

@router.get("/runlog/{project}/{group}/{run_id}/override")
def get_conjunctions(project: str, group: str, run_id: str):
    project_path = get_project_path(project)
    pid_field = _group_pid_field(project_path, group)

    status_file = resolve_path(project_path, "status_file", verb_group=group, run_id=run_id)
    status_data = load_data(status_file) or {}
    conjunctions = status_data.get("conjunctions", [])

    entries = load_verb_group_log(project_path, group)
    run = next((e for e in entries if str(e.get(pid_field)) == str(run_id)), None)
    if not run:
        raise AppError("RUN_NOT_FOUND", f"Run {run_id} not found in {group}", status=404,
                       details={"project": project, "group": group, "run_id": run_id})

    verb_key = run.get("test_type") or run.get("verb")
    verb_types = load_schema(project_path, "verb")
    verb_def = verb_types.get(verb_key, {}) or {}

    available = []
    for item in verb_def.get("status_values", []):
        if isinstance(item, dict):
            available.append({
                "type": item.get("name") or item.get("type") or "Unknown",
                "status": item.get("status") or "Exception",
                "fields": item.get("fields", [])
            })
        else:
            available.append({"type": str(item), "status": "Exception", "fields": []})

    return {
        "conjunctions": conjunctions,
        "available_types": available,
        "verb": verb_key
    }
