# api/routers/verb/routes_status_workflow.py
#
# Linear status-workflow endpoints. Handlers moved VERBATIM from
# api/routers/verb.py (no logic changes). Registered LAST so the literal
# "/verb/status-workflow/step-types" route precedes the param-bearing
# "/verb/status-workflow/{project}/{verb_name}" routes, exactly as in the
# original file.

from fastapi import Body, Query
from copy import deepcopy

from core.errors import AppError

from ._router import router
from ._log import log
from ._helpers import _get_project_path, _load_verb, _save_verb
from .linear_status import (
    _ALLOWED_STEP_TYPES,
    _validate_linear_status_block,
    _propose_linear_status,
)


# ─────────────────────────────────────────────────────────────
# Linear Status Workflow Endpoints
# ─────────────────────────────────────────────────────────────
@router.get("/verb/status-workflow/step-types")
def get_linear_status_step_types():
    """
    Introspection endpoint to help UIs build editors.
    """
    log.debug("[get_linear_status_step_types] start")
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
    log.debug("[get_linear_status_step_types] ok", {"allowed": out["allowed_types"]})
    return out

@router.get("/verb/status-workflow/{project}/{verb_name}")
def get_linear_status(project: str, verb_name: str, propose_if_missing: bool = Query(True)):
    """
    Read the linear_status block. If absent and propose_if_missing, also return a 'proposal'.
    """
    proj = _get_project_path(project)
    verbs, verb_def = _load_verb(proj, verb_name)

    ls = verb_def.get("linear_status")
    log.debug("[get_linear_status] loaded", {"has_linear_status": bool(ls)})

    out = {"verb": verb_name, "linear_status": ls or None}
    if not ls and propose_if_missing:
        log.debug("[get_linear_status] building proposal (missing linear_status)")
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

    log.debug("[put_linear_status] begin", {"verb": verb_name})
    block = payload.get("linear_status")
    if block is None:
        log.debug("[put_linear_status] missing linear_status key")
        raise AppError(
            "LINEAR_STATUS_REQUIRED",
            "Body must include 'linear_status' object",
            status=400,
        )

    # validate against current verb_def
    normalized = _validate_linear_status_block(verb_def, block)

    # persist
    new_def = deepcopy(verb_def)
    new_def["linear_status"] = normalized
    _save_verb(proj, verbs, verb_name, new_def)

    log.debug("[put_linear_status] saved", {"steps": len(normalized.get("steps", []))})
    return {"status": "saved", "verb": verb_name, "steps": len(normalized.get("steps", []))}

@router.post("/verb/status-workflow/migrate/{project}/{verb_name}")
def migrate_linear_status(project: str, verb_name: str, persist: bool = Query(False)):
    """
    Build a linear_status proposal from the current bucketed schema.
    If persist=true, it will be validated and saved as the verb's linear_status.
    """
    proj = _get_project_path(project)
    verbs, verb_def = _load_verb(proj, verb_name)

    log.debug("[migrate_linear_status] propose", {"verb": verb_name, "persist": persist})
    proposal = _propose_linear_status(verb_def)
    if not persist:
        log.debug("[migrate_linear_status] returning proposal only")
        return {"status": "proposed", "verb": verb_name, "linear_status": proposal}

    # validate proposal and persist
    normalized = _validate_linear_status_block(verb_def, proposal)
    new_def = deepcopy(verb_def)
    new_def["linear_status"] = normalized
    _save_verb(proj, verbs, verb_name, new_def)

    log.debug("[migrate_linear_status] saved")
    return {"status": "saved", "verb": verb_name, "steps": len(normalized.get("steps", []))}

@router.delete("/verb/status-workflow/{project}/{verb_name}")
def delete_linear_status(project: str, verb_name: str):
    """
    Remove the linear_status block from a verb (fallback to bucket behavior).
    """
    proj = _get_project_path(project)
    verbs, verb_def = _load_verb(proj, verb_name)

    log.debug("[delete_linear_status] begin", {"verb": verb_name})
    if "linear_status" not in verb_def:
        log.debug("[delete_linear_status] no-op; already missing")
        return {"status": "no-op", "verb": verb_name}

    new_def = deepcopy(verb_def)
    del new_def["linear_status"]
    _save_verb(proj, verbs, verb_name, new_def)
    log.debug("[delete_linear_status] removed")
    return {"status": "deleted", "verb": verb_name}
