# api/routers/runlog_workbench/data_dump.py
"""Data-dump read endpoint + project / verb-group listing endpoints."""

from fastapi import HTTPException

from ._router import router
from ._shared import (
    AppError,
    get_project_path,
    _group_pid_field,
    resolve_path,
    load_data,
    load_schema,
    load_verb_group_log,
    load_override,
    s3_read_text,
    prepare_data_dump,
    is_file_empty,
    io_list_projects,
    list_verb_groups,
    _jp_list_dirnames,
    fs_exists,
    fs_is_file,
    fs_iterdir,
    fs_walk,
    log,
)


@router.get("/runlog/{project}/{verb_group}/{run_id}/dump")
def get_data_dump(project: str, verb_group: str, run_id: str):
    log.debug(f"[dump] start | project={project}, verb_group={verb_group}, run_id={run_id}")
    try:
        project_path = get_project_path(project)
        pid_field = _group_pid_field(project_path, verb_group)

        data_dump_dir = resolve_path(project_path, "data_dump_dir", verb_group=verb_group, run_id=run_id)
        data_entry_path = data_dump_dir / "DataEntry.json"
        status_path = data_dump_dir / "Status.json"
        adverb_path = data_dump_dir / "adverbs.json"

        present_files = []
        if fs_exists(data_dump_dir):
            for _root, _dirs, files in fs_walk(data_dump_dir):
                present_files.extend(files)

        try:
            data_entry_data = load_data(data_entry_path) or []
        except Exception:
            log.debug("[dump] failed to load DataEntry.json; defaulting to []",
                      {"run_id": run_id, "path": str(data_entry_path)}, exc_info=True)
            data_entry_data = []

        try:
            status_data = load_data(status_path) or {}
        except Exception:
            log.debug("[dump] failed to load Status.json; defaulting to {}",
                      {"run_id": run_id, "path": str(status_path)}, exc_info=True)
            status_data = {}

        try:
            adverb_data = load_data(adverb_path) or {}
        except Exception:
            log.debug("[dump] failed to load adverbs.json; defaulting to {}",
                      {"run_id": run_id, "path": str(adverb_path)}, exc_info=True)
            adverb_data = {}

        verb_schemas = load_schema(project_path, "verb")
        noun_schemas = load_schema(project_path, "noun")

        log_entries = load_verb_group_log(project_path, verb_group)
        run_entry = next((e for e in log_entries if str(e.get(pid_field)) == str(run_id)), None)
        if not run_entry:
            # Caught locally by `except HTTPException: raise` below; converting to AppError
            # would change catch semantics (404 would fall through to the 500 handler).
            raise AppError("RUN_NOT_FOUND", f"Run {run_id} not found in {verb_group} log", status=404,
                           details={"run_id": run_id, "verb_group": verb_group})

        verb_key = run_entry.get("test_type") or run_entry.get("verb")
        verb_def = verb_schemas.get(verb_key, {})

        _raw_inputs = verb_def.get("data_entry_schema", {}).get("raw_data_inputs", [])

        noun_type_name = (
            verb_def.get("data_entry_schema", {})
            .get("set_up_inputs", {})
            .get("noun_type_ref")
        )
        noun_schema = noun_schemas.get(noun_type_name, {}) if noun_type_name else {}

        overrides_all = load_override(project_path) or []
        overrides = [
            row for row in overrides_all
            if str(row.get("run")) == str(run_id)
               and (not row.get("verb") or row.get("verb") == verb_key)
        ]

        markdown_instructions = ""
        if fs_exists(data_dump_dir):
            try:
                for f in fs_iterdir(data_dump_dir):
                    if f.name.lower() == "instructions.md":
                        markdown_instructions = s3_read_text(f, encoding="utf-8").strip()
                        break
            except Exception:
                pass

        if not markdown_instructions:
            setup = verb_def.get("data_entry_schema", {}).get("set_up_inputs", {})
            setup_instructions = setup.get("instructions", []) if isinstance(setup, dict) else []
            markdown_instructions = "\n".join(setup_instructions) if setup_instructions else "No instructions available"

        interp_config = verb_def.get("data_entry_schema", {}).get("interpretation", {})
        expected_tabs = interp_config.get("tabs", [])
        uploaded_tabs, empty_tabs = [], []
        acceptable_extensions = [".csv", ".xlsx", ".json", ".txt", ".pdf", ".docx"]

        for tab in expected_tabs:
            found_file = None
            for ext in acceptable_extensions:
                candidate = data_dump_dir / f"{tab}{ext}"
                if fs_exists(candidate) and fs_is_file(candidate):
                    found_file = candidate
                    break
            if found_file and not is_file_empty(found_file):
                uploaded_tabs.append(tab)
            elif found_file:
                empty_tabs.append(tab)

        status_data.setdefault("interpretation", {})
        status_data["interpretation"]["uploaded_tabs"] = uploaded_tabs

        result = prepare_data_dump(
            project_path=project_path,
            run_id=str(run_id),
            run_entry=run_entry,
            verb_def=verb_def,
            noun_schema=noun_schema,
            data_entry_data=data_entry_data,
            adverb_data=adverb_data,
            interpretation_data={},
            overrides=overrides,
        )

        result["instructions"] = markdown_instructions.splitlines()
        result.setdefault("meta", {})["primary_id_field"] = pid_field
        return result

    except HTTPException:
        raise
    except Exception as e:
        raise AppError("DATA_DUMP_LOAD_FAILED", f"Error loading data dump: {e}", status=500,
                       details={"project": project, "verb_group": verb_group, "run_id": run_id})

@router.get("/runlog_data_dump/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception:
        # Optional: return empty list on failure instead of 500
        log.warning("[list_projects] io_list_projects failed; returning []", exc_info=True)
        return []

@router.get("/runlog_data_dump/verb_groups/{project}")
def list_verb_groups_for_project(project: str):
    try:
        project_path = get_project_path(project)
        # Prefer the already S3-aware api.i_o implementation
        return list_verb_groups(project_path)
    except Exception:
        # Fallback: list directories under verbs/
        log.debug("[verb_groups] list_verb_groups failed; falling back to directory scan",
                  {"project": project}, exc_info=True)
        try:
            project_path = get_project_path(project)
            verbs_dir = project_path / "verbs"
            return _jp_list_dirnames(verbs_dir)
        except Exception:
            log.warning("[verb_groups] fallback directory scan failed; returning defaults",
                        {"project": project}, exc_info=True)
            return ["main", "test"]
