# api/routers/verb/routes_verb.py
#
# Verb CRUD endpoints + valid-noun-refs. Handlers moved VERBATIM from
# api/routers/verb.py (no logic changes). Registered FIRST so the literal
# "/verb/projects" route precedes the "/verb/{project}" param route, exactly
# as in the original file.

from fastapi import Body
from copy import deepcopy

from api.i_o import (
    load_schema,
    save_schema,
    io_list_projects,
)
from core.handlers.verb import (
    create_new_verb,
    update_description,
    update_status_values,
    update_data_entry_schema,
    update_adverb_schema,
    assign_verb_group,
    filter_valid_noun_type_refs,
)
from core.errors import AppError

from ._router import router
from ._log import log
from ._helpers import _get_project_path, _save_verb
from ._db import _ensure_verb_table
from .group_migration import _ensure_group_scaffold, _migrate_group_sql_and_dumps
from .linear_status import _validate_linear_status_block


# ─────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────
@router.get("/verb/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception:
        # Optional: return empty list on failure instead of 500
        log.warning("[list_projects] io_list_projects failed", exc_info=True)
        return []

@router.get("/verb/{project}")
def list_verbs(project: str):
    """Return all verb definitions in the project."""
    proj = _get_project_path(project)
    verbs = load_schema(proj, "verb")
    log.debug("[list_verbs]", {"project": project, "count": len(verbs)})
    return verbs

@router.get("/verb/{project}/{verb_name}")
def get_verb(project: str, verb_name: str):
    """Return a single verb definition."""
    proj = _get_project_path(project)
    verbs = load_schema(proj, "verb")
    if verb_name not in verbs:
        log.debug("[get_verb] 404", {"verb": verb_name})
        raise AppError(
            "VERB_NOT_FOUND",
            f"Verb {verb_name} not found",
            status=404,
            details={"verb": verb_name},
        )
    log.debug("[get_verb] ok", {"verb": verb_name})
    return verbs[verb_name]

@router.post("/verb/{project}/{verb_name}")
def create_verb(project: str, verb_name: str, data: dict = Body(...)):
    """
    Create a new verb entry, ensure:
      • unified SQL table exists in objects_db
      • group scaffold (config & data_dumps) exists (S3-aware)
    """
    proj = _get_project_path(project)
    verbs = load_schema(proj, "verb")
    if verb_name in verbs:
        log.debug("[create_verb] already exists", {"verb": verb_name})
        raise AppError(
            "VERB_ALREADY_EXISTS",
            f"Verb {verb_name} already exists",
            status=400,
            details={"verb": verb_name},
        )

    # Build base definition
    new_def = create_new_verb(verb_name)
    log.debug("[create_verb] base", new_def)

    if "description" in data:
        new_def = update_description(new_def, data["description"])
    if "status_values" in data:
        new_def = update_status_values(new_def, data["status_values"])
    if "data_entry_schema" in data:
        new_def = update_data_entry_schema(new_def, data["data_entry_schema"])
    if "adverb_schema" in data:
        new_def = update_adverb_schema(new_def, data["adverb_schema"])
    if "verb_group" in data:
        new_def = assign_verb_group(new_def, data["verb_group"])

    # allow linear_status on create
    if "linear_status" in data:
        log.debug("[create_verb] validating linear_status")
        ls_norm = _validate_linear_status_block(new_def, data["linear_status"])
        new_def["linear_status"] = ls_norm
        log.debug("[create_verb] linear_status attached", {"steps": len(ls_norm.get("steps", []))})

    # Safeguard: noun_type_ref must be present
    noun_ref = (
        new_def.get("data_entry_schema", {})
        .get("set_up_inputs", {})
        .get("noun_type_ref")
    )
    if not noun_ref:
        log.debug("[create_verb] missing noun_type_ref")
        raise AppError(
            "NOUN_TYPE_REF_REQUIRED",
            "[X] noun_type_ref is required when creating a verb",
            status=400,
            details={"verb": verb_name},
        )

    # Save into verb_types.json
    verbs[verb_name] = new_def
    save_schema(proj, "verb", verbs)
    log.debug("[create_verb] saved schema")

    # Ensure SQL table for this project
    _ensure_verb_table(proj)

    # Ensure group scaffold for UI/dumps (S3 aware)
    group_name = new_def["verb_group"]
    _ensure_group_scaffold(proj, group_name)

    return {"status": "created", "verb": verb_name}

@router.put("/verb/{project}/{verb_name}")
def update_verb(project: str, verb_name: str, data: dict = Body(...)):
    """
    Update an existing verb entry.
    If verb_group changes, migrate existing SQL rows by updating their verb_group
    and move any run_id-named data_dumps from old_group → new_group.
    """
    proj = _get_project_path(project)
    verbs = load_schema(proj, "verb")
    if verb_name not in verbs:
        log.debug("[update_verb] 404", {"verb": verb_name})
        raise AppError(
            "VERB_NOT_FOUND",
            f"Verb {verb_name} not found",
            status=404,
            details={"verb": verb_name},
        )

    verb_def_original = deepcopy(verbs[verb_name])
    old_group = verb_def_original.get("verb_group")
    log.debug("[update_verb] start", {"verb": verb_name, "old_group": old_group})

    verb_def = deepcopy(verb_def_original)

    if "description" in data:
        verb_def = update_description(verb_def, data["description"])
        log.debug("[update_verb] description set")

    if "status_values" in data:
        verb_def = update_status_values(verb_def, data["status_values"])
        log.debug("[update_verb] status_values set")

    if "data_entry_schema" in data:
        verb_def = update_data_entry_schema(verb_def, data["data_entry_schema"])
        log.debug("[update_verb] data_entry_schema set")

    if "adverb_schema" in data:
        verb_def = update_adverb_schema(verb_def, data["adverb_schema"])
        log.debug("[update_verb] adverb_schema set")

    new_group = old_group
    group_changed = False
    if "verb_group" in data:
        verb_def = assign_verb_group(verb_def, data["verb_group"])
        new_group = verb_def.get("verb_group")
        group_changed = (new_group != old_group)
        log.debug("[update_verb] verb_group set", {"new_group": new_group, "changed": group_changed})

    # linear_status full replacement (validate against the *updated* verb_def)
    if "linear_status" in data:
        log.debug("[update_verb] validating linear_status")
        ls_norm = _validate_linear_status_block(verb_def, data["linear_status"])
        verb_def["linear_status"] = ls_norm
        log.debug("[update_verb] linear_status attached", {"steps": len(ls_norm.get("steps", []))})

    # Persist schema first (so future reads reflect the change)
    _save_verb(proj, verbs, verb_name, verb_def)

    # Ensure SQL table exists (no-op if present)
    _ensure_verb_table(proj)

    migration_summary = None
    if group_changed and old_group and new_group:
        try:
            migration_summary = _migrate_group_sql_and_dumps(proj, verb_name, old_group, new_group)
        except Exception as e:
            log.debug("[update_verb] SQL+dump migration failed", {"error": str(e)})
            raise AppError(
                "VERB_GROUP_MIGRATION_FAILED",
                f"Verb group changed, but updating SQL rows or moving data dumps failed: {e}",
                status=500,
                details={"verb": verb_name, "from": old_group, "to": new_group},
            )

    out = {"status": "updated", "verb": verb_name}
    if migration_summary is not None:
        out["migration"] = {
            "from": old_group,
            "to": new_group,
            **migration_summary
        }
    return out

@router.delete("/verb/{project}/{verb_name}")
def delete_verb(project: str, verb_name: str):
    """Delete a verb definition (does not drop SQL table)."""
    proj = _get_project_path(project)
    verbs = load_schema(proj, "verb")
    if verb_name not in verbs:
        log.debug("[delete_verb] 404", {"verb": verb_name})
        raise AppError(
            "VERB_NOT_FOUND",
            f"Verb {verb_name} not found",
            status=404,
            details={"verb": verb_name},
        )
    del verbs[verb_name]
    save_schema(proj, "verb", verbs)
    log.debug("[delete_verb] deleted", {"verb": verb_name})
    return {"status": "deleted", "verb": verb_name}

@router.get("/noun/valid-refs/{project}")
def list_valid_noun_refs(project: str):
    """
    Return noun types that can be used as noun_type_ref in verbs.
    Only includes noun types that have a Reference or ReferenceList adjective.
    """
    proj = _get_project_path(project)

    try:
        noun_schema = load_schema(proj, "noun")
    except FileNotFoundError:
        log.debug("[list_valid_noun_refs] noun_types.json not found")
        raise AppError(
            "NOUN_SCHEMA_NOT_FOUND",
            "noun_types.json not found",
            status=404,
            details={"project": project},
        )

    valid_refs = filter_valid_noun_type_refs(noun_schema)
    log.debug("[list_valid_noun_refs]", {"count": len(valid_refs)})
    return {"valid_noun_types": valid_refs}
