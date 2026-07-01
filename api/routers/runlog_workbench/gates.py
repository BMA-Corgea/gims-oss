# api/routers/runlog_workbench/gates.py
"""Gate operations (linear_status in Status.json) + gate helpers.

``_project_path`` is defined here (its "home" area) and re-used by the step_ids
submodule.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, Query, Request

from nodes.compliance.compliance_node import _append_event
from nodes.compliance.esign import enforce_gate_signoff_reauth
from nodes.login_rules_node import require_gate_signoff
from ._router import router
from ._shared import (
    AppError,
    resolve_path,
    fs_exists,
    load_data,
    save_json,
    resolve_run_id_to_test_type,
    resolve_verb_group_from_test_type,
    get_verb_schema,
    _ensure_linear_status_fresh,
    log,
)

# -----------------------------------------------------------------------------
# Gate operations (linear_status in Status.json)
# -----------------------------------------------------------------------------

def _project_path(project: str) -> Path:
    projects_root = resolve_path(Path(), "project_root")
    # Avoid .resolve() to keep S3-compat; rely on fs_exists
    pp = (projects_root / project)
    if not fs_exists(pp):
        raise AppError("PROJECT_NOT_FOUND", f"Project '{project}' not found.", status=404,
                       details={"project": project})
    return pp

def _status_paths_for_run(pp: Path, group: str, run_id: str) -> Dict[str, Path]:
    dump_dir   = resolve_path(pp, "data_dump_dir", verb_group=group, run_id=run_id)
    status_path = resolve_path(pp, "status_file",   verb_group=group, run_id=run_id)
    return {"dump_dir": dump_dir, "status_path": status_path}

def _load_linear_status(status_path: Path) -> dict:
    doc = load_data(status_path) or {}
    ls = doc.get("linear_status") or {}
    return {"doc": doc, "linear_status": ls}

def _save_status_json(status_path: Path, doc: dict) -> None:
    save_json(status_path, doc)

def _recalc_current_index(steps: List[dict]) -> int:
    for i, s in enumerate(steps or []):
        if not bool(s.get("completed")):
            return i
    return len(steps or [])

@router.get("/runlog/{project}/{verb_group}/{run_id}/status/linear")
def get_linear_status_for_run(project: str, verb_group: str, run_id: str):
    pp = _project_path(project)

    verb = resolve_run_id_to_test_type(pp, run_id)
    if not verb:
        raise AppError("RUN_NOT_FOUND", f"Run '{run_id}' not found (verb not resolved).", status=404,
                       details={"project": project, "run_id": run_id})
    resolved_group = resolve_verb_group_from_test_type(pp, verb) or verb_group

    try:
        from core.status import get_linear_status_progress
        get_linear_status_progress(pp, str(run_id))
    except Exception as e:
        log.debug("[linear][status/linear][refresh][error]", {"run_id": run_id, "err": repr(e)})

    paths = _status_paths_for_run(pp, resolved_group, run_id)
    status_info = _load_linear_status(paths["status_path"])
    ls = status_info["linear_status"] or {}

    enabled = bool(ls.get("enabled", False))
    steps   = list(ls.get("steps") or [])
    total   = len(steps)
    completed = sum(1 for s in steps if bool(s.get("completed")))
    progress = f"{completed}/{total}"

    return {
        "ok": True,
        "verb": verb,
        "group": resolved_group,
        "run_id": run_id,
        "enabled": enabled,
        "steps_total": total,
        "steps_completed": completed,
        "progress": progress,
        "status_path": str(paths["status_path"]),
        "gates": [
            {
                "index": i,
                "id": s.get("id"),
                "type": s.get("type"),
                "label": s.get("label"),
                "required": bool(s.get("required", False)),
                "completed": bool(s.get("completed", False)),
            }
            for i, s in enumerate(steps) if (s.get("type") == "gate")
        ],
    }

@router.get("/runlog/{project}/{verb_group}/{run_id}/gate/list")
def list_gates_for_run(project: str, verb_group: str, run_id: str):
    pp = _project_path(project)

    verb = resolve_run_id_to_test_type(pp, run_id)
    if not verb:
        raise AppError("RUN_NOT_FOUND", f"Run '{run_id}' not found (verb not resolved).", status=404,
                       details={"project": project, "run_id": run_id})

    resolved_group = resolve_verb_group_from_test_type(pp, verb) or verb_group
    verb_schema = get_verb_schema(pp, verb) or {}
    ls_schema = (verb_schema.get("linear_status") or {})
    if not bool(ls_schema.get("enabled")):
        raise AppError("VERB_NOT_LINEAR", f"Verb '{verb}' is not linear-enabled.", status=400,
                       details={"verb": verb})

    _ensure_linear_status_fresh(pp, resolved_group, run_id)

    paths = _status_paths_for_run(pp, resolved_group, run_id)
    status_info = _load_linear_status(paths["status_path"])
    ls = status_info["linear_status"] or {}
    steps = list(ls.get("steps") or [])

    if not steps:
        raise AppError("NO_LINEAR_STEPS", "No linear steps found in Status.json.", status=404,
                       details={"project": project, "run_id": run_id, "verb": verb})

    gates = [
        {
            "index": i,
            "id": s.get("id"),
            "label": s.get("label"),
            "completed": bool(s.get("completed", False)),
        }
        for i, s in enumerate(steps) if s.get("type") == "gate"
    ]

    return {
        "ok": True,
        "verb": verb,
        "group": resolved_group,
        "run_id": run_id,
        "status_path": str(paths["status_path"]),
        "gates": gates,
    }

@router.post(
    "/runlog/{project}/{verb_group}/{run_id}/gate/{step_id}/complete",
    # Server-side gate sign-off authorization (manager approval). Enforced here so
    # it cannot be bypassed by skipping the client orchestrate preflight.
    dependencies=[Depends(require_gate_signoff)],
)
async def complete_gate_step(
    project: str,
    verb_group: str,
    run_id: str,
    step_id: str,
    request: Request,
    completed: bool = Query(True, description="True=sign off, False=reopen"),
    body: Optional[Dict[str, Any]] = Body(default=None),
):
    pp = _project_path(project)

    # §11.200 two-component: completing/reopening a gate is an electronic signature, so beyond
    # the authorization dependency above we require a FRESH, server-verified password for the
    # authenticated user at the moment of signing. Returns the verified signer email (or None if
    # the control is disabled). Raises 401 if the password is missing/incorrect.
    action = "gate_signoff" if completed else "gate_reopen"
    password = (body or {}).get("password") if isinstance(body, dict) else None
    reason = (body or {}).get("reason") if isinstance(body, dict) else None
    signer_email = await enforce_gate_signoff_reauth(request, project, password=password, action=action)

    verb = resolve_run_id_to_test_type(pp, run_id)
    if not verb:
        raise AppError("RUN_NOT_FOUND", f"Run '{run_id}' not found (verb not resolved).", status=404,
                       details={"project": project, "run_id": run_id})
    resolved_group = resolve_verb_group_from_test_type(pp, verb) or verb_group

    verb_schema = get_verb_schema(pp, verb) or {}
    ls_schema = (verb_schema.get("linear_status") or {})
    if not bool(ls_schema.get("enabled")):
        raise AppError("VERB_NOT_LINEAR", f"Verb '{verb}' is not linear-enabled.", status=400,
                       details={"verb": verb})

    _ensure_linear_status_fresh(pp, resolved_group, run_id)

    paths = _status_paths_for_run(pp, resolved_group, run_id)
    status_info = _load_linear_status(paths["status_path"])
    doc = status_info["doc"]
    ls  = status_info["linear_status"] or {}
    steps = list(ls.get("steps") or [])

    if not steps:
        raise AppError("NO_LINEAR_STEPS", "No linear steps found in Status.json.", status=404,
                       details={"project": project, "run_id": run_id, "verb": verb})

    idx = None
    for i, s in enumerate(steps):
        if s.get("internal_id") == step_id:
            idx = i
            if s.get("type") != "gate":
                raise AppError("STEP_NOT_GATE", f"Step '{step_id}' is not a gate.", status=400,
                               details={"step_id": step_id, "run_id": run_id})
            break
    if idx is None:
        raise AppError("GATE_STEP_NOT_FOUND", f"Gate step '{step_id}' not found in Status.json.",
                       status=404, details={"step_id": step_id, "run_id": run_id})

    steps[idx]["completed"] = bool(completed)

    ls["steps"] = steps
    ls["current_index"] = _recalc_current_index(steps)
    doc["linear_status"] = ls

    _save_status_json(paths["status_path"], doc)

    # Record the approval as a BOUND e-signature in the HMAC-chained compliance trail: signer +
    # meaning (gate_signoff/gate_reopen) + reason are hashed into the record (P5). Best-effort —
    # the gate state is already persisted, so a logging hiccup must not fail the operation.
    if signer_email:
        try:
            await _append_event(
                project,
                user_id=signer_email,
                method="POST",
                path=str(request.url.path),
                payload=json.dumps(
                    {"action": action, "run_id": run_id, "step_id": step_id,
                     "verb": verb, "gate": step_id, "completed": bool(completed)},
                    sort_keys=True,
                ),
                status=200,
                signer=signer_email,
                signature_meaning=action,
                reason=reason,
            )
        except Exception as e:
            log.debug("[gate] signature log failed (non-fatal):", repr(e))

    total = len(steps)
    done  = sum(1 for s in steps if bool(s.get("completed")))
    return {
        "ok": True,
        "verb": verb,
        "group": resolved_group,
        "run_id": run_id,
        "step_id": step_id,
        "completed": bool(completed),
        "steps_total": total,
        "steps_completed": done,
        "progress": f"{done}/{total}",
        "current_index": ls["current_index"],
        "status_path": str(paths["status_path"]),
    }
