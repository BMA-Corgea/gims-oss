# api/routers/runlog_workbench/status.py
"""Status.json endpoints (linear + full schema)."""

import traceback
from typing import Any, Dict, List, Optional

from fastapi.responses import JSONResponse

from ._router import router
from ._shared import (
    AppError,
    get_project_path,
    resolve_path,
    fs_exists,
    load_data,
    resolve_run_id_to_test_type,
    get_verb_schema,
    get_status_breakdown_core,
    log,
)

# -----------------------------------------------------------------------------
# Status.json endpoints (linear + full schema)
# -----------------------------------------------------------------------------

def _summarize_steps(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(steps)
    completed = sum(1 for s in steps if bool(s.get("completed")))
    first_incomplete_idx: Optional[int] = next(
        (i for i, s in enumerate(steps) if not bool(s.get("completed"))),
        None
    )
    first_incomplete: Optional[Dict[str, Any]] = None
    if first_incomplete_idx is not None:
        s = steps[first_incomplete_idx]
        first_incomplete = {
            "index": first_incomplete_idx,
            "id": s.get("id"),
            "type": s.get("type"),
            "label": s.get("label"),
            "required": bool(s.get("required", True)),
            "source": s.get("source"),
            "completed": bool(s.get("completed", False)),
            "reason": s.get("reason"),
        }

    return {
        "mode": "linear",
        "steps": steps,
        "steps_total": total,
        "steps_completed": completed,
        "progress": f"{completed}/{total}",
        "first_incomplete": first_incomplete,
        "linear_steps_total": total,
        "linear_steps_completed": completed,
        "linear_progress": f"{completed}/{total}",
        "details": {
            "mode": "linear",
            "steps_total": total,
            "steps_completed": completed,
            "progress_text": f"{completed}/{total}",
            "first_incomplete": first_incomplete,
        },
    }

@router.get("/runlog/{project}/{group}/{run_id}/status.json")
def get_full_status(project: str, group: str, run_id: str):
    project_path = get_project_path(project)
    status_path = resolve_path(project_path, "status_file", verb_group=group, run_id=run_id)

    if not fs_exists(status_path):
        raise AppError("STATUS_JSON_NOT_FOUND", f"Status.json not found for run {run_id}", status=404,
                       details={"project": project, "group": group, "run_id": run_id})

    try:
        data = load_data(status_path) or {}
    except Exception as e:
        raise AppError("STATUS_JSON_READ_FAILED", f"Could not read Status.json: {e}", status=500,
                       details={"project": project, "group": group, "run_id": run_id})

    verb_name = resolve_run_id_to_test_type(project_path, run_id)
    if verb_name:
        verb_schema = get_verb_schema(project_path, verb_name) or {}
        ls = (verb_schema or {}).get("linear_status") or {}
        if ls and ls.get("enabled") and (ls.get("steps") or []):
            from core.status import get_linear_status_progress
            _ = get_linear_status_progress(project_path, run_id)
            try:
                data = load_data(status_path) or data
            except Exception as e:
                log.debug(f"Failed to re-read status file: {e}")

    steps_source = data.get("linear_status") or data
    steps: List[Dict[str, Any]] = list((steps_source or {}).get("steps") or [])

    if steps:
        summary = _summarize_steps(steps)
        normalized = {**summary, **data}
        return JSONResponse(content=normalized)

    return JSONResponse(content=data)

@router.get("/runlog/{project}/{verb_group}/{run_id}/status")
def get_status_breakdown(project: str, verb_group: str, run_id: str):
    try:
        project_path = get_project_path(project)
        breakdown = get_status_breakdown_core(project_path, str(run_id))
        if breakdown.get("mode") == "linear":
            from core.status import get_linear_status_progress
            linear_details = get_linear_status_progress(project_path, str(run_id)) or {}
            breakdown["details"] = linear_details
        return {"ok": True, "status": breakdown}
    except Exception as e:
        log.debug(f"[status-refresh] error: {e}\n{traceback.format_exc()}")
        raise AppError("STATUS_LOAD_FAILED", f"Error loading status for {run_id}: {e}", status=500,
                       details={"project": project, "verb_group": verb_group, "run_id": run_id})
