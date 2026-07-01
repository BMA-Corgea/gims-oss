# api/routers/runlog_workbench/view.py
"""Runlog table view endpoint (primary-id aware)."""

from typing import Any, Dict, List

from ._router import router, log
from ._shared import (
    AppError,
    get_project_path,
    _group_pid_field,
    load_verb_group_log,
    load_schema,
    load_data,
    resolve_path,
    get_status_breakdown_core,
    TagAdverb,
    summarize_status_as_fraction,
    collect_headers,
    flatten_entries,
    is_file_empty,
    fs_exists,
    fs_is_file,
    fs_is_dir,
    fs_iterdir,
    fs_walk,
)

# -----------------------------------------------------------------------------
# RUNLOG + DATA DUMP ENDPOINTS (now primary-id aware)
# -----------------------------------------------------------------------------

@router.get("/runlog/{project}/{verb_group}")
def get_runlog(project: str, verb_group: str):
    try:
        project_path = get_project_path(project)
        pid_field = _group_pid_field(project_path, verb_group)

        entries = load_verb_group_log(project_path, verb_group)
        noun_schemas = load_schema(project_path, "noun")
        verb_schemas = load_schema(project_path, "verb")

        enriched_entries: List[Dict[str, Any]] = []

        for entry in entries:
            run_id = entry.get(pid_field)
            verb_key = entry.get("test_type") or entry.get("verb", "")
            if not run_id or verb_key not in verb_schemas:
                ee = dict(entry)
                ee["__status"] = ""
                ee["_pid_field"] = pid_field
                ee["_run_id"] = run_id
                enriched_entries.append(ee)
                continue

            verb_def = verb_schemas[verb_key]
            raw_inputs = verb_def.get("data_entry_schema", {}).get("raw_data_inputs", [])
            adverb_schema = verb_def.get("adverb_schema", {})

            noun_type = (
                verb_def.get("data_entry_schema", {})
                .get("set_up_inputs", {})
                .get("noun_type_ref")
            )
            noun_schema = noun_schemas.get(noun_type, {})

            dump_dir = resolve_path(project_path, "data_dump_dir", verb_group=verb_group, run_id=str(run_id))
            data_entry_path = dump_dir / "DataEntry.json"
            status_path = dump_dir / "Status.json"
            adverb_path = dump_dir / "adverbs.json"

            data_entry_data = load_data(data_entry_path) or []
            status_data = load_data(status_path) or {}
            adverb_data = load_data(adverb_path) or {}

            display_id = str(entry.get(pid_field, ""))
            try:
                for k, adv in (verb_def.get("adverb_schema", {}) or {}).items():
                    if (adv or {}).get("adverb_class") == "Tag":
                        val = (adverb_data or {}).get(k)
                        if val:
                            try:
                                handler = TagAdverb({**adv, "adverb": k})
                                suffix = handler.get_display_suffix(val)
                                if suffix:
                                    display_id += suffix
                            except Exception:
                                pass
            except Exception:
                pass

            present_files = []
            if fs_exists(dump_dir):
                for _root, _dirs, files in fs_walk(dump_dir):
                    present_files.extend(files)

            raw_uploaded = []
            for pocket in raw_inputs:
                pocket_path = dump_dir / pocket
                if fs_is_dir(pocket_path):
                    any_file = False
                    try:
                        for _f in fs_iterdir(pocket_path):
                            any_file = True
                            break
                    except Exception as e:
                        log.debug("view: pocket iterdir failed",
                                  {"path": str(pocket_path), "error": repr(e)})
                        any_file = False
                    if any_file:
                        raw_uploaded.append(pocket)
            status_data["raw_uploaded"] = raw_uploaded

            interp_config = verb_def.get("data_entry_schema", {}).get("interpretation", {})
            interp_tabs = interp_config.get("tabs", [])
            uploaded_tabs = []
            empty_tabs = []
            acceptable_extensions = [".csv", ".xlsx", ".json", ".txt", ".pdf", ".docx"]

            for tab in interp_tabs:
                found_file = None
                for ext in acceptable_extensions:
                    candidate = dump_dir / f"{tab}{ext}"
                    if fs_exists(candidate) and fs_is_file(candidate):
                        found_file = candidate
                        break
                if found_file and not is_file_empty(found_file):
                    uploaded_tabs.append(tab)
                elif found_file:
                    empty_tabs.append(tab)

            status_data.setdefault("interpretation", {})
            status_data["interpretation"]["uploaded_tabs"] = uploaded_tabs

            breakdown = get_status_breakdown_core(project_path, str(run_id))

            enriched_entry = dict(entry)
            enriched_entry["display_ID"] = display_id
            enriched_entry["__status"] = summarize_status_as_fraction(breakdown)
            enriched_entry["_status_breakdown"] = breakdown
            enriched_entry["_data_entry"] = data_entry_data
            enriched_entry["_status_data"] = status_data
            enriched_entry["_adverb_data"] = adverb_data
            enriched_entry["_present_files"] = present_files
            enriched_entry["_raw_inputs"] = raw_inputs
            enriched_entry["_verb_key"] = verb_key
            enriched_entry["_verb_def"] = verb_def
            enriched_entry["_adverb_schema"] = adverb_schema
            enriched_entry["_noun_schema"] = noun_schema
            enriched_entry["_pid_field"] = pid_field
            enriched_entry["_run_id"] = run_id

            if "run_ID" not in enriched_entry:
                enriched_entry["run_ID"] = run_id

            enriched_entries.append(enriched_entry)

        headers = collect_headers(enriched_entries)
        if pid_field in headers:
            headers = [pid_field] + [h for h in headers if h != pid_field]

        rows = flatten_entries(enriched_entries, headers)

        return {
            "headers": headers,
            "rows": rows,
            "entries": enriched_entries,
            "meta": {
                "primary_id_field": pid_field,
                "verb_group": verb_group,
            },
        }

    except Exception as e:
        log.error("get_runlog failed", exc_info=True)
        raise AppError("RUNLOG_LOAD_FAILED", f"Error loading runlog: {e}", status=500,
                       details={"project": project, "verb_group": verb_group})
