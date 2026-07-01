# api/routers/adjective.py  ─── S3-aware (uses i_o.load_schema/save_schema/save_json everywhere)
from fastapi import APIRouter, Body
import sys
from pathlib import Path

# Ensure repo root on sys.path (unchanged)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from typing import List, Dict, Any

from core.words.handlers.base import WordHandler

from api.manifest.resolver import resolve_path
from api import i_o
from api.routers._descriptor_crud import (
    load_descriptor_handler,
    run_descriptor_logic,
    list_projects as _list_projects,
)

from core.errors import AppError
from utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter()

# class key -> unified Descriptor behavior name. This is the noun-context subset
# of the one canonical core.words.handlers.DESCRIPTOR_CLASSES (the legacy twin
# adjective tree collapsed into Descriptor); keys preserved verbatim so the
# /adjective/classes list + the dispatch-cohesion guard are unchanged.
ADJ_CLASS_MAP: dict[str, str] = {
    "ActionRequirement": "ActionRequirement",
    "Tag":               "Tag",
    "Reference":         "Reference",
    "ReferenceList":     "ReferenceList",
    "Picture":           "Picture",
    "Duration":          "Duration",
}

# ──────────────────────────────────────────────────────────────────────────────
# Helper: load + instantiate an adjective handler WITHOUT utils.disambiguation
# ──────────────────────────────────────────────────────────────────────────────
def load_adjective_handler(
    project_path: Path,
    noun_type: str,
    adjective_name: str
) -> WordHandler:
    """
    Pure resolver/i_o version of utils.disambiguation.load_adjective_handler().
    Thin alias over the shared descriptor loader (Phase 3c). Adjectives attach to a noun via an
    `applies_to` list and carry verb_types (needed by the ActionRequirement behavior); an
    unknown/missing adjective_class falls back to a bare WordHandler (legacy BaseAdjective).
    """
    return load_descriptor_handler(
        project_path,
        schema_kind="adjective",
        scope_value=noun_type,
        attaches_to="noun",
        match=lambda e: e.get("adjective") == adjective_name and noun_type in e.get("applies_to", []),
        behavior_of=lambda e: ADJ_CLASS_MAP.get(e.get("adjective_class", "")),
        not_found_code="ADJECTIVE_NOT_FOUND",
        not_found_msg="Adjective not found",
        not_found_details={"adjective": adjective_name, "noun": noun_type},
        handler_kwargs=lambda: {"verb_types": i_o.load_schema(project_path, "verb")},
    )


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
        raise AppError(
            "ADJECTIVE_NOT_FOUND",
            "Adjective not found",
            status=404,
            details={"adjective": adjective, "noun": noun},
        )
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
    return _list_projects()


@router.get("/adjective/nouns/{project}")
def list_nouns(project: str):
    """Return the full noun schema map for a project."""
    project_root = resolve_path(Path(), "project_root")
    try:
        data = i_o.load_schema(project_root / project, "noun")
        return data  # Full noun data map
    except FileNotFoundError:
        raise AppError(
            "PROJECT_NOT_FOUND",
            "Project not found",
            status=404,
            details={"project": project},
        )


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

    raise AppError(
        "ADJECTIVE_NOT_FOUND",
        "Adjective not found",
        status=404,
        details={"adjective": adjective, "noun": noun},
    )


@router.post("/adjective/promote/{project}/{noun}")
def promote_adjective(project: str, noun: str, data: dict = Body(...)):
    proj_root = resolve_path(Path(), "project_root") / project

    # Load noun schema first (S3-aware)
    noun_schema: Dict[str, Any] = i_o.load_schema(proj_root, "noun")
    if noun not in noun_schema:
        raise AppError(
            "NOUN_NOT_FOUND",
            f"Noun '{noun}' not found",
            status=404,
            details={"noun": noun, "project": project},
        )

    fields = noun_schema[noun].get("fields", {})
    field_name = data["adjective"]

    # 🚫 Disallow promoting the primary ID field
    primary_id = noun_schema[noun].get("primary_id_field")
    if primary_id == field_name:
        raise AppError(
            "CANNOT_PROMOTE_PRIMARY_ID",
            f"Cannot promote primary ID field '{field_name}' to an adjective",
            status=400,
            details={"field": field_name, "noun": noun, "project": project},
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
        raise AppError(
            "NOUN_NOT_FOUND",
            f"Noun '{noun}' not found",
            status=404,
            details={"noun": noun, "project": project},
        )

    fields = noun_defs[noun].get("fields", {})
    if adjective not in fields:
        raise AppError(
            "ADJECTIVE_FIELD_NOT_FOUND",
            f"Adjective field '{adjective}' not found on noun '{noun}'",
            status=404,
            details={"adjective": adjective, "noun": noun, "project": project},
        )

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
    return run_descriptor_logic(project_path, load_adjective_handler(project_path, noun, adjective))


@router.get("/adjective/logic/{project}/{noun}/{adjective}/{instance_id}")
def run_logic_with_instance(project: str, noun: str, adjective: str, instance_id: str):
    project_path = resolve_path(Path(), "project_root") / project
    handler = load_adjective_handler(project_path, noun, adjective)

    # ActionRequirement: supply noun_schema, verb_defs, instance
    if getattr(handler, "behavior_name", None) == "ActionRequirement":
        noun_schema = i_o.get_noun_schema(project_path, noun)  # dict
        verb_defs   = i_o.load_schema(project_path, "verb")    # dict
        items       = i_o.get_noun_items(project_path, noun)   # list[dict]
        instance = next(
            (it for it in items if it.get(noun_schema["primary_id_field"]) == instance_id),
            None
        )
        if not instance:
            raise AppError(
                "INSTANCE_NOT_FOUND",
                "Instance not found",
                status=404,
                details={"instance_id": instance_id, "noun": noun, "project": project},
            )
        return handler.use_logic(
            noun_schema=noun_schema,
            verb_defs=verb_defs,
            instance=instance,
            project_path=project_path,
            noun_type=noun
        )

    # For all others, just pass through
    return handler.use_logic(project_path=project_path, instance_id=instance_id) or {}
