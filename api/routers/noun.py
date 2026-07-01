# api/routers/noun.py
from __future__ import annotations

import json
import traceback
from pathlib import Path
from fastapi import APIRouter, HTTPException, Body
from typing import Set

from core.errors import AppError
from core.handlers.noun import NounType, register_noun_type  # RDS-aware core
from api.manifest.resolver import resolve_path
from api.i_o import (
    load_schema,
    get_noun_schema,
    read_text,
    write_text,
    io_list_projects,
)
from api.json_proxy import S3_ENABLED, _is_s3_path  # for diagnostics only

# Debug controls
from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()
router = APIRouter()
API_BASE = "http://localhost:8000"


# -----------------------------
# Adjective helpers (GUI-scope)
# -----------------------------
def _is_adjective_fielddef(field_def: dict) -> bool:
    if not isinstance(field_def, dict):
        return False
    if field_def.get("type") == "adjective":
        return True
    return "adjective_class" in field_def

def _load_adjective_schema(project_root: Path) -> list:
    path = resolve_path(project_root, "adjective_schema")
    try:
        payload = read_text(path, encoding="utf-8")
        data = json.loads(payload)
    except FileNotFoundError:
        return []
    except Exception as e:
        tb = traceback.format_exc()
        log.debug("Failed to read adjective_types.json", {"path": str(path), "error": repr(e), "traceback": tb})
        raise AppError("ADJECTIVE_SCHEMA_READ_FAILED", f"Failed to read adjective_types.json: {e}\n{tb}", status=500, details={"path": str(path)})

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        out = []
        for k, v in data.items():
            if isinstance(v, dict):
                v2 = v.copy()
                v2.setdefault("adjective", k)
                out.append(v2)
        return out
    return []

def _save_adjective_schema(project_root: Path, entries: list) -> None:
    path = resolve_path(project_root, "adjective_schema")
    try:
        write_text(path, json.dumps(entries, indent=2), encoding="utf-8")
    except Exception as e:
        tb = traceback.format_exc()
        log.debug("Failed to write adjective_types.json", {"path": str(path), "error": repr(e), "traceback": tb})
        raise AppError("ADJECTIVE_SCHEMA_WRITE_FAILED", f"Failed to write adjective_types.json: {e}\n{tb}", status=500, details={"path": str(path)})

def _cascade_rename_in_adjectives(entries: list, noun_name: str, old_field: str, new_field: str) -> bool:
    updated = False
    for entry in entries:
        try:
            applies = entry.get("applies_to") or entry.get("appliesTo") or []
            if entry.get("adjective") == old_field and noun_name in applies:
                entry["adjective"] = new_field
                updated = True
        except Exception:
            log.debug("Skipping malformed adjective entry during cascade rename",
                      {"noun": noun_name, "old_field": old_field, "new_field": new_field}, exc_info=True)
            continue
    return updated


# -----------------------------
# Small JSON helpers (S3-aware)
# -----------------------------
def _load_json(path: Path) -> dict | list:
    """
    Always S3-aware read. Do not call Path.read_text() or Path.exists().
    """
    log.debug("LOAD JSON", {"path": str(path), "s3_mode": S3_ENABLED, "redirect": _is_s3_path(str(path))})
    try:
        return json.loads(read_text(path, encoding="utf-8"))
    except FileNotFoundError:
        # Caller decides if empty is acceptable
        raise
    except Exception as e:
        tb = traceback.format_exc()
        log.debug("LOAD JSON FAILED", {"path": str(path), "error": repr(e), "traceback": tb})
        raise

def _save_json(path: Path, obj) -> None:
    """
    Always S3-aware write.
    """
    log.debug("SAVE JSON", {"path": str(path), "s3_mode": S3_ENABLED, "redirect": _is_s3_path(str(path))})
    try:
        write_text(path, json.dumps(obj, indent=2), encoding="utf-8")
    except Exception as e:
        tb = traceback.format_exc()
        log.debug("SAVE JSON FAILED", {"path": str(path), "error": repr(e), "traceback": tb})
        raise


# -----------------------------
# Routes
# -----------------------------
@router.get("/noun/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception:
        # Optional: return empty list on failure instead of 500
        log.warning("list_projects failed; returning empty list", exc_info=True)
        return []

@router.get("/noun/types/{project}")
def list_nouns(project: str):
    project_root = resolve_path(Path(), "project_root")
    try:
        data = load_schema(project_root / project, "noun")
        return list(data.keys())
    except HTTPException:
        return []
    except Exception as e:
        tb = traceback.format_exc()
        raise AppError("NOUN_LIST_FAILED", f"{e}\n{tb}", status=500, details={"project": project})

@router.get("/noun/describe/{project}/{noun}")
def describe_noun(project: str, noun: str):
    project_root = resolve_path(Path(), "project_root")
    try:
        schema = get_noun_schema(project_root / project, noun)
        if not schema:
            raise AppError("NOUN_NOT_FOUND", f"Noun '{noun}' not found", status=404, details={"project": project, "noun": noun})
    except (HTTPException, AppError) as e:
        raise e
    except Exception as e:
        tb = traceback.format_exc()
        raise AppError("NOUN_DESCRIBE_FAILED", f"{e}\n{tb}", status=500, details={"project": project, "noun": noun})

    nt = NounType(noun, schema, project_root / project)
    return nt.describe()

def list_date_formats():
    # Kept for compatibility; GUI uses this to populate a dropdown.
    return [
        "yyyy-mm-dd",
        "mm/dd/yyyy",
        "dd/mm/yyyy",
        "yyyy-mm",
        "yyyy",
    ]

@router.get("/noun/date_formats")
def get_date_formats():
    return list_date_formats()

@router.post("/noun/preview_id/{project}/{noun}")
def preview_autogenerated_id(
    project: str,
    noun: str,
    existing_ids: Set[str] = Body(default=set()),
):
    project_root = resolve_path(Path(), "project_root")
    try:
        schema = get_noun_schema(project_root / project, noun)
        if not schema:
            raise AppError("NOUN_NOT_FOUND", f"Noun '{noun}' not found", status=404, details={"project": project, "noun": noun})
    except (HTTPException, AppError) as e:
        raise e
    except Exception as e:
        tb = traceback.format_exc()
        raise AppError("NOUN_SCHEMA_LOAD_FAILED", f"{e}\n{tb}", status=500, details={"project": project, "noun": noun})

    nt = NounType(noun, schema, project_root / project)
    try:
        preview = nt.preview_autogenerated_id(existing_ids)
        return {"id_preview": preview}
    except Exception as e:
        tb = traceback.format_exc()
        raise AppError("NOUN_ID_PREVIEW_FAILED", f"{e}\n{tb}", status=400, details={"project": project, "noun": noun})


@router.post("/noun/edit/{project}/{noun}")
def edit_noun(
    project: str,
    noun: str,
    payload: dict = Body(...),
):
    project_root = resolve_path(Path(), "project_root")
    try:
        schema = get_noun_schema(project_root / project, noun)
        if not schema:
            raise AppError("NOUN_NOT_FOUND", f"Noun '{noun}' not found", status=404)
    except HTTPException:
        # Normalize to 404 for GUI
        raise AppError("NOUN_NOT_FOUND", f"Noun '{noun}' not found", status=404, details={"project": project, "noun": noun})
    except Exception as e:
        tb = traceback.format_exc()
        raise AppError("NOUN_SCHEMA_LOAD_FAILED", f"{e}\n{tb}", status=500, details={"project": project, "noun": noun})

    nt = NounType(noun, schema, project_root / project)
    action = payload.get("action")

    try:
        if action == "add":
            nt.add_field(
                field_name=payload["field_name"],
                field_type=payload["field_type"],
                required=payload.get("required", False),
                format=payload.get("format")
            )

        elif action == "edit":
            field_name = payload["field_name"]
            new_type   = payload.get("new_type")
            # Block type changes for adjective fields at GUI layer too
            fdef = schema.get("fields", {}).get(field_name, {})
            if _is_adjective_fielddef(fdef) and new_type and new_type != fdef.get("type"):
                raise AppError(
                    "ADJECTIVE_FIELD_TYPE_CHANGE_FORBIDDEN",
                    f"Cannot change type of adjective field '{field_name}'. Rename only.",
                    status=400,
                    details={"project": project, "noun": noun, "field": field_name},
                )
            nt.edit_field(
                field_name=field_name,
                new_type=new_type,
                required=payload.get("required"),
                format_override=payload.get("format_override")
            )

        elif action == "delete":
            _ = nt.delete_field(payload["field_name"])

        elif action == "rename":
            info = nt.rename_field(payload["old_name"], payload["new_name"])
            # If it was adjective, cascade into adjective_types.json
            if info.get("was_adjective"):
                adj_entries = _load_adjective_schema(project_root / project)
                updated = _cascade_rename_in_adjectives(
                    adj_entries, noun_name=noun,
                    old_field=info["old_field"], new_field=info["new_field"]
                )
                if updated:
                    _save_adjective_schema(project_root / project, adj_entries)

        elif action == "set_id":
            nt.configure_primary_id(
                field_name=payload["field_name"],
                autogenerate=payload.get("autogenerate", "no"),
                segments=payload.get("segments", [])
            )
        else:
            raise AppError("INVALID_ACTION", f"Invalid action '{action}'", status=400, details={"action": action})

        # Persist updated noun schema to noun_types.json (S3-aware)
        noun_path = resolve_path(project_root / project, "noun_schema")
        log.debug("Updating noun_types.json", {"noun_path": str(noun_path), "s3_redirect": _is_s3_path(str(noun_path))})
        try:
            data = _load_json(noun_path)
        except FileNotFoundError:
            data = {}
        data[noun] = nt.schema
        _save_json(noun_path, data)

        # NOTE (Phase 5): the convenience per-noun stub (nouns/<noun>/<noun>.json) is no
        # longer written — the nouns/ folder store is retired and nothing reads the stub.
        # noun_types.json (saved above) is the single schema source.

        return {"success": True, "message": "Edit applied successfully"}

    except (HTTPException, AppError):
        raise
    except Exception as e:
        tb = traceback.format_exc()
        log.debug("Edit noun failed", {"error": repr(e), "traceback": tb})
        raise AppError("EDIT_NOUN_FAILED", f"X Internal Server Error: {e}\n{tb}", status=500, details={"project": project, "noun": noun, "action": action})


@router.post("/noun/register/{project}")
def register_new_noun(
    project: str,
    payload: dict = Body(...)
):
    project_root = resolve_path(Path(), "project_root")
    if not isinstance(project, str):
        raise AppError("INVALID_PROJECT", "Project must be a string", status=400)
    project_root = project_root / project

    noun_name = payload.get("noun_name")
    schema    = payload.get("schema")
    if not noun_name or not isinstance(schema, dict):
        raise AppError(
            "INVALID_REGISTER_BODY",
            "Body must include 'noun_name' (string) and 'schema' (object)",
            status=400,
            details={"project": project},
        )

    # Load existing noun_types.json (S3-aware; no Path.exists())
    noun_types_path = resolve_path(project_root, "noun_schema")
    try:
        existing = _load_json(noun_types_path)
    except FileNotFoundError:
        existing = {}
    except Exception as e:
        tb = traceback.format_exc()
        log.debug("Failed to read noun_types.json", {"path": str(noun_types_path), "error": repr(e), "traceback": tb})
        raise AppError("NOUN_TYPES_READ_FAILED", f"Failed to read noun_types.json: {e}\n{tb}", status=500, details={"project": project, "path": str(noun_types_path)})

    # Core validation + dict mutation (also updates SQL meta + tables)
    try:
        updated = register_noun_type(existing, noun_name, schema, project_path=project_root)
    except ValueError as ve:
        raise AppError("NOUN_REGISTER_INVALID", str(ve), status=400, details={"project": project, "noun": noun_name})
    except Exception as e:
        tb = traceback.format_exc()
        raise AppError("NOUN_REGISTER_FAILED", f"{e}\n{tb}", status=500, details={"project": project, "noun": noun_name})

    # Persist updated noun_types.json (S3-aware)
    try:
        try:
            noun_types_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        _save_json(noun_types_path, updated)
    except Exception as e:
        tb = traceback.format_exc()
        log.debug("Failed to write noun_types.json", {"path": str(noun_types_path), "error": repr(e), "traceback": tb})
        raise AppError("NOUN_TYPES_WRITE_FAILED", f"Failed to write noun_types.json: {e}\n{tb}", status=500, details={"project": project, "path": str(noun_types_path)})

    # NOTE (Phase 5): no per-noun directory/stub is scaffolded — the nouns/ folder store is
    # retired. The noun type lives in noun_types.json (saved above) and its instances live in
    # the unified `instances` store; there is no per-noun folder to create.

    return {"success": True, "messages": [f"✅ Registered noun: {noun_name}"]}
