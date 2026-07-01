# api/routers/noun_workbench/routes_write.py
"""Single-instance routes: validate, load, create, update.

These four handlers operate on ONE instance each. They are kept together, and in
their ORIGINAL top-to-bottom order (validate -> load -> create -> update), so the
``@router`` registration order is byte-identical to the pre-split module — the
original file interleaves the GET ``load`` between the POST ``validate`` and the
POST ``create``, and FastAPI route order is load-bearing for path shadowing.

Bodies are moved verbatim from the original ``noun_workbench.py``.
"""

from typing import Any, Dict, List

from fastapi import Body

# Repo-local imports (schema utilities only, no JSONL)
from api.i_o import get_noun_schema

# Phase-2 error contract
from core.errors import AppError

from ._router import log, router
from .paths import get_project_path
from .validation import (
    _autogen_enforced_blank,
    _generate_primary,
    _list_ids_for_noun,
    validate_item_against_schema,
)


@router.post("/{project}/{noun_type}/validate", summary="Validate a single item payload")
def validate_single(project: str, noun_type: str, payload: Dict[str, Any] = Body(...)):
    pp = get_project_path(project)
    schema = get_noun_schema(pp, noun_type)
    if not schema:
        raise AppError("NOUN_NOT_FOUND", f"Noun '{noun_type}' not found.", status=404, details={"noun_type": noun_type})
    errs = validate_item_against_schema(payload, noun_type, schema, pp, project=project)
    ok = len(errs) == 0
    log.debug("[validate][single]", "ok" if ok else errs)
    return {"ok": ok, "errors": errs}

@router.get("/{project}/{noun_type}/instance/{instance_id}", summary="Load existing instance by primary ID")
def load_instance(project: str, noun_type: str, instance_id: str):
    pp = get_project_path(project)
    schema = get_noun_schema(pp, noun_type)
    if not schema:
        raise AppError("NOUN_NOT_FOUND", f"Noun '{noun_type}' not found.", status=404, details={"noun_type": noun_type})
    primary = schema.get("primary_id_field") or "id"

    from core.storage.factory import collection_for_noun, get_record_store
    row = get_record_store(pp).get_record(collection_for_noun(noun_type), primary, instance_id)
    return row or {}

# NOTE (Phase 5): the per-noun-table row helpers (_insert_row_sqlite / _update_row_sqlite /
# _insert_row_pg / _update_row_pg / _exists_by_primary_pg) were removed. create_single /
# update_single / bulk_commit below write through the unified `instances` store
# (get_record_store().put_record), so there is no per-noun table to insert/update into.

# Create

@router.post("/{project}/{noun_type}/create", summary="Create single instance")
def create_single(project: str, noun_type: str, payload: Dict[str, Any] = Body(...)):
    pp = get_project_path(project)
    schema = get_noun_schema(pp, noun_type)
    if not schema:
        raise AppError("NOUN_NOT_FOUND", f"Noun '{noun_type}' not found.", status=404, details={"noun_type": noun_type})
    primary = schema.get("primary_id_field") or "id"

    # ---- validate
    errs: List[str] = []
    msg = _autogen_enforced_blank(schema, payload, mode="create")
    if msg:
        errs.append(msg)
    errs += validate_item_against_schema(payload, noun_type, schema, pp, project=project)
    if errs:
        return {"ok": False, "errors": errs}

    # ---- autogen primary if needed
    if schema.get("autogenerate_id") and not payload.get(primary):
        try:
            existing = _list_ids_for_noun(pp, project, noun_type, primary)
        except Exception:
            log.warning("[create] failed to list existing IDs for autogen", project, noun_type, exc_info=True)
            existing = []
        payload = {**payload, primary: _generate_primary(schema, noun_type, pp, existing)}

    # Write to the unified instances store (SQL-only). Reject a duplicate primary id.
    from core.storage.factory import collection_for_noun, get_record_store
    store = get_record_store(pp)
    coll = collection_for_noun(noun_type)
    if store.get_record(coll, primary, payload.get(primary)) is not None:
        raise AppError("DUPLICATE_PRIMARY_ID", f"Duplicate primary ID: {payload.get(primary)}", status=409,
                       details={"noun_type": noun_type, "primary": payload.get(primary)})
    store.put_record(coll, primary, {k: (None if v == "" else v) for k, v in payload.items()})

    return {"ok": True, "id": payload.get(primary)}

# Update

@router.post("/{project}/{noun_type}/update/{instance_id}", summary="Update single instance by primary ID")
def update_single(project: str, noun_type: str, instance_id: str, payload: Dict[str, Any] = Body(...)):
    pp = get_project_path(project)
    schema = get_noun_schema(pp, noun_type)
    if not schema:
        raise AppError("NOUN_NOT_FOUND", f"Noun '{noun_type}' not found.", status=404, details={"noun_type": noun_type})
    primary = schema.get("primary_id_field") or "id"

    # ---- validate payload fields (primary itself comes via path)
    errs = validate_item_against_schema(payload, noun_type, schema, pp, project=project)
    if errs:
        return {"ok": False, "errors": errs}

    # Update in the unified instances store; preserve fields not in the payload (e.g. _runID/archived).
    from core.storage.factory import collection_for_noun, get_record_store
    store = get_record_store(pp)
    coll = collection_for_noun(noun_type)
    existing = store.get_record(coll, primary, instance_id)
    if existing is None:
        raise AppError("INSTANCE_NOT_FOUND", f"Instance '{instance_id}' not found.", status=404,
                       details={"noun_type": noun_type, "instance": instance_id})
    merged = {**existing, **{k: (None if v == "" else v) for k, v in payload.items()}, primary: instance_id}
    store.put_record(coll, primary, merged)

    return {"ok": True, "id": instance_id}
