# api/routers/adverb.py
from fastapi import APIRouter, Body
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.errors import AppError
from core.words.handlers.base import WordHandler

from api.manifest.resolver import resolve_path
from api import i_o
from api.routers._descriptor_crud import (
    load_descriptor_handler,
    run_descriptor_logic,
    list_projects as _list_projects,
)

router = APIRouter()

# class key -> unified Descriptor behavior name. Verb-context subset of the one
# canonical core.words.handlers.DESCRIPTOR_CLASSES (the legacy twin adverb tree
# collapsed into Descriptor); keys preserved verbatim so /adverb/classes + the
# dispatch-cohesion guard are unchanged.
ADV_CLASS_MAP: dict[str, str] = {
    "Tag":           "Tag",
    "Reference":     "Reference",
    "ReferenceList": "ReferenceList",
    "Picture":       "Picture",
    "Attribute":     "Attribute",
}

# -------------------------
# Debug block
# -------------------------
from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()


# ──────────────────────────────────────────────────────────────────────────────
# Helper: load + instantiate an adverb handler
# ──────────────────────────────────────────────────────────────────────────────
def load_adverb_handler(
    project_path: Path,
    verb_name: str,
    adverb_name: str
) -> WordHandler:
    # Thin alias over the shared descriptor loader (Phase 3c). Adverbs are scoped by a single
    # `verb`; an unknown adverb_class falls back to a bare WordHandler (legacy BaseAdverb).
    return load_descriptor_handler(
        project_path,
        schema_kind="adverb",
        scope_value=verb_name,
        attaches_to="verb",
        match=lambda e: e.get("adverb") == adverb_name and e.get("verb") == verb_name,
        behavior_of=lambda e: ADV_CLASS_MAP.get(e["adverb_class"]),
        not_found_code="ADVERB_NOT_FOUND",
        not_found_msg="Adverb not found",
        not_found_details={"project": project_path.name, "verb": verb_name, "adverb": adverb_name},
    )


# ──────────────────────────────────────────────────────────────────────────────
# LIST + FULL CONFIG ROUTES
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/adverb/list/{project}/{verb}")
def list_adverbs(project: str, verb: str):
    proj_root = resolve_path(Path(), "project_root") / project
    verb_schema = i_o.get_verb_schema(proj_root, verb)
    if not verb_schema:
        raise AppError(
            "VERB_NOT_FOUND",
            f"Verb '{verb}' not found",
            status=404,
            details={"project": project, "verb": verb},
        )
    return verb_schema.get("adverb_schema", {})


@router.get("/adverb/configure/{project}/{verb}/{adverb}")
def get_adverb_config(project: str, verb: str, adverb: str):
    proj_root = resolve_path(Path(), "project_root") / project
    verb_schema = i_o.get_verb_schema(proj_root, verb)
    if not verb_schema:
        raise AppError(
            "VERB_NOT_FOUND",
            f"Verb '{verb}' not found",
            status=404,
            details={"project": project, "verb": verb},
        )

    adv = verb_schema.get("adverb_schema", {}).get(adverb)
    if not adv:
        raise AppError(
            "ADVERB_NOT_FOUND",
            "Adverb not found",
            status=404,
            details={"project": project, "verb": verb, "adverb": adverb},
        )
    return adv


@router.get("/adverb/classes")
def list_adverb_classes():
    return list(ADV_CLASS_MAP.keys())


@router.get("/adverb/options/{project}/{verb}/{adverb}")
def get_adverb_options(project: str, verb: str, adverb: str):
    proj_root = resolve_path(Path(), "project_root") / project
    handler = load_adverb_handler(proj_root, verb, adverb)
    return handler.get_configurable_options()

@router.get("/adverb/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    return _list_projects()

@router.get("/adverb/list/{project}")
def list_verbs_for_project(project: str):
    """Return all verbs in a project."""
    proj_root = resolve_path(Path(), "project_root") / project
    try:
        verb_schemas = i_o.load_schema(proj_root, "verb")
        return verb_schemas
    except FileNotFoundError:
        raise AppError(
            "PROJECT_NOT_FOUND",
            f"Project {project} not found",
            status=404,
            details={"project": project},
        )

@router.get("/adverb/nouns/{project}")
def list_nouns_for_project(project: str):
    """Return all noun types for a project."""
    proj_root = resolve_path(Path(), "project_root") / project
    try:
        noun_schemas = i_o.load_schema(proj_root, "noun")
        return noun_schemas
    except FileNotFoundError:
        raise AppError(
            "PROJECT_NOT_FOUND",
            f"Project {project} not found",
            status=404,
            details={"project": project},
        )

# ──────────────────────────────────────────────────────────────────────────────
# UPDATE, PROMOTE, DEMOTE
# ──────────────────────────────────────────────────────────────────────────────
@router.post("/adverb/update/{project}/{verb}/{adverb}")
def update_adverb(project: str, verb: str, adverb: str, data: dict = Body(...)):
    """
    Update an adverb definition. If the adverb name changes, rename the key in
    verb_types.json. Be idempotent: if the new key already exists, just overwrite it.
    """
    proj_root = resolve_path(Path(), "project_root") / project
    old_name = adverb
    new_name = (data or {}).get("adverb", old_name)

    # --- 1) adverb_types.json (list of rows) ---
    adv_entries = i_o.load_schema(proj_root, "adverb")

    # Find existing row by old name+verb, or by new name+verb (in case it was already renamed)
    idx = next((i for i, e in enumerate(adv_entries)
                if e.get("adverb") == old_name and e.get("verb") == verb), None)
    if idx is None:
        idx = next((i for i, e in enumerate(adv_entries)
                    if e.get("adverb") == new_name and e.get("verb") == verb), None)

    if idx is None:
        raise AppError(
            "ADVERB_NOT_FOUND",
            "Adverb not found",
            status=404,
            details={"project": project, "verb": verb, "adverb": old_name},
        )

    # Ensure verb is correct in payload; write row back
    data = dict(data or {})
    data["verb"] = verb
    adv_entries[idx] = data
    i_o.save_schema(proj_root, "adverb", adv_entries)

    # --- 2) verb_types.json (keyed by adverb name) ---
    verb_defs = i_o.load_schema(proj_root, "verb")
    if verb not in verb_defs:
        raise AppError(
            "VERB_NOT_FOUND",
            f"Verb '{verb}' not found",
            status=404,
            details={"project": project, "verb": verb},
        )

    adv_schema = verb_defs[verb].setdefault("adverb_schema", {})

    # If renamed: drop old key if present, then set/overwrite new key
    if new_name != old_name:
        adv_schema.pop(old_name, None)
        adv_schema[new_name] = data  # overwrite if exists
        renamed = True
    else:
        adv_schema[old_name] = data
        renamed = False

    i_o.save_schema(proj_root, "verb", verb_defs)

    return {
        "status": "updated",
        "renamed": renamed,
        "old_adverb": old_name,
        "new_adverb": new_name,
    }


@router.post("/adverb/promote/{project}/{verb}")
def promote_adverb(project: str, verb: str, data: dict = Body(...)):
    proj_root = resolve_path(Path(), "project_root") / project

    # 1) update adverb_types.json
    adv_entries = i_o.load_schema(proj_root, "adverb")
    adv_entries.append(data)
    i_o.save_schema(proj_root, "adverb", adv_entries)

    # 2) update verb_types.json
    verb_defs = i_o.load_schema(proj_root, "verb")
    if verb not in verb_defs:
        raise AppError(
            "VERB_NOT_FOUND",
            f"Verb '{verb}' not found",
            status=404,
            details={"project": project, "verb": verb},
        )

    adv_schema = verb_defs[verb].setdefault("adverb_schema", {})
    adv_schema[data["adverb"]] = data
    i_o.save_schema(proj_root, "verb", verb_defs)

    return {"status": "promoted"}


@router.post("/adverb/demote/{project}/{verb}/{adverb}")
def demote_adverb(project: str, verb: str, adverb: str):
    proj_root = resolve_path(Path(), "project_root") / project

    # 1) remove from adverb_types.json
    adv_entries = i_o.load_schema(proj_root, "adverb")
    adv_entries = [e for e in adv_entries if not (e.get("adverb") == adverb and e.get("verb") == verb)]
    i_o.save_schema(proj_root, "adverb", adv_entries)

    # 2) remove from verb_types.json
    verb_defs = i_o.load_schema(proj_root, "verb")
    if verb not in verb_defs:
        raise AppError(
            "VERB_NOT_FOUND",
            f"Verb '{verb}' not found",
            status=404,
            details={"project": project, "verb": verb},
        )

    adv_schema = verb_defs[verb].get("adverb_schema", {})
    if adverb in adv_schema:
        adv_schema.pop(adverb)

    i_o.save_schema(proj_root, "verb", verb_defs)

    return {"status": "demoted"}


# ──────────────────────────────────────────────────────────────────────────────
# LOGIC ROUTES
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/adverb/logic/{project}/{verb}/{adverb}")
def run_logic(project: str, verb: str, adverb: str):
    proj_root = resolve_path(Path(), "project_root") / project
    return run_descriptor_logic(proj_root, load_adverb_handler(proj_root, verb, adverb))
    