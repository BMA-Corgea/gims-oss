# api/gui/noun_gui.py
from __future__ import annotations

import json
import traceback
from pathlib import Path
from fastapi import APIRouter, HTTPException, Body
from typing import Set

from core.handlers.core_noun import NounType, register_noun_type  # RDS-aware core
from api.manifest.resolver import resolve_path
from api.i_o import (
    load_schema,
    get_noun_schema,
    read_text,
    write_text,
    open_file,   # S3-aware file open wrapper
    io_list_projects,
)
from api.json_proxy import S3_ENABLED, _is_s3_path  # for diagnostics only

# Debug controls
DEBUG_ENABLED = False
def debug(*args, **kwargs):
    if DEBUG_ENABLED:
        print("[noun_gui]", *args, **kwargs)

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
        debug("Failed to read adjective_types.json", {"path": str(path), "error": repr(e), "traceback": tb})
        raise HTTPException(status_code=500, detail=f"Failed to read adjective_types.json: {e}\n{tb}")

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
        debug("Failed to write adjective_types.json", {"path": str(path), "error": repr(e), "traceback": tb})
        raise HTTPException(status_code=500, detail=f"Failed to write adjective_types.json: {e}\n{tb}")

def _cascade_rename_in_adjectives(entries: list, noun_name: str, old_field: str, new_field: str) -> bool:
    updated = False
    for entry in entries:
        try:
            applies = entry.get("applies_to") or entry.get("appliesTo") or []
            if entry.get("adjective") == old_field and noun_name in applies:
                entry["adjective"] = new_field
                updated = True
        except Exception:
            continue
    return updated


# -----------------------------
# Small JSON helpers (S3-aware)
# -----------------------------
def _load_json(path: Path) -> dict | list:
    """
    Always S3-aware read. Do not call Path.read_text() or Path.exists().
    """
    debug("LOAD JSON", {"path": str(path), "s3_mode": S3_ENABLED, "redirect": _is_s3_path(str(path))})
    try:
        return json.loads(read_text(path, encoding="utf-8"))
    except FileNotFoundError:
        # Caller decides if empty is acceptable
        raise
    except Exception as e:
        tb = traceback.format_exc()
        debug("LOAD JSON FAILED", {"path": str(path), "error": repr(e), "traceback": tb})
        raise

def _save_json(path: Path, obj) -> None:
    """
    Always S3-aware write.
    """
    debug("SAVE JSON", {"path": str(path), "s3_mode": S3_ENABLED, "redirect": _is_s3_path(str(path))})
    try:
        write_text(path, json.dumps(obj, indent=2), encoding="utf-8")
    except Exception as e:
        tb = traceback.format_exc()
        debug("SAVE JSON FAILED", {"path": str(path), "error": repr(e), "traceback": tb})
        raise


# -----------------------------
# Routes
# -----------------------------
@router.get("/noun/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception as e:
        # Optional: return empty list on failure instead of 500
        print(f"[list_projects] list_projects failed: {e!r}")
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
        raise HTTPException(status_code=500, detail=f"{e}\n{tb}")

@router.get("/noun/describe/{project}/{noun}")
def describe_noun(project: str, noun: str):
    project_root = resolve_path(Path(), "project_root")
    try:
        schema = get_noun_schema(project_root / project, noun)
        if not schema:
            raise HTTPException(status_code=404, detail=f"Noun '{noun}' not found")
    except HTTPException as e:
        raise e
    except Exception as e:
        tb = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"{e}\n{tb}")

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
            raise HTTPException(status_code=404, detail=f"Noun '{noun}' not found")
    except HTTPException as e:
        raise e
    except Exception as e:
        tb = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"{e}\n{tb}")

    nt = NounType(noun, schema, project_root / project)
    try:
        preview = nt.preview_autogenerated_id(existing_ids)
        return {"id_preview": preview}
    except Exception as e:
        tb = traceback.format_exc()
        raise HTTPException(status_code=400, detail=f"{e}\n{tb}")


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
            raise HTTPException(status_code=404, detail=f"Noun '{noun}' not found")
    except HTTPException:
        # Normalize to 404 for GUI
        raise HTTPException(status_code=404, detail=f"Noun '{noun}' not found")
    except Exception as e:
        tb = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"{e}\n{tb}")

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
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot change type of adjective field '{field_name}'. Rename only."
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
            raise HTTPException(status_code=400, detail=f"Invalid action '{action}'")

        # Persist updated noun schema to noun_types.json (S3-aware)
        noun_path = resolve_path(project_root / project, "noun_schema")
        debug("Updating noun_types.json", {"noun_path": str(noun_path), "s3_redirect": _is_s3_path(str(noun_path))})
        try:
            data = _load_json(noun_path)
        except FileNotFoundError:
            data = {}
        data[noun] = nt.schema
        _save_json(noun_path, data)

        # Save per-noun schema file (nouns/<noun>/<noun>.json) for convenience
        noun_schema_path = resolve_path(project_root / project, "noun_items", noun_type=noun).parent / f"{noun}.json"
        try:
            noun_schema_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            # Harmless in S3 mode; ignore
            pass
        _save_json(noun_schema_path, nt.schema)

        return {"success": True, "message": "Edit applied successfully"}

    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        debug("Edit noun failed", {"error": repr(e), "traceback": tb})
        raise HTTPException(status_code=500, detail=f"X Internal Server Error: {e}\n{tb}")


@router.post("/noun/register/{project}")
def register_new_noun(
    project: str,
    payload: dict = Body(...)
):
    project_root = resolve_path(Path(), "project_root")
    if not isinstance(project, str):
        raise HTTPException(status_code=400, detail="Project must be a string")
    project_root = project_root / project

    noun_name = payload.get("noun_name")
    schema    = payload.get("schema")
    if not noun_name or not isinstance(schema, dict):
        raise HTTPException(
            status_code=400,
            detail="Body must include 'noun_name' (string) and 'schema' (object)"
        )

    # Load existing noun_types.json (S3-aware; no Path.exists())
    noun_types_path = resolve_path(project_root, "noun_schema")
    try:
        existing = _load_json(noun_types_path)
    except FileNotFoundError:
        existing = {}
    except Exception as e:
        tb = traceback.format_exc()
        debug("Failed to read noun_types.json", {"path": str(noun_types_path), "error": repr(e), "traceback": tb})
        raise HTTPException(status_code=500, detail=f"Failed to read noun_types.json: {e}\n{tb}")

    # Core validation + dict mutation (also updates SQL meta + tables)
    try:
        updated = register_noun_type(existing, noun_name, schema, project_path=project_root)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        tb = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"{e}\n{tb}")

    # Persist updated noun_types.json (S3-aware)
    try:
        try:
            noun_types_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        _save_json(noun_types_path, updated)
    except Exception as e:
        tb = traceback.format_exc()
        debug("Failed to write noun_types.json", {"path": str(noun_types_path), "error": repr(e), "traceback": tb})
        raise HTTPException(status_code=500, detail=f"Failed to write noun_types.json: {e}\n{tb}")

    # Scaffold per-noun directory & the schema file (no JSONL items)
    try:
        noun_items_dir = resolve_path(project_root, "noun_items", noun_type=noun_name).parent
        try:
            noun_items_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        _save_json(noun_items_dir / f"{noun_name}.json", schema)
    except Exception as e:
        tb = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"Failed to scaffold per-noun directory: {e}\n{tb}")

    return {"success": True, "messages": [f"✅ Registered noun: {noun_name}"]}
