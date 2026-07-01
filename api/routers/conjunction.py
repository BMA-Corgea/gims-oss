# api/routers/conjunction.py

from fastapi import APIRouter, Body
from pathlib import Path
import json
from typing import Any, Dict, List

from core.handlers import conjunction as core_conjunction
from api.manifest.resolver import resolve_path
from api import i_o
from core.errors import AppError
from utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter()

# --------------------------
# Schema-level Conjunctions
# --------------------------

@router.get("/conjunction/list/{project}/{verb_name}")
def list_conjunctions(project: str, verb_name: str) -> List[Dict[str, Any]]:
    """Return all conjunctions (status_values) defined for a verb,
    normalized so each entry is a dict.
    """
    try:
        # Resolve the project path, then use i_o
        project_path = resolve_path(Path(), "project_root") / project

        verb_schema = i_o.get_verb_schema(project_path, verb_name)
        if not verb_schema:
            return []  # empty list is friendlier for UIs

        values = verb_schema.get("status_values", [])
        normalized: List[Dict[str, Any]] = []

        for v in values:
            if isinstance(v, str):
                normalized.append({"name": v, "status": v, "fields": []})
            elif isinstance(v, dict):
                normalized.append(v)
            else:
                normalized.append({"name": str(v), "status": str(v), "fields": []})

        return normalized
    except Exception:
        log.warning("list_conjunctions failed", {"project": project, "verb": verb_name}, exc_info=True)
        return []


@router.post("/conjunction/register/{project}/{verb_name}")
def register_conjunction(project: str, verb_name: str, data: Dict[str, Any] = Body(...)):
    """Register a new conjunction schema inside a verb."""
    project_path = resolve_path(Path(), "project_root") / project

    try:
        verb_types = i_o.load_schema(project_path, "verb")
    except FileNotFoundError:
        raise AppError("VERB_SCHEMA_NOT_FOUND", "Verb schema not found", status=404,
                       details={"project": project})

    verb_def = verb_types.get(verb_name)
    if not verb_def:
        raise AppError("VERB_NOT_FOUND", "Verb not found", status=404,
                       details={"project": project, "verb": verb_name})

    errors = core_conjunction.validate_conjunction_schema(data)
    if errors:
        raise AppError("CONJUNCTION_SCHEMA_INVALID", "Conjunction schema validation failed",
                       status=400, details={"errors": errors})

    if any(c.get("name") == data["name"] for c in verb_def.get("status_values", [])):
        raise AppError("CONJUNCTION_ALREADY_EXISTS", "Conjunction already exists", status=400,
                       details={"verb": verb_name, "conjunction": data.get("name")})

    verb_def.setdefault("status_values", []).append(data)
    verb_types[verb_name] = verb_def
    i_o.save_schema(project_path, "verb", verb_types)

    return {"status": "registered", "conjunction": data}


@router.post("/conjunction/update/{project}/{verb_name}/{name}")
def update_conjunction(project: str, verb_name: str, name: str, updates: Dict[str, Any] = Body(...)):
    """Update an existing conjunction schema in a verb."""
    project_path = resolve_path(Path(), "project_root") / project

    try:
        verb_types = i_o.load_schema(project_path, "verb")
    except FileNotFoundError:
        raise AppError("VERB_SCHEMA_NOT_FOUND", "Verb schema not found", status=404,
                       details={"project": project})

    verb_def = verb_types.get(verb_name)
    if not verb_def:
        raise AppError("VERB_NOT_FOUND", "Verb not found", status=404,
                       details={"project": project, "verb": verb_name})

    conj_list = verb_def.get("status_values", [])
    match = next((c for c in conj_list if c.get("name") == name), None)
    if not match:
        raise AppError("CONJUNCTION_NOT_FOUND", "Conjunction not found", status=404,
                       details={"verb": verb_name, "conjunction": name})

    updated = core_conjunction.update_conjunction(match, updates)
    errors = core_conjunction.validate_conjunction_schema(updated)
    if errors:
        raise AppError("CONJUNCTION_SCHEMA_INVALID", "Conjunction schema validation failed",
                       status=400, details={"errors": errors})

    new_list = [updated if c.get("name") == name else c for c in conj_list]
    verb_def["status_values"] = new_list
    verb_types[verb_name] = verb_def
    i_o.save_schema(project_path, "verb", verb_types)

    return {"status": "updated", "conjunction": updated}


@router.delete("/conjunction/delete/{project}/{verb_name}/{name}")
def delete_conjunction(project: str, verb_name: str, name: str):
    """Delete a conjunction schema from a verb."""
    project_path = resolve_path(Path(), "project_root") / project

    try:
        verb_types = i_o.load_schema(project_path, "verb")
    except FileNotFoundError:
        raise AppError("VERB_SCHEMA_NOT_FOUND", "Verb schema not found", status=404,
                       details={"project": project})

    verb_def = verb_types.get(verb_name)
    if not verb_def:
        raise AppError("VERB_NOT_FOUND", "Verb not found", status=404,
                       details={"project": project, "verb": verb_name})

    conj_list = verb_def.get("status_values", [])
    new_list = core_conjunction.delete_conjunction(conj_list, name)
    verb_def["status_values"] = new_list
    verb_types[verb_name] = verb_def
    i_o.save_schema(project_path, "verb", verb_types)

    return {"status": "deleted", "name": name}


@router.get("/conjunction/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return i_o.io_list_projects()
    except Exception:
        # Optional: return empty list on failure instead of 500
        log.warning("list_projects failed", exc_info=True)
        return []


@router.get("/conjunction/verbs/{project}")
def list_verbs(project: str):
    """Return a list of verb types for a project."""
    project_path = resolve_path(Path(), "project_root") / project
    try:
        data = i_o.load_schema(project_path, "verb")
        return list(data.keys())
    except FileNotFoundError:
        return {}


@router.get("/conjunction/nouns/{project}")
def list_nouns(project: str):
    """Return a list of noun types for a project."""
    project_path = resolve_path(Path(), "project_root") / project
    try:
        data = i_o.load_schema(project_path, "noun")
        return data
    except FileNotFoundError:
        return {}

# --------------------------
# Run-level Overrides
# --------------------------

@router.post("/conjunction/apply/{project}/{verb_group}/{run_id}")
def apply_conjunction(project: str, verb_group: str, run_id: str,
                      override: Dict[str, Any] = Body(...),
                      context: Dict[str, Any] = Body(...)):
    """
    Apply a conjunction (override) to a specific run.
    `override["type"]` must match one of the verb's status_values.
    """
    project_path = resolve_path(Path(), "project_root") / project

    # Resolve all files via resolver
    status_path = resolve_path(project_path, "status_file", verb_group=verb_group, run_id=run_id)
    override_path = resolve_path(project_path, "override_file")
    verb_schema_path = resolve_path(project_path, "verb_schema")

    test_type = override.get("type")
    if not test_type:
        raise AppError("OVERRIDE_TYPE_REQUIRED", "Override must include 'type'", status=400)

    # Load verb schema (S3/local aware)
    try:
        verb_types_raw = i_o.read_text(verb_schema_path)
        verb_types = json.loads(verb_types_raw or "{}")
    except FileNotFoundError:
        raise AppError("VERB_SCHEMA_NOT_FOUND", "Verb schema not found", status=404,
                       details={"project": project})
    except json.JSONDecodeError:
        raise AppError("VERB_SCHEMA_INVALID_JSON", "Verb schema is not valid JSON", status=500,
                       details={"project": project})

    # Find the matching status_values entry
    schema_entry = None
    verb_name = None
    for vname, vdef in (verb_types or {}).items():
        for opt in vdef.get("status_values", []):
            if opt.get("name") == test_type:
                schema_entry, verb_name = opt, vname
                break
        if schema_entry:
            break

    if not schema_entry:
        raise AppError("OVERRIDE_TYPE_NOT_FOUND", f"Override type '{test_type}' not found",
                       status=404, details={"type": test_type})

    # --------------------------
    # Build valid_refs map
    # --------------------------
    valid_refs: Dict[str, List[str]] = {}

    for field in schema_entry.get("fields", []):
        if isinstance(field, dict) and field.get("type") == "reference":
            ref_noun = field["reference_noun"]
            if ref_noun == "Run":
                runs = i_o.load_verb_group_log(project_path, verb_group)
                candidates = [r.get("run_ID") for r in runs if r.get("test_type") == verb_name]

                filters = field.get("filters", {})
                if "status" in filters and filters["status"]:
                    candidates = [
                        r.get("run_ID") for r in runs
                        if r.get("test_type") == verb_name
                        and r.get("status") in filters["status"]
                    ]
                valid_refs["Run"] = [c for c in candidates if c is not None]
            else:
                try:
                    items = i_o.get_noun_items(project_path, ref_noun)
                    noun_schema = i_o.get_noun_schema(project_path, ref_noun)
                    pid_field = noun_schema.get("primary_id_field", "id") if noun_schema else "id"

                    candidates = [it.get(pid_field) for it in items if pid_field in it]

                    filters = field.get("filters", {})
                    if filters:
                        candidates = [
                            it.get(pid_field) for it in items
                            if all(str(it.get(k)) == str(v) for k, v in filters.items())
                        ]

                    valid_refs[ref_noun] = [c for c in candidates if c is not None]
                except FileNotFoundError:
                    valid_refs[ref_noun] = []

    # --------------------------
    # Validate + normalize
    # --------------------------
    ok, norm, errors = core_conjunction.validate_and_normalize_override(
        schema_entry, override, context, valid_refs
    )
    if not ok:
        raise AppError("OVERRIDE_VALIDATION_FAILED", "Override validation failed", status=400,
                       details={"errors": errors})

    # --------------------------
    # Update Status.json (S3/local aware)
    # --------------------------
    if i_o.path_exists(status_path):
        try:
            current = i_o.read_text(status_path)
            status_data = json.loads(current or "{}")
        except json.JSONDecodeError:
            log.warning("apply_conjunction: corrupt Status.json, starting fresh",
                        {"project": project, "verb_group": verb_group, "run_id": run_id}, exc_info=True)
            status_data = {}
    else:
        status_data = {}

    status_data = core_conjunction.apply_conjunction(status_data, norm)
    i_o.write_text(status_path, json.dumps(status_data, indent=2))

    # --------------------------
    # Update override.json (global log) — S3/local aware
    # --------------------------
    global_log: List[Dict[str, Any]] = []
    if i_o.path_exists(override_path):
        try:
            gl_raw = i_o.read_text(override_path)
            global_log = json.loads(gl_raw or "[]")
        except json.JSONDecodeError:
            log.warning("apply_conjunction: corrupt override log, starting fresh",
                        {"project": project, "override_path": str(override_path)}, exc_info=True)
            global_log = []

    global_log.append(core_conjunction.to_global_entry(run_id, verb_name, norm))
    i_o.write_text(override_path, json.dumps(global_log, indent=2))

    return {"status": "applied", "override": norm}


@router.post("/conjunction/resolve/{project}/{verb_group}/{run_id}/{idx}")
def resolve_conjunction(project: str, verb_group: str, run_id: str, idx: int,
                        note: Dict[str, str] = Body(...)):
    """Mark a conjunction override as resolved in Status.json (and global log)."""
    project_path = resolve_path(Path(), "project_root") / project
    status_path = resolve_path(project_path, "status_file", verb_group=verb_group, run_id=run_id)
    override_path = resolve_path(project_path, "override_file")

    if not i_o.path_exists(status_path):
        raise AppError("STATUS_FILE_NOT_FOUND", "Status.json not found", status=404,
                       details={"project": project, "verb_group": verb_group, "run_id": run_id})

    try:
        status_raw = i_o.read_text(status_path)
        status_data = json.loads(status_raw or "{}")
    except json.JSONDecodeError:
        raise AppError("STATUS_FILE_INVALID_JSON", "Status.json is not valid JSON", status=500,
                       details={"project": project, "verb_group": verb_group, "run_id": run_id})

    note_text = note.get("note")
    if not note_text:
        raise AppError("RESOLUTION_NOTE_REQUIRED", "Resolution note required", status=400)

    overrides = status_data.get("overrides", [])
    if idx < 0 or idx >= len(overrides):
        raise AppError("INVALID_OVERRIDE_INDEX", "Invalid override index", status=400,
                       details={"index": idx, "override_count": len(overrides)})

    # Update Status.json
    new_status = core_conjunction.resolve_conjunction(status_data, idx, note_text)
    i_o.write_text(status_path, json.dumps(new_status, indent=2))

    # Fetch the resolved override type for global-log matching
    override_entry = overrides[idx]
    override_type = override_entry.get("type")

    # Update override.json (global log)
    global_log: List[Dict[str, Any]] = []
    if i_o.path_exists(override_path):
        try:
            gl_raw = i_o.read_text(override_path)
            global_log = json.loads(gl_raw or "[]")
        except json.JSONDecodeError:
            log.warning("resolve_conjunction: corrupt override log, starting fresh",
                        {"project": project, "override_path": str(override_path)}, exc_info=True)
            global_log = []

    updated = False
    for entry in global_log:
        if (
            entry.get("run") == run_id
            and entry.get("verb") == core_conjunction.resolve_verb_from_status(status_data)
            and entry.get("type") == override_type
        ):
            entry.setdefault("resolution", []).append({"note": note_text})
            updated = True
            break

    if updated:
        i_o.write_text(override_path, json.dumps(global_log, indent=2))

    return {"status": "resolved", "index": idx, "note": note_text}
