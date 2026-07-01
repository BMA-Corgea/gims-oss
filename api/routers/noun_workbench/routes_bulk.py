# api/routers/noun_workbench/routes_bulk.py
"""Bulk routes: preview a CSV/XLSX upload and commit previewed rows.

Handlers are registered in their ORIGINAL top-to-bottom order. Bodies are moved
verbatim from the original ``noun_workbench.py``.
"""

import tempfile
from pathlib import Path
from typing import Any, Dict, List

from fastapi import Body, File, HTTPException, UploadFile

# Repo-local imports (schema utilities only, no JSONL)
from api.i_o import get_noun_schema

# Phase-2 error contract
from core.errors import AppError

from ._router import log, router
from .paths import get_project_path
from .uploads import _parse_upload_to_rows, _preview_rows
from .validation import (
    _autogen_enforced_blank,
    _generate_primary,
    _list_ids_for_noun,
    validate_item_against_schema,
)


# Bulk preview/commit
@router.post("/{project}/{noun_type}/bulk_preview", summary="Validate CSV/XLSX for mass create/update/upsert")
async def bulk_preview(
    project: str,
    noun_type: str,
    mode: str = "create",
    storage_backend: str = "sql",  # now enforced to 'sql' only
    file: UploadFile = File(...)
):
    """
    mode:
      - create: primary must be blank if autogen; inserts only
      - update: primary required; updates only
      - upsert: update if primary exists; if autogen and primary blank → generate and insert

    storage_backend:
      - sql: only write to SQL database (enforced)
    """
    if storage_backend != "sql":
        raise AppError("STORAGE_BACKEND_INVALID", "storage_backend must be 'sql' (JSONL disabled for nouns).", status=400, details={"storage_backend": storage_backend})

    pp = get_project_path(project)
    schema = get_noun_schema(pp, noun_type)
    if not schema:
        raise AppError("NOUN_NOT_FOUND", f"Noun '{noun_type}' not found.", status=404, details={"noun_type": noun_type})

    suffix = Path(file.filename or "").suffix.lower() or ".csv"
    with tempfile.NamedTemporaryFile(delete=True, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp.flush()
        try:
            rows = _parse_upload_to_rows(Path(tmp.name))
        except HTTPException:
            # _parse_upload_to_rows raises HTTPException intentionally; preserve it (caught here).
            raise
        except Exception as e:
            raise AppError("FILE_PARSE_FAILED", f"Failed to parse file: {e}", status=400, details={"filename": file.filename})

    if not rows:
        return {"valid": [], "invalid": [], "warnings": ["No rows found."]}

    preview = _preview_rows(rows, noun_type, schema, project, pp, mode)
    log.debug(f"[bulk_preview] noun={noun_type} mode={mode} valid={len(preview['valid'])} invalid={len(preview['invalid'])}")
    return preview

@router.post("/{project}/{noun_type}/bulk_commit", summary="Commit previewed rows to DB (no JSONL)")
def bulk_commit(
    project: str,
    noun_type: str,
    mode: str = "create",
    storage_backend: str = "sql",  # enforced
    rows: Dict[str, Any] = Body(...)
):
    """
    Expects body: { "rows": [ { ...payload... }, ... ] }

    mode:
      - create: add new items
      - update: replace existing items by primary
      - upsert: replace if exists else add

    storage_backend:
      - sql: only write to SQL database (enforced)
    """
    if storage_backend != "sql":
        raise AppError("STORAGE_BACKEND_INVALID", "storage_backend must be 'sql' (JSONL disabled for nouns).", status=400, details={"storage_backend": storage_backend})

    pp = get_project_path(project)
    schema = get_noun_schema(pp, noun_type)
    if not schema:
        raise AppError("NOUN_NOT_FOUND", f"Noun '{noun_type}' not found.", status=404, details={"noun_type": noun_type})

    payloads = rows.get("rows") or []
    if not isinstance(payloads, list):
        raise AppError("INVALID_REQUEST_BODY", "Body must contain a 'rows' list.", status=400)

    primary = schema.get("primary_id_field") or "id"
    inserted = 0
    updated = 0
    skipped = 0
    errors: List[str] = []

    # One unified write loop into the instances store (no per-noun-table DDL / pg-vs-sqlite branch).
    from core.storage.factory import collection_for_noun, get_record_store
    store = get_record_store(pp)
    coll = collection_for_noun(noun_type)

    def _clean(d: Dict[str, Any]) -> Dict[str, Any]:
        return {k: (None if v == "" else v) for k, v in d.items()}

    for i, raw in enumerate(payloads, start=1):
        msg = _autogen_enforced_blank(schema, raw, mode)
        errs = [msg] if msg else []
        if mode in ("update", "upsert") and not raw.get(primary):
            if not (mode == "upsert" and schema.get("autogenerate_id")):
                errs.append(f"Primary field '{primary}' is required for {mode} mode.")
        errs += validate_item_against_schema(raw, noun_type, schema, pp, project=project)
        if errs:
            skipped += 1
            errors.append(f"Row {i}: " + "; ".join(errs))
            continue

        pid = raw.get(primary)
        existing = store.get_record(coll, primary, pid) if pid else None

        if mode == "update":
            if existing is None:
                skipped += 1
                errors.append(f"Row {i}: '{pid}' not found for update")
                continue
            store.put_record(coll, primary, {**existing, **_clean(raw), primary: pid})
            updated += 1
        elif mode == "upsert" and existing is not None:
            store.put_record(coll, primary, {**existing, **_clean(raw), primary: pid})
            updated += 1
        else:  # create, or upsert of a new / auto-id row
            if schema.get("autogenerate_id") and not pid:
                try:
                    existing_ids = _list_ids_for_noun(pp, project, noun_type, primary)
                except Exception:
                    log.warning("[bulk_commit] failed to list existing IDs for autogen", project, noun_type, exc_info=True)
                    existing_ids = []
                raw = {**raw, primary: _generate_primary(schema, noun_type, pp, existing_ids)}
                pid = raw.get(primary)
            if not pid:
                skipped += 1
                errors.append(f"Row {i}: missing primary id")
                continue
            if store.get_record(coll, primary, pid) is not None:
                skipped += 1
                errors.append(f"Row {i}: duplicate '{pid}'")
                continue
            store.put_record(coll, primary, _clean(raw))
            inserted += 1

    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "storage_backend": "sql"
    }
