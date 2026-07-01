# api/routers/audit.py
# FastAPI router for the Audit module (GUI backend).
# Performs I/O via api/i_o.py and api/manifest/resolver.py, then calls core_audit_instances.py (pure logic).
# Noun instances are loaded from SQL (NOT from items.jsonl).

from __future__ import annotations

from fastapi import APIRouter, Query
from pathlib import Path
from typing import Optional
import traceback

from core.errors import AppError

# Project imports
# Expect these modules to be importable in your runtime (same package layout as your repo):
from api.i_o import (
    load_schema,
    load_override,
    list_verb_groups,
    load_verb_group_log,
    get_verb_group_log_config,
    get_noun_items,
    load_data,
    io_list_projects,
)
from api.manifest.resolver import resolve_path
from core.audit import audit_all

# Debug control - set to False to disable all backend debug logging
from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()

# -----------------------------
# Router
# -----------------------------
router = APIRouter(prefix="/api/audit", tags=["Audit"])
log.debug("[audit_gui] Router initialized at /api/audit with tag 'Audit'")

# -----------------------------
# Helpers
# -----------------------------

def _resolve_project_path(project: str) -> Path:
    """
    Resolve a project path. Accepts an absolute path or a project name living under the
    project root — the SAME root the project dropdown lists from (``io_list_projects`` →
    ``resolve_path(project_root)``), so any listed project resolves.
    """
    log.debug("[_resolve_project_path] input:", project)
    p = Path(project)
    if p.exists():
        log.debug("[_resolve_project_path] Using provided path:", p)
        return p.resolve()

    # Resolve <project_root>/<project> via the canonical resolver (not a fragile __file__ walk —
    # the old ``here.name == "audit_gui.py"`` check broke when the reorg renamed this to audit.py,
    # which pointed the project lookup at api/routers/projects/ → PROJECT_PATH_NOT_FOUND).
    candidate = resolve_path(Path(), "project_root") / project
    log.debug("[_resolve_project_path] candidate:", candidate)
    if candidate.exists():
        return candidate.resolve()

    raise AppError(
        "PROJECT_PATH_NOT_FOUND",
        f"Project path not found: {project}",
        status=404,
        details={"project": project},
    )


def _load_all_schemas(project_path: Path) -> tuple[dict, dict, dict, dict]:
    """
    Load noun_types, verb_types (dicts), adjective_types, adverb_types (lists) -> return as
    noun_types: dict[str, dict]
    verb_types: dict[str, dict]
    adjective_types: dict[str, dict] (keyed by 'adjective')
    adverb_types: dict[str, dict]    (keyed by 'adverb')
    """
    log.debug("[_load_all_schemas] Start for project:", project_path)
    try:
        noun_types = load_schema(project_path, "noun") or {}
        verb_types = load_schema(project_path, "verb") or {}
        adj_list   = load_schema(project_path, "adjective") or []
        adv_list   = load_schema(project_path, "adverb") or []
    except FileNotFoundError as e:
        log.debug("[_load_all_schemas] File not found:", e)
        raise AppError(
            "SCHEMA_NOT_FOUND",
            str(e),
            status=404,
            details={"project": str(project_path)},
        )
    except Exception as e:
        log.debug("[_load_all_schemas] Error:", e)
        log.debug(traceback.format_exc())
        raise AppError(
            "SCHEMA_LOAD_FAILED",
            f"Failed to load schemas: {e}",
            status=500,
            details={"project": str(project_path)},
        )

    # Convert adjective/adverb lists to dicts keyed by their identifying field
    adjective_types = {}
    for entry in (adj_list if isinstance(adj_list, list) else []):
        key = entry.get("adjective")
        if key:
            if key in adjective_types:
                log.debug(f"[_load_all_schemas] ! duplicate adjective key in list: {key}")
            adjective_types[key] = entry

    adverb_types = {}
    for entry in (adv_list if isinstance(adv_list, list) else []):
        key = entry.get("adverb")
        if key:
            if key in adverb_types:
                log.debug(f"[_load_all_schemas] ! duplicate adverb key in list: {key}")
            adverb_types[key] = entry

    log.debug("[_load_all_schemas] Loaded:",
          f"noun_types={len(noun_types)}",
          f"verb_types={len(verb_types)}",
          f"adjective_types={len(adjective_types)}",
          f"adverb_types={len(adverb_types)}")
    return noun_types, verb_types, adjective_types, adverb_types


def _build_noun_instances_sql_only(project_path: Path, noun_types: dict) -> dict[str, list[dict]]:
    """
    For each noun type, pull instances from the SQL DB (no JSONL fallback).
    """
    log.debug("[_build_noun_instances_sql_only] Start")
    out: dict[str, list[dict]] = {}
    for noun_type in noun_types.keys():
        try:
            rows = get_noun_items(project_path, noun_type)  # SQL only in your i_o.py
            log.debug(f"[_build_noun_instances_sql_only] {noun_type}: {len(rows)} rows")
            out[noun_type] = rows
        except Exception as e:
            log.debug(f"[_build_noun_instances_sql_only] ! Failed for noun_type={noun_type}: {e}")
            out[noun_type] = []
    return out


def _build_noun_index(noun_types: dict, noun_instances_by_type: dict[str, list[dict]]) -> dict[str, set]:
    """
    Build { noun_type -> {primary_ids} } using each noun type's primary_id_field.
    """
    log.debug("[_build_noun_index] Start")
    index: dict[str, set] = {}
    for nt, schema in noun_types.items():
        pf = schema.get("primary_id_field")
        ids = set()
        if pf:
            for inst in noun_instances_by_type.get(nt, []):
                if pf in inst and inst.get(pf):
                    ids.add(inst.get(pf))
        index[nt] = ids
        log.debug(f"[_build_noun_index] {nt}: pf={pf} count={len(ids)}")
    return index


def _list_verb_groups_safe(project_path: Path) -> list[str]:
    log.debug("[_list_verb_groups_safe] Start")
    try:
        groups = list_verb_groups(project_path)
        log.debug("[_list_verb_groups_safe] Found groups:", groups)
        return groups
    except Exception as e:
        log.debug("[_list_verb_groups_safe] ! list_verb_groups failed:", e)
        return []


def _load_runs_for_group(project_path: Path, group: str) -> tuple[list[dict], Optional[str]]:
    """
    Load the verb group log and primary_id field name for the group (from its log_config).
    Returns (entries, primary_id_field or None).
    """
    log.debug(f"[_load_runs_for_group] group={group}")
    try:
        cfg = get_verb_group_log_config(project_path, group)
        primary_id = cfg.get("primary_id") or cfg.get("primaryId") or cfg.get("primary")
        log.debug(f"[_load_runs_for_group] primary_id field={primary_id}")
    except Exception as e:
        log.debug(f"[_load_runs_for_group] ! get_verb_group_log_config failed for {group}: {e}")
        primary_id = None

    try:
        entries = load_verb_group_log(project_path, group)
        log.debug(f"[_load_runs_for_group] entries={len(entries)}")
    except Exception as e:
        log.debug(f"[_load_runs_for_group] ! load_verb_group_log failed for {group}: {e}")
        entries = []

    return entries, primary_id


def _infer_verb_name(entry: dict) -> Optional[str]:
    """
    Derive the verb 'name' from a log entry. Accept common fallbacks.
    """
    for key in ("verb", "test_type", "testType", "type", "verb_name"):
        if key in entry and entry.get(key):
            return entry.get(key)
    return None


def _hydrate_run_entry(project_path: Path, group: str, run_id: str, base_entry: dict) -> dict:
    """
    Add data_entry and adverbs payloads by reading the resolved files. Missing files -> {}.
    """
    log.debug(f"[_hydrate_run_entry] group={group} run_id={run_id}")
    run = dict(base_entry)  # shallow copy
    try:
        de_path = resolve_path(project_path, "data_entry", verb_group=group, run_id=run_id)
        run["data_entry"] = load_data(de_path) or {}
        log.debug(f"[_hydrate_run_entry] DataEntry.json -> {de_path} loaded={bool(run['data_entry'])}")
    except Exception as e:
        log.debug(f"[_hydrate_run_entry] ! data_entry load failed: {e}")
        run["data_entry"] = {}

    try:
        adv_path = resolve_path(project_path, "adverb_file", verb_group=group, run_id=run_id)
        run["adverbs"] = load_data(adv_path) or {}
        log.debug(f"[_hydrate_run_entry] adverbs.json -> {adv_path} loaded keys={list(run['adverbs'].keys()) if isinstance(run['adverbs'], dict) else 'n/a'}")
    except Exception as e:
        log.debug(f"[_hydrate_run_entry] ! adverbs load failed: {e}")
        run["adverbs"] = {}

    # Status is not required by core audit, but grab it if useful downstream (ignored by core)
    try:
        st_path = resolve_path(project_path, "status_file", verb_group=group, run_id=run_id)
        run["status"] = load_data(st_path) or {}
        log.debug(f"[_hydrate_run_entry] Status.json -> {st_path} loaded={bool(run['status'])}")
    except Exception as e:
        log.debug(f"[_hydrate_run_entry] ! status load failed: {e}")
        run["status"] = {}

    return run


def _build_run_entries_by_group(project_path: Path, verb_types: dict) -> dict[str, list[dict]]:
    """
    Build run_entries_by_group for core audit:
    { group: [ { _runID, verb, data_entry: {}, adverbs: {} , ... } ] }
    """
    log.debug("[_build_run_entries_by_group] Start")
    out: dict[str, list[dict]] = {}
    groups = _list_verb_groups_safe(project_path)
    for group in groups:
        entries, primary_id_field = _load_runs_for_group(project_path, group)
        if not entries:
            log.debug(f"[_build_run_entries_by_group] group={group} no entries")
            out[group] = []
            continue

        hydrated: list[dict] = []
        for i, row in enumerate(entries):
            run_id = None
            if primary_id_field and primary_id_field in row:
                run_id = row.get(primary_id_field)
            else:
                # fallback to common field names
                for key in ("run_ID", "runId", "run_id", "_runID"):
                    if key in row and row.get(key):
                        run_id = row.get(key)
                        break

            verb_name = _infer_verb_name(row)
            run_min = {
                "_runID": run_id,
                "verb": verb_name
            }
            log.debug(f"[_build_run_entries_by_group] row#{i} run_id={run_id} verb={verb_name}")

            # Hydrate data_entry + adverbs
            if run_id:
                hydrated.append(_hydrate_run_entry(project_path, group, run_id, {**row, **run_min}))
            else:
                log.debug(f"[_build_run_entries_by_group] ! missing run_id in group={group} row#{i}")
                hydrated.append({**row, **run_min, "data_entry": {}, "adverbs": {}})

        out[group] = hydrated
        log.debug(f"[_build_run_entries_by_group] group={group} hydrated={len(hydrated)}")
    return out


def _build_override_index(project_path: Path) -> dict[str, set]:
    """
    Build a flexible override alias index:
      - 'run' + 'aliases':  run -> set(aliases)
      - 'alias_of':         alias_of -> set(add run)
      - 'references':       run -> set(references)
    Any of the above will contribute to an equivalence set for that run.
    """
    log.debug("[_build_override_index] Start")
    idx: dict[str, set] = {}
    try:
        entries = load_override(project_path) or []
    except Exception as e:
        log.debug("[_build_override_index] ! load_override failed:", e)
        entries = []

    for i, row in enumerate(entries):
        run = row.get("run")
        if run:
            idx.setdefault(run, set())
            # aliases
            if isinstance(row.get("aliases"), list):
                for a in row["aliases"]:
                    idx[run].add(a)
            # references (list of related run ids)
            if isinstance(row.get("references"), list):
                for r in row["references"]:
                    idx[run].add(r)
        # alias_of (this row is an alias of some other run)
        alias_of = row.get("alias_of")
        if alias_of and run:
            idx.setdefault(alias_of, set()).add(run)

        log.debug(f"[_build_override_index] row#{i} run={run} alias_of={alias_of}")

    log.debug("[_build_override_index] Built index keys:", list(idx.keys()))
    return idx


# -----------------------------
# Routes
# -----------------------------

@router.get("/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception:
        # Optional: return empty list on failure instead of 500
        log.warning("[list_projects] io_list_projects failed", exc_info=True)
        return []

@router.get("/{project}")
def audit_project(
    project: str,
    # Optional toggles for debugging / shaping output
    include_noun_index: bool = Query(False, description="Include computed noun_index in response for debugging"),
):
    """
    Run full audit for a project. Returns findings + summary.
    - project: absolute path OR project folder under repo_root/projects/<project>
    """
    log.debug("[audit_project] Start project=", project)
    project_path = _resolve_project_path(project)
    log.debug("[audit_project] Resolved project_path:", project_path)

    # 1) Load schemas
    noun_types, verb_types, adjective_types, adverb_types = _load_all_schemas(project_path)

    # 2) SQL-only noun instances + noun_index
    noun_instances_by_type = _build_noun_instances_sql_only(project_path, noun_types)
    noun_index = _build_noun_index(noun_types, noun_instances_by_type)

    # 3) Run entries (by group) + hydrate data_entry/adverbs
    run_entries_by_group = _build_run_entries_by_group(project_path, verb_types)

    # 4) Override index
    override_index = _build_override_index(project_path)

    # 5) Core audit
    log.debug("[audit_project] Calling core audit_all()")
    result = audit_all(
        noun_types=noun_types,
        verb_types=verb_types,
        adverb_types=adverb_types,
        adjective_types=adjective_types,
        noun_instances_by_type=noun_instances_by_type,
        run_entries_by_group=run_entries_by_group,
        noun_index=noun_index,
        override_index=override_index,
    )
    log.debug("[audit_project] audit_all() completed. Findings:", result["summary"]["total"])

    # 6) Attach debug extras if requested
    if include_noun_index:
        log.debug("[audit_project] include_noun_index=True")
        result["debug"] = {
            "noun_index_sizes": {k: len(v) for k, v in noun_index.items()},
            "verb_groups": list(run_entries_by_group.keys()),
        }

    return result


@router.get("/{project}/verb_groups")
def list_groups(project: str):
    log.debug("[list_groups] project=", project)
    project_path = _resolve_project_path(project)
    groups = _list_verb_groups_safe(project_path)
    return {"project": str(project_path), "verb_groups": groups}