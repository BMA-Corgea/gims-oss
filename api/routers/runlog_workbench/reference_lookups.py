# api/routers/runlog_workbench/reference_lookups.py
"""Reference options for overrides UI (Run noun primary-id aware) + verb schema."""

from pathlib import Path
from typing import Any, Dict, List

from fastapi import Request

from ._router import router
from ._shared import (
    AppError,
    resolve_path,
    get_noun_items,
    get_noun_schema,
    get_verb_schema,
    _group_pid_field,
    load_verb_group_log,
    log,
)

# -----------------------------------------------------------------------------
# Reference options for overrides UI (Run noun primary-id aware)
# -----------------------------------------------------------------------------

@router.get("/conjunction/reference_options/{project}/{noun_type}")
def get_reference_options(project: str, noun_type: str, request: Request):
    project_root = resolve_path(Path(), "project_root")
    project_path = project_root / project

    qp = request.query_params
    verb_group = qp.get("verb_group")
    verb_name  = qp.get("verb_name")
    statuses   = qp.getlist("status")

    if noun_type == "Run":
        if not verb_group:
            return {"options": []}
        pid_field = _group_pid_field(project_path, verb_group)
        runs = load_verb_group_log(project_path, verb_group)
        if verb_name:
            runs = [r for r in runs if r.get("test_type") == verb_name]
        if statuses:
            runs = [r for r in runs if r.get("status") in statuses]
        options = [{"label": str(r.get(pid_field)), "value": str(r.get(pid_field))}
                   for r in runs if r.get(pid_field) is not None]
        return {"options": options}

    try:
        items = get_noun_items(project_path, noun_type)
        noun_schema = get_noun_schema(project_path, noun_type)
    except FileNotFoundError:
        return {"options": []}

    pid_field = (noun_schema or {}).get("primary_id_field", "id")

    ignore_keys = {"verb_group", "verb_name", "status"}
    filters: Dict[str, List[str]] = {}
    for k, v in qp.multi_items():
        if k in ignore_keys:
            continue
        filters.setdefault(k, []).append(v)

    def passes_filters(rec: dict[str, Any]) -> bool:
        if not filters:
            return True
        for k, vals in filters.items():
            if str(rec.get(k)) not in {str(x) for x in vals}:
                return False
        return True

    options = [{"label": str(rec.get(pid_field)), "value": str(rec.get(pid_field))}
               for rec in items if pid_field in rec and passes_filters(rec)]
    return {"options": options}

@router.get("/schema/verb/{project}/{verb_name}")
def api_get_verb_schema(project: str, verb_name: str):
    try:
        project_root = resolve_path(Path(), "project_root")
        project_path = project_root / project
        schema = get_verb_schema(project_path, verb_name)
    except FileNotFoundError as e:
        raise AppError("VERB_SCHEMA_NOT_FOUND", str(e), status=404,
                       details={"project": project, "verb_name": verb_name})
    if not schema:
        raise AppError("VERB_NOT_FOUND", f"Verb {verb_name} not found", status=404,
                       details={"project": project, "verb_name": verb_name})
    log.debug("verb_schema", {"project": project, "verb_name": verb_name, "has_schema": bool(schema)})
    return schema
