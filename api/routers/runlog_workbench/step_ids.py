# api/routers/runlog_workbench/step_ids.py
"""Linear step-id listing endpoint."""

from ._router import router
from ._shared import (
    AppError,
    resolve_run_id_to_test_type,
    resolve_verb_group_from_test_type,
    get_verb_schema,
)
from .gates import _project_path


@router.get("/runlog/{project}/{verb_group}/{run_id}/status/step_ids")
def get_linear_step_ids(project: str, verb_group: str, run_id: str):
    pp = _project_path(project)

    verb = resolve_run_id_to_test_type(pp, run_id)
    if not verb:
        raise AppError("RUN_NOT_FOUND", f"Run '{run_id}' not found (verb not resolved).", status=404,
                       details={"project": project, "run_id": run_id})
    resolved_group = resolve_verb_group_from_test_type(pp, verb) or verb_group

    schema = get_verb_schema(pp, verb) or {}
    ls = (schema.get("linear_status") or {})
    steps = list(ls.get("steps") or [])

    return {
        "ok": True,
        "verb": verb,
        "group": resolved_group,
        "run_id": run_id,
        "step_ids": [s.get("id") for s in steps],
        "steps": [
            {
                "index": i,
                "id": s.get("id"),
                "type": s.get("type"),
                "label": s.get("label"),
                "required": bool(s.get("required", True)),
                "source": s.get("source"),
            }
            for i, s in enumerate(steps)
        ],
    }
