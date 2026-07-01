# api/routers/noun_workbench/routes_read.py
"""Read routes: project/noun listing, items, schema, and reference options.

Handlers are registered in their ORIGINAL top-to-bottom order. Bodies are moved
verbatim from the original ``noun_workbench.py``.
"""

from typing import Any, Dict, List

# Repo-local imports (schema utilities only, no JSONL)
from api.i_o import (
    get_adjective_schema,
    get_noun_schema,
    list_projects_safe,
    load_schema,
)

# Phase-2 error contract
from core.errors import AppError

from ._router import log, router
from .paths import get_project_path


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/projects", summary="List available projects")
def list_projects() -> List[str]:
    """Return a list of available projects (S3- and local-aware)."""
    return list_projects_safe()

@router.get("/{project}/{noun_type}/items", response_model=List[Dict[str, Any]])
def get_items(project: str, noun_type: str) -> List[Dict[str, Any]]:
    """
    Load all items for a given noun in a project from the unified `instances` store
    (core.storage record store) — Postgres JSONB in RDS mode, SQLite locally.
    """
    proj_path = get_project_path(project)
    schema = get_noun_schema(proj_path, noun_type) or {}
    primary = schema.get("primary_id_field") or "id"

    from core.storage.factory import collection_for_noun, get_record_store
    rows = get_record_store(proj_path).list_records(collection_for_noun(noun_type))

    def _clean_order(d: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(d, dict):
            return d
        r = dict(d)
        r.pop(" rowid", None); r.pop("_rowid", None)
        k1 = [primary] if primary in r else []
        k2 = [k for k in r.keys()
              if k not in k1 and not str(k).startswith("_") and k.strip().lower() != "rowid"]
        k3 = [k for k in r.keys() if k not in k1 and k not in k2]
        return {k: r.get(k) for k in (k1 + k2 + k3)}
    return [_clean_order(x) for x in rows]

@router.get("/{project}", summary="List available noun types")
def list_nouns(project: str) -> List[str]:
    pp = get_project_path(project)
    schema = load_schema(pp, "noun")  # expects noun_types.json in the project root
    names = sorted(schema.keys())
    log.debug("[nouns] list", names)
    return names

@router.get("/{project}/{noun_type}/schema", summary="Fetch noun schema")
def get_schema(project: str, noun_type: str):
    pp = get_project_path(project)
    schema = get_noun_schema(pp, noun_type)
    if not schema:
        raise AppError("NOUN_NOT_FOUND", f"Noun '{noun_type}' not found.", status=404, details={"noun_type": noun_type})
    log.debug("[schema]", noun_type, "primary:", schema.get("primary_id_field"))
    return schema

@router.get("/{project}/{noun_type}/references/{field_name}", summary="Dropdown options for Reference adjectives")
def get_reference_options(project: str, noun_type: str, field_name: str) -> List[Dict[str, str]]:
    pp = get_project_path(project)
    adj = get_adjective_schema(pp, field_name, applies_to=noun_type)
    if not adj:
        return []

    adj_class = adj.get("class") or adj.get("adjective_class")
    if adj_class not in {"Reference", "ReferenceList"}:
        return []

    ref_noun = adj.get("reference_noun") or adj.get("reference")
    if not ref_noun:
        return []

    ref_schema = get_noun_schema(pp, ref_noun) or {}
    ref_primary = ref_schema.get("primary_id_field") or "id"

    # Read the reference noun's instances from the unified store.
    from core.storage.factory import collection_for_noun, get_record_store
    rows = get_record_store(pp).list_records(collection_for_noun(ref_noun))

    options: List[Dict[str, str]] = []
    for it in rows:
        val = it.get(ref_primary)
        if not val:
            continue
        label = str(val)
        for cand in ["name", "label", "client", "description"]:
            if cand in it and it[cand] and cand != ref_primary:
                label = f"{val} — {it[cand]}"
                break
        options.append({"value": str(val), "label": label})
    return options
