# gui/adjective_gui.py  ─── S3-aware (uses i_o.load_schema/save_schema/save_json everywhere)
from fastapi import APIRouter, HTTPException, Body
import sys
from pathlib import Path

# Ensure repo root on sys.path (unchanged)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from typing import List, Dict, Any

from core.handlers.adjectives.base import BaseAdjective
from core.handlers.adjectives.action_requirement import ActionRequirementAdjective
from core.handlers.adjectives.tag          import TagAdjective
from core.handlers.adjectives.reference    import ReferenceAdjective
from core.handlers.adjectives.reference_list import ReferenceListAdjective
from core.handlers.adjectives.picture      import PictureAdjective

from api.manifest.resolver import resolve_path
from api import i_o

router = APIRouter()

ADJ_CLASS_MAP: dict[str, type[BaseAdjective]] = {
    "ActionRequirement": ActionRequirementAdjective,
    "Tag":               TagAdjective,
    "Reference":         ReferenceAdjective,
    "ReferenceList":     ReferenceListAdjective,
    "Picture":           PictureAdjective
}

# ──────────────────────────────────────────────────────────────────────────────
# Helper: load + instantiate an adjective handler WITHOUT utils.disambiguation
# ──────────────────────────────────────────────────────────────────────────────
def load_adjective_handler(
    project_path: Path,
    noun_type: str,
    adjective_name: str
) -> BaseAdjective:
    """
    Pure resolver/i_o version of utils.disambiguation.load_adjective_handler().
    S3-aware: i_o.load_schema is used for adjective/verb schemas.
    """
    adj_schema_list = i_o.load_schema(project_path, "adjective")  # list
    entry = next(
        (
            e for e in adj_schema_list
            if e.get("adjective") == adjective_name
            and noun_type in e.get("applies_to", [])
        ),
        None
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Adjective not found")

    verb_types = i_o.load_schema(project_path, "verb")  # dict; required by ActionRequirement
    cls = ADJ_CLASS_MAP.get(entry.get("adjective_class", ""), BaseAdjective)
    return cls(entry, noun_type=noun_type, verb_types=verb_types, project_name=project_path.name)


# ──────────────────────────────────────────────────────────────────────────────
# LIST + FULL CONFIG ROUTES (S3-aware via i_o.load_schema / save_schema)
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/adjective/list/{project}/{noun}")
def list_adjectives(project: str, noun: str):
    project_path = resolve_path(Path(), "project_root") / project
    entries: List[Dict[str, Any]] = i_o.load_schema(project_path, "adjective")
    return [e for e in entries if noun in e.get("applies_to", [])]


@router.get("/adjective/configure/{project}/{noun}/{adjective}")
def get_adjective_config(project: str, noun: str, adjective: str):
    project_path = resolve_path(Path(), "project_root") / project
    entries: List[Dict[str, Any]] = i_o.load_schema(project_path, "adjective")
    match = next(
        (e for e in entries if e.get("adjective") == adjective and noun in e.get("applies_to", [])),
        None
    )
    if not match:
        raise HTTPException(status_code=404, detail="Adjective not found")
    return match


@router.get("/adjective/classes")
def list_adjective_classes():
    """Return a list of available adjective classes."""
    return list(ADJ_CLASS_MAP.keys())


@router.get("/adjective/options/{project}/{noun}/{adjective}")
def get_adjective_options(project: str, noun: str, adjective: str):
    project_path = resolve_path(Path(), "project_root") / project
    handler = load_adjective_handler(project_path, noun, adjective)
    return handler.get_configurable_options()


@router.get("/adjective/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return i_o.io_list_projects()
    except Exception as e:
        # Optional: return empty list on failure instead of 500
        print(f"[list_projects] list_projects failed: {e!r}")
        return []


@router.get("/adjective/nouns/{project}")
def list_nouns(project: str):
    """Return the full noun schema map for a project."""
    project_root = resolve_path(Path(), "project_root")
    try:
        data = i_o.load_schema(project_root / project, "noun")
        return data  # Full noun data map
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")


# ──────────────────────────────────────────────────────────────────────────────
# UPDATE, PROMOTE, DEMOTE (S3-aware via i_o.load_schema / save_schema)
# ──────────────────────────────────────────────────────────────────────────────
@router.post("/adjective/update/{project}/{noun}/{adjective}")
def update_adjective(project: str, noun: str, adjective: str, data: dict = Body(...)):
    project_path = resolve_path(Path(), "project_root") / project
    entries: List[Dict[str, Any]] = i_o.load_schema(project_path, "adjective")

    for i, e in enumerate(entries):
        if e.get("adjective") == adjective and noun in e.get("applies_to", []):
            entries[i] = data
            i_o.save_schema(project_path, "adjective", entries)  # S3-aware write
            return {"status": "updated"}

    raise HTTPException(status_code=404, detail="Adjective not found")


@router.post("/adjective/promote/{project}/{noun}")
def promote_adjective(project: str, noun: str, data: dict = Body(...)):
    proj_root = resolve_path(Path(), "project_root") / project

    # Load noun schema first (S3-aware)
    noun_schema: Dict[str, Any] = i_o.load_schema(proj_root, "noun")
    if noun not in noun_schema:
        raise HTTPException(status_code=404, detail=f"Noun '{noun}' not found")

    fields = noun_schema[noun].get("fields", {})
    field_name = data["adjective"]

    # 🚫 Disallow promoting the primary ID field
    primary_id = noun_schema[noun].get("primary_id_field")
    if primary_id == field_name:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot promote primary ID field '{field_name}' to an adjective"
        )

    # 1) update adjective_types.json (S3-aware)
    adj_entries: List[Dict[str, Any]] = i_o.load_schema(proj_root, "adjective")
    adj_entries.append(data)
    i_o.save_schema(proj_root, "adjective", adj_entries)

    # 2) update noun_types.json to mark the field as adjective (S3-aware)
    existing = fields.get(field_name, {})
    required = existing.get("required", False)

    fields[field_name] = {
        "type": "adjective",
        "adjective_class": data["adjective_class"]
    }
    if required:
        fields[field_name]["required"] = True

    noun_schema[noun]["fields"] = fields
    i_o.save_schema(proj_root, "noun", noun_schema)

    return {"status": "promoted"}


@router.post("/adjective/demote/{project}/{noun}/{adjective}")
def demote_adjective(project: str, noun: str, adjective: str):
    proj_root = resolve_path(Path(), "project_root") / project

    # 1) downgrade field in noun_types.json (S3-aware)
    noun_defs: Dict[str, Any] = i_o.load_schema(proj_root, "noun")
    if noun not in noun_defs:
        raise HTTPException(status_code=404, detail=f"Noun '{noun}' not found")

    fields = noun_defs[noun].get("fields", {})
    if adjective not in fields:
        raise HTTPException(status_code=404, detail=f"Adjective field '{adjective}' not found on noun '{noun}'")

    old = fields[adjective]
    # strip back to a plain string field, preserving non-class metadata
    new = {"type": "string", **{k: v for k, v in old.items() if k not in ("type", "adjective_class")}}
    fields[adjective] = new
    noun_defs[noun]["fields"] = fields
    i_o.save_schema(proj_root, "noun", noun_defs)

    # 2) remove from adjective_types.json (S3-aware)
    adj_entries: List[Dict[str, Any]] = i_o.load_schema(proj_root, "adjective")
    adj_entries = [
        e for e in adj_entries
        if not (e.get("adjective") == adjective and noun in e.get("applies_to", []))
    ]
    i_o.save_schema(proj_root, "adjective", adj_entries)

    return {"status": "demoted"}


# ──────────────────────────────────────────────────────────────────────────────
# LOGIC ROUTES: provide preloaded data to refactored adjectives
# (All data loads are S3-aware via i_o.* helpers)
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/adjective/logic/{project}/{noun}/{adjective}")
def run_logic(project: str, noun: str, adjective: str):
    project_path = resolve_path(Path(), "project_root") / project
    handler = load_adjective_handler(project_path, noun, adjective)

    # ReferenceList: supply noun_items_map
    if isinstance(handler, ReferenceListAdjective):
        noun_items_map: Dict[str, list] = {}
        for ref_noun in handler.get_reference_noun():
            try:
                noun_items_map[ref_noun] = i_o.get_noun_items(project_path, ref_noun)
            except FileNotFoundError:
                noun_items_map[ref_noun] = []
        return handler.use_logic(noun_items_map=noun_items_map)

    # Reference: single noun
    if isinstance(handler, ReferenceAdjective):
        ref_noun = handler.get_reference_noun()
        try:
            items = i_o.get_noun_items(project_path, ref_noun)
        except FileNotFoundError:
            items = []
        return handler.use_logic(noun_items=items)

    # All others
    return handler.use_logic(project_path=project_path) or {}


@router.get("/adjective/logic/{project}/{noun}/{adjective}/{instance_id}")
def run_logic_with_instance(project: str, noun: str, adjective: str, instance_id: str):
    project_path = resolve_path(Path(), "project_root") / project
    handler = load_adjective_handler(project_path, noun, adjective)

    # ActionRequirement: supply noun_schema, verb_defs, instance
    if isinstance(handler, ActionRequirementAdjective):
        noun_schema = i_o.get_noun_schema(project_path, noun)  # dict
        verb_defs   = i_o.load_schema(project_path, "verb")    # dict
        items       = i_o.get_noun_items(project_path, noun)   # list[dict]
        instance = next(
            (it for it in items if it.get(noun_schema["primary_id_field"]) == instance_id),
            None
        )
        if not instance:
            raise HTTPException(status_code=404, detail="Instance not found")
        return handler.use_logic(
            noun_schema=noun_schema,
            verb_defs=verb_defs,
            instance=instance,
            project_path=project_path,
            noun_type=noun
        )

    # For all others, just pass through
    return handler.use_logic(project_path=project_path, instance_id=instance_id) or {}
