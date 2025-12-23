# gui/adverb_gui.py
from fastapi import APIRouter, HTTPException, Body
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json

from core.handlers.adverbs.base_adverb import BaseAdverb
from core.handlers.adverbs.tag_adverb import TagAdverb
from core.handlers.adverbs.reference_adverb import ReferenceAdverb
from core.handlers.adverbs.reference_list_adverb import ReferenceListAdverb
from core.handlers.adverbs.picture_adverb import PictureAdverb
from core.handlers.adverbs.attribute_adverb import AttributeAdverb

from api.manifest.resolver import resolve_path
from api import i_o

router = APIRouter()

ADV_CLASS_MAP: dict[str, type[BaseAdverb]] = {
    "Tag":           TagAdverb,
    "Reference":     ReferenceAdverb,
    "ReferenceList": ReferenceListAdverb,
    "Picture":       PictureAdverb,
    "Attribute":     AttributeAdverb,
}

# -------------------------
# Debug block
# -------------------------
DEBUG_ENABLED = False  # Flip to False to silence debug logs


def debug(*args, **kwargs):
    if DEBUG_ENABLED:
        print(*args, **kwargs)


# ──────────────────────────────────────────────────────────────────────────────
# Helper: load + instantiate an adverb handler
# ──────────────────────────────────────────────────────────────────────────────
def load_adverb_handler(
    project_path: Path,
    verb_name: str,
    adverb_name: str
) -> BaseAdverb:
    adv_schema_list = i_o.load_schema(project_path, "adverb")
    entry = next(
        (
            e for e in adv_schema_list
            if e.get("adverb") == adverb_name and e.get("verb") == verb_name
        ),
        None,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Adverb not found")

    cls = ADV_CLASS_MAP.get(entry["adverb_class"], BaseAdverb)
    return cls(entry, verb_name=verb_name, project_name=project_path.name)


# ──────────────────────────────────────────────────────────────────────────────
# LIST + FULL CONFIG ROUTES
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/adverb/list/{project}/{verb}")
def list_adverbs(project: str, verb: str):
    proj_root = resolve_path(Path(), "project_root") / project
    verb_schema = i_o.get_verb_schema(proj_root, verb)
    if not verb_schema:
        raise HTTPException(status_code=404, detail=f"Verb '{verb}' not found")
    return verb_schema.get("adverb_schema", {})


@router.get("/adverb/configure/{project}/{verb}/{adverb}")
def get_adverb_config(project: str, verb: str, adverb: str):
    proj_root = resolve_path(Path(), "project_root") / project
    verb_schema = i_o.get_verb_schema(proj_root, verb)
    if not verb_schema:
        raise HTTPException(status_code=404, detail=f"Verb '{verb}' not found")

    adv = verb_schema.get("adverb_schema", {}).get(adverb)
    if not adv:
        raise HTTPException(status_code=404, detail="Adverb not found")
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
    try:
        return i_o.io_list_projects()
    except Exception as e:
        # Optional: return empty list on failure instead of 500
        print(f"[list_projects] list_projects failed: {e!r}")
        return []

@router.get("/adverb/list/{project}")
def list_verbs_for_project(project: str):
    """Return all verbs in a project."""
    proj_root = resolve_path(Path(), "project_root") / project
    try:
        verb_schemas = i_o.load_schema(proj_root, "verb")
        return verb_schemas
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project} not found")

@router.get("/adverb/nouns/{project}")
def list_nouns_for_project(project: str):
    """Return all noun types for a project."""
    proj_root = resolve_path(Path(), "project_root") / project
    try:
        noun_schemas = i_o.load_schema(proj_root, "noun")
        return noun_schemas
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Project {project} not found")

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
        raise HTTPException(status_code=404, detail="Adverb not found")

    # Ensure verb is correct in payload; write row back
    data = dict(data or {})
    data["verb"] = verb
    adv_entries[idx] = data
    i_o.save_schema(proj_root, "adverb", adv_entries)

    # --- 2) verb_types.json (keyed by adverb name) ---
    verb_defs = i_o.load_schema(proj_root, "verb")
    if verb not in verb_defs:
        raise HTTPException(status_code=404, detail=f"Verb '{verb}' not found")

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
        raise HTTPException(status_code=404, detail=f"Verb '{verb}' not found")

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
        raise HTTPException(status_code=404, detail=f"Verb '{verb}' not found")

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
    handler = load_adverb_handler(proj_root, verb, adverb)

    if isinstance(handler, ReferenceListAdverb):
        noun_items_map = {}
        for ref_noun in handler.get_reference_noun():
            try:
                noun_items_map[ref_noun] = i_o.get_noun_items(proj_root, ref_noun)
            except FileNotFoundError:
                noun_items_map[ref_noun] = []
        return handler.use_logic(noun_items_map=noun_items_map)

    if isinstance(handler, ReferenceAdverb):
        ref_noun = handler.get_reference_noun()
        try:
            items = i_o.get_noun_items(proj_root, ref_noun)
        except FileNotFoundError:
            items = []
        return handler.use_logic(noun_items=items)

    return handler.use_logic(project_path=proj_root) or {}
    