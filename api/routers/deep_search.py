# api/routers/deep_search.py

from fastapi import APIRouter, Query
from pathlib import Path

from api.i_o import (
    load_schema,
    get_noun_items,
    load_verb_group_log,
    list_verb_groups,
    get_verb_group_log_config,  # <-- pull verb log configs here
    io_list_projects,
)
from api.manifest.resolver import resolve_path
from core.deep_search import cascade_deep_search
from core.errors import AppError

# -------------------------
# Debug block
# -------------------------
from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()


router = APIRouter()
API_BASE = "http://localhost:8000"  # Still used for actual POST/PUT I/O


@router.get("/deep_search/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception:
        # Optional: return empty list on failure instead of 500
        log.warning("[list_projects] io_list_projects failed", exc_info=True)
        return []


@router.get("/deep_search/{project}")
def deep_search_api(
    project: str,
    term: str = Query(..., description="Search term")
):
    """
    Run deep search for a given project + search term.
    Returns JSON with schema, noun instance, and verb run matches.
    """
    log.debug("[deep_search_api] start", {"project": project, "term": term})

    # Resolve project root
    project_root = resolve_path(Path(), "project_root")
    project_path = project_root / project
    log.debug("[deep_search_api] project_root:", project_root)
    log.debug("[deep_search_api] project_path:", project_path)

    if not project_path.exists():
        log.debug("[deep_search_api] 404 project not found:", project_path)
        raise AppError(
            "PROJECT_NOT_FOUND",
            f"Project '{project}' not found.",
            status=404,
            details={"project": project},
        )

    # 1. Load schemas
    schemas = {
        "noun": load_schema(project_path, "noun"),
        "verb": load_schema(project_path, "verb"),
        "adjective": load_schema(project_path, "adjective"),
        "adverb": load_schema(project_path, "adverb"),
    }
    log.debug("[deep_search_api] schemas loaded:",
          {k: (0 if v is None else (len(v) if isinstance(v, dict) else len(v))) for k, v in schemas.items()})

    # 2. Load all noun instances (and tag with _noun_type)
    noun_instances: list[dict] = []
    noun_schema = schemas.get("noun") or {}
    for noun_type in noun_schema.keys():
        try:
            items = get_noun_items(project_path, noun_type)
            log.debug(f"[deep_search_api] get_noun_items noun_type={noun_type} -> {len(items)} rows")
            for item in items:
                item["_noun_type"] = noun_type
            noun_instances.extend(items)
        except FileNotFoundError:
            log.debug(f"[deep_search_api] get_noun_items: no table for noun_type={noun_type}")
            continue
        except Exception as e:
            log.debug(f"[deep_search_api] get_noun_items ERROR noun_type={noun_type}: {e}")

    log.debug("[deep_search_api] total noun_instances:", len(noun_instances))

    # 3. Load all verb run logs and build primary-id map per group
    verb_runs: list[dict] = []
    verb_primary_id_by_group: dict[str, str] = {}

    try:
        verb_groups = list_verb_groups(project_path)
        log.debug("[deep_search_api] verb_groups:", verb_groups)
    except Exception as e:
        log.debug(f"[deep_search_api] ERROR listing verb groups: {str(e)}")
        verb_groups = []

    for verb_group in verb_groups:
        # Load per-group log config to find the deterministic primary ID field
        cfg_primary_id = None
        try:
            cfg = get_verb_group_log_config(project_path, verb_group)
            cfg_primary_id = cfg.get("primary_id") if isinstance(cfg, dict) else None
            log.debug(f"[deep_search_api] verb_group={verb_group} cfg_primary_id={repr(cfg_primary_id)}")
            if isinstance(cfg_primary_id, str) and cfg_primary_id.strip():
                verb_primary_id_by_group[verb_group] = cfg_primary_id.strip()
        except Exception as e:
            log.debug(f"[deep_search_api] ERROR loading verb log config for {verb_group}: {str(e)}")

        # Load the actual log entries
        try:
            runs = load_verb_group_log(project_path, verb_group)
            log.debug(f"[deep_search_api] loaded {len(runs)} runs for group={verb_group}")
            for run in runs:
                # Tag with the folder/group name AND the primary ID field for deterministic mapping
                run["_verb_group"] = verb_group
                if cfg_primary_id:
                    run["_primary_id_field_from_config"] = cfg_primary_id
            verb_runs.extend(runs)
        except Exception as e:
            log.debug(f"[deep_search_api] ERROR loading verb group log for {verb_group}: {str(e)}")

    log.debug("[deep_search_api] total verb_runs:", len(verb_runs))
    log.debug("[deep_search_api] verb_primary_id_by_group:", verb_primary_id_by_group)

    # 4. Build primary_id lookup for nouns (used for deduplication & display)
    primary_id_lookup = {
        noun_type: (schema.get("primary_id_field") if isinstance(schema, dict) else None)
        for noun_type, schema in (schemas.get("noun") or {}).items()
    }
    log.debug("[deep_search_api] primary_id_lookup (nouns):", primary_id_lookup)

    # 5. Cascade the search (core logic; no I/O)
    results = cascade_deep_search(
        term,
        schemas=schemas,
        noun_instances=noun_instances,
        verb_runs=verb_runs,
        primary_id_lookup=primary_id_lookup,
        verb_primary_id_by_group=verb_primary_id_by_group,  # <-- per-group deterministic primary IDs
    )

    # Quick summary logs
    log.debug("[deep_search_api] results summary:", {
        "schema": len(results.get("schema", [])),
        "noun_instances": len(results.get("noun_instances", [])),
        "verb_runs": len(results.get("verb_runs", [])),
    })

    return {
        "search_term": term,
        "results": results,
    }
