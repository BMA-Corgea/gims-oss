# gui/runlog_workbench_gui.py

from fastapi import APIRouter, Body, HTTPException, Request, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Iterable, Tuple
import json
import mimetypes, io, zipfile
import traceback
from datetime import datetime

# Make sure we can import from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

# Debug control - set to False to disable all backend debug logging
DEBUG_ENABLED = False  # Change to True to enable debug logs

def debug(*args, **kwargs):
    """Debug print that respects DEBUG_ENABLED flag."""
    if DEBUG_ENABLED:
        print(*args, **kwargs)

# -----------------------------------------------------------------------------
# S3-awareness: prefer api.i_o shims if present; else provide local shims that
# delegate to api.json_proxy (when available) or local filesystem.
# -----------------------------------------------------------------------------
_HAS_S3 = False
_json_proxy = None

try:
    from api import json_proxy as _json_proxy  # Optional S3 layer
    _HAS_S3 = True
except Exception:
    _json_proxy = None
    _HAS_S3 = False

# Try to import fs_* shims from api.i_o if your codebase already has them.
_fs = {}
try:
    from api.i_o import (
        fs_exists, fs_is_file, fs_is_dir, fs_iterdir, fs_walk, fs_mkdirs,
        fs_open_readbin, fs_open_writebin, fs_write_bytes, fs_remove,
        fs_stat_size, fs_glob_first, make_zip_stream,
    )  # type: ignore
    _fs.update({"external_shims": True})
except Exception:
    _fs.update({"external_shims": False})

# Provide fallback shims if api.i_o didn't export them
if not _fs.get("external_shims"):
    import os
    import io
    import zipfile
    from pathlib import Path
    from typing import Iterable, List, Optional, Tuple

    def fs_exists(p: Path) -> bool:
        if _HAS_S3 and hasattr(_json_proxy, "exists"):
            return bool(_json_proxy.exists(str(p)))
        return p.exists()

    def fs_is_file(p: Path) -> bool:
        if _HAS_S3 and hasattr(_json_proxy, "is_file"):
            return bool(_json_proxy.is_file(str(p)))
        return p.is_file()

    def fs_is_dir(p: Path) -> bool:
        if _HAS_S3 and hasattr(_json_proxy, "is_dir"):
            return bool(_json_proxy.is_dir(str(p)))
        return p.is_dir()

    def fs_iterdir(p: Path) -> Iterable[Path]:
        """
        Return an iterable of Path objects (never strings).
        json_proxy.iterdir() already yields Path-like entries on S3.
        """
        if _HAS_S3 and hasattr(_json_proxy, "iterdir"):
            items = _json_proxy.iterdir(str(p)) or []
            out: list[Path] = []
            for x in items:
                if isinstance(x, Path):
                    out.append(x)
                else:
                    # Normalize possible string/Key return into a Path relative to parent
                    out.append(Path(x) if ("/" in str(x) or "\\" in str(x)) else (p / str(x)))
            return out
        return list(p.iterdir())

    def fs_walk(p: Path):
        if _HAS_S3 and hasattr(_json_proxy, "walk"):
            # json_proxy.walk should behave like os.walk, returning (root, dirs, files)
            return _json_proxy.walk(str(p))
        return os.walk(p)

    def fs_mkdirs(p: Path) -> None:
        if _HAS_S3 and hasattr(_json_proxy, "makedirs"):
            _json_proxy.makedirs(str(p))
            return
        p.mkdir(parents=True, exist_ok=True)

    def fs_open_readbin(p: Path):
        if _HAS_S3 and hasattr(_json_proxy, "open"):
            return _json_proxy.open(str(p), "rb")
        return open(p, "rb")

    def fs_open_writebin(p: Path):
        if _HAS_S3 and hasattr(_json_proxy, "open"):
            return _json_proxy.open(str(p), "wb")
        return open(p, "wb")

    def fs_write_bytes(p: Path, data: bytes) -> None:
        if _HAS_S3 and hasattr(_json_proxy, "write_bytes"):
            _json_proxy.write_bytes(str(p), data)
            return
        fs_mkdirs(p.parent)
        p.write_bytes(data)

    def fs_remove(p: Path) -> None:
        if _HAS_S3 and hasattr(_json_proxy, "remove"):
            _json_proxy.remove(str(p))
            return
        try:
            os.remove(p)
        except FileNotFoundError:
            pass

    def fs_stat_size(p: Path) -> int:
        if _HAS_S3 and hasattr(_json_proxy, "stat"):
            st = _json_proxy.stat(str(p))
            # Be lenient: accept dict-like or os.stat_result-like
            if hasattr(st, "st_size"):
                return int(st.st_size)  # type: ignore
            if isinstance(st, dict) and "st_size" in st:
                return int(st["st_size"])
            raise RuntimeError("json_proxy.stat returned unexpected type")
        return p.stat().st_size

    def fs_glob_first(d: Path, pattern: str) -> Optional[Path]:
        if _HAS_S3 and hasattr(_json_proxy, "glob_first"):
            g = _json_proxy.glob_first(str(d), pattern)
            return Path(g) if g else None
        return next(iter(d.glob(pattern)), None)

    def make_zip_stream(files: List[Tuple[Path, str]]):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path, arcname in files:
                with fs_open_readbin(path) as fh:
                    zf.writestr(arcname, fh.read())
        buf.seek(0)
        return buf

def s3_read_text(p: Path, encoding: str = "utf-8") -> str:
    if _HAS_S3 and hasattr(_json_proxy, "read_text"):
        return _json_proxy.read_text(str(p), encoding=encoding)  # type: ignore
    return p.read_text(encoding=encoding)

def s3_write_text(p: Path, text: str, encoding: str = "utf-8") -> None:
    if _HAS_S3 and hasattr(_json_proxy, "write_text"):
        _json_proxy.write_text(str(p), text, encoding=encoding)  # type: ignore
        return
    fs_mkdirs(p.parent)
    p.write_text(text, encoding=encoding)

def fs_stat_mtime(p: Path) -> Optional[float]:
    """S3-aware mtime (seconds since epoch) if available, else None if not provided."""
    if _HAS_S3 and hasattr(_json_proxy, "stat"):
        try:
            st = _json_proxy.stat(str(p))
            if hasattr(st, "st_mtime"):
                return float(st.st_mtime)  # type: ignore
            if isinstance(st, dict) and "st_mtime" in st:
                return float(st["st_mtime"])
            # Some backends expose ISO timestamps; ignore if not numeric
        except Exception:
            return None
    try:
        return p.stat().st_mtime
    except Exception:
        return None

def _jp_list_projects() -> List[str]:
    """Archive-style: list project roots via json_proxy if available."""
    if _HAS_S3 and hasattr(_json_proxy, "list_projects"):
        try:
            return sorted(list(_json_proxy.list_projects() or []))
        except Exception:
            pass
    # Fallback to local scan
    root = resolve_path(Path(), "project_root")
    if fs_exists(root):
        return sorted([p.name for p in fs_iterdir(root) if fs_is_dir(p)])
    return ["demo", "test_project"]

def _jp_list_dirnames(root: Path) -> List[str]:
    """Archive-style: list subdirectories under a given root path."""
    if _HAS_S3 and hasattr(_json_proxy, "list_dirnames"):
        try:
            return sorted(list(_json_proxy.list_dirnames(str(root)) or []))
        except Exception:
            pass
    try:
        return sorted([p.name for p in fs_iterdir(root) if fs_is_dir(p)])
    except Exception:
        return []

# -----------------------------------------------------------------------------
# Project imports that are already S3-aware for JSON/text artifacts
# -----------------------------------------------------------------------------
from api.manifest.resolver import resolve_path, resolve_data_dump_contents
from core.handlers.adverbs.reference_list_adverb import ReferenceListAdverb
from core.handlers.adverbs.reference_adverb import ReferenceAdverb
from core.handlers.adverbs.attribute_adverb import AttributeAdverb
from core.handlers.adverbs.tag_adverb import TagAdverb
from core.handlers.adverbs.picture_adverb import PictureAdverb

from core.core_view_runlog import (
    prepare_runlog,
    summarize_status_as_fraction,
    collect_headers,
    flatten_entries,
)
from core.status import get_status_breakdown_core
from core.core_data_dump import prepare_data_dump
from core.id_generator import generate_autogenerated_id

from api.i_o import (
    load_schema,
    load_data,
    load_override,
    load_verb_group_log,
    get_verb_group_log_config,
    list_verb_groups,
    is_file_empty,
    get_noun_items,
    get_noun_schema,
    get_verb_schema,
    get_adjective_schema,
    save_json,
    save_override,
    resolve_run_id_to_test_type,  # Used by grid_save and others
    resolve_verb_group_from_test_type,
    rewrite_jsonl,  # Used by grid_save
    resolve_reference_noun_from_verb,
    io_list_projects,
)

# --- Imports Added from Noun Workbench for RDS-aware grid_save ---
from gui.noun_workbench_gui import (
    get_project_path,    # Used by grid_save and more
    debug,               # Used by dbg logger
    _open_db,            # Used by grid_save
    _DBHandle,           # Type hint for _open_db
    _PSYCOPG_AVAILABLE,  # Used by grid_save
    _sanitize_table_name,  # Used by grid_save
    _ensure_table_sqlite,  # Used by grid_save
    _resolve_pg_table_and_primary, # Used by grid_save
    _insert_row_pg,      # (Imported but not used directly here)
    _insert_row_sqlite,  # Used by grid_save
)

HANDLERS = {
    "ReferenceList": ReferenceListAdverb,
    "Reference":     ReferenceAdverb,
    "Attribute":     AttributeAdverb,
    "Tag":           TagAdverb,
    "Picture":       PictureAdverb,
}

router = APIRouter()

# -----------------------------------------------------------------------------
# Small S3-safe helpers
# -----------------------------------------------------------------------------

def _is_within(parent: Path, child: Path) -> bool:
    """
    FS/S3-safe containment check. Uses Path.relative_to (no FS access).
    """
    try:
        child.relative_to(parent)
        return True
    except Exception:
        return False

# -----------------------------------------------------------------------------
# Shared helpers
# -----------------------------------------------------------------------------

# ---- Linear-status refresh helper -------------------------------------------
def _ensure_linear_status_fresh(pp: Path, group: str, run_id: str) -> None:
    try:
        from core.status import get_linear_status_progress
        get_linear_status_progress(pp, str(run_id))
    except Exception as e:
        debug("[linear][ensure_fresh][error]", {"run_id": run_id, "err": repr(e)})

def _data_dumps_dir(project_path: Path, verb_group: str) -> Path:
    return resolve_path(project_path, "verbs_dir") / verb_group / "data_dumps"

def _list_run_ids(project_path: Path, verb_group: str) -> List[str]:
    base = _data_dumps_dir(project_path, verb_group)
    if not fs_exists(base):
        return []
    return sorted([p.name for p in fs_iterdir(base) if fs_is_dir(p)])

def _normalize_to_grid(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict) and "headers" in data and "rows" in data:
        return {"headers": list(data["headers"]), "rows": list(data["rows"])}
    if isinstance(data, list):
        headers = sorted({k for r in data if isinstance(r, dict) for k in r.keys()})
        return {"headers": headers, "rows": data}
    if isinstance(data, dict):
        return {"headers": list(data.keys()), "rows": [data]}
    return {"headers": [], "rows": []}

def _resolve_reference_for_field(project_path: Path, noun_type: str, field: str) -> Optional[dict]:
    noun_schema = get_noun_schema(project_path, noun_type) or {}
    fields_cfg = (noun_schema.get("fields", {}) or {})
    fcfg = fields_cfg.get(field, {})
    if not isinstance(fcfg, dict):
        return None
    is_adjective = fcfg.get("type") == "adjective"
    is_reference = (fcfg.get("adjective_class") or "").lower() == "reference"
    if not (is_adjective and is_reference):
        return None

    adj_schema = get_adjective_schema(project_path, field, applies_to=noun_type) or {}
    ref_noun = adj_schema.get("reference_noun")
    if not ref_noun:
        return None

    target_schema = get_noun_schema(project_path, ref_noun) or {}
    target_pid = (
        target_schema.get("primary_id_field")
        or target_schema.get("primary_id")
        or "id"
    )
    filters = adj_schema.get("filters", {}) or {}
    return {
        "adjective": field,
        "reference_noun": str(ref_noun),
        "target_primary_id": str(target_pid),
        "filters": filters,
    }

def _build_noun_items_map(project_path: Path, noun_types: list[str]) -> dict[str, list[dict]]:
    out = {}
    for nt in noun_types:
        try:
            out[nt] = get_noun_items(project_path, nt)
        except Exception:
            out[nt] = []
    return out

def _group_pid_field(project_path: Path, verb_group: str) -> str:
    cfg = get_verb_group_log_config(project_path, verb_group) or {}
    pid = (
        cfg.get("primary_id")
        or cfg.get("primaryId")
        or cfg.get("primary_id_field")
        or "run_ID"
    )
    return str(pid)

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
                    except Exception:
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
        print(f"!!! ERROR in get_runlog: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error loading runlog: {e}")

@router.get("/runlog/{project}/{verb_group}/{run_id}/dump")
def get_data_dump(project: str, verb_group: str, run_id: str):
    debug(f"[dump] start | project={project}, verb_group={verb_group}, run_id={run_id}")
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
            data_entry_data = []

        try:
            status_data = load_data(status_path) or {}
        except Exception:
            status_data = {}

        try:
            adverb_data = load_data(adverb_path) or {}
        except Exception:
            adverb_data = {}

        verb_schemas = load_schema(project_path, "verb")
        noun_schemas = load_schema(project_path, "noun")

        log_entries = load_verb_group_log(project_path, verb_group)
        run_entry = next((e for e in log_entries if str(e.get(pid_field)) == str(run_id)), None)
        if not run_entry:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found in {verb_group} log")

        verb_key = run_entry.get("test_type") or run_entry.get("verb")
        verb_def = verb_schemas.get(verb_key, {})

        raw_inputs = verb_def.get("data_entry_schema", {}).get("raw_data_inputs", [])

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
        raise HTTPException(status_code=500, detail=f"Error loading data dump: {e}")

@router.get("/runlog_data_dump/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception as e:
        # Optional: return empty list on failure instead of 500
        print(f"[list_projects] list_projects failed: {e!r}")
        return []

@router.get("/runlog_data_dump/verb_groups/{project}")
def list_verb_groups_for_project(project: str):
    try:
        project_path = get_project_path(project)
        # Prefer the already S3-aware api.i_o implementation
        return list_verb_groups(project_path)
    except Exception:
        # Fallback: list directories under verbs/
        try:
            project_path = get_project_path(project)
            verbs_dir = project_path / "verbs"
            return _jp_list_dirnames(verbs_dir)
        except Exception:
            return ["main", "test"]

# -----------------------------------------------------------------------------
# Overrides editor endpoints (primary-id aware where relevant)
# -----------------------------------------------------------------------------

@router.post("/runlog/{project}/{group}/{run_id}/override/update")
def update_conjunctions(project: str, group: str, run_id: str, payload: dict = Body(...)):
    project_path = get_project_path(project)

    status_file = resolve_path(project_path, "status_file", verb_group=group, run_id=run_id)
    if not fs_exists(status_file):
        raise HTTPException(status_code=404, detail=f"Status file not found: {status_file}")

    current_status = load_data(status_file) or {}
    incoming = payload.get("overrides", []) or []
    current_status["conjunctions"] = incoming
    save_json(status_file, current_status)

    override_path = resolve_path(project_path, "override_file")
    existing = load_override(project_path)  # list[dict] or []
    verb = resolve_run_id_to_test_type(project_path, run_id) or payload.get("verb")

    new_rows = []
    for row in incoming:
        entry = dict(row)
        entry.setdefault("run", run_id)
        if verb:
            entry.setdefault("verb", verb)
        new_rows.append(entry)

    kept = [row for row in existing if str(row.get("run")) != str(run_id)]
    updated = kept + new_rows
    save_override(project_path, updated)

    return {
        "status": "success",
        "status_conjunctions": len(incoming),
        "override_rows_written": len(new_rows),
        "override_file": str(override_path),
    }

@router.get("/runlog/{project}/{group}/{run_id}/override")
def get_conjunctions(project: str, group: str, run_id: str):
    project_path = get_project_path(project)
    pid_field = _group_pid_field(project_path, group)

    status_file = resolve_path(project_path, "status_file", verb_group=group, run_id=run_id)
    status_data = load_data(status_file) or {}
    conjunctions = status_data.get("conjunctions", [])

    entries = load_verb_group_log(project_path, group)
    run = next((e for e in entries if str(e.get(pid_field)) == str(run_id)), None)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found in {group}")

    verb_key = run.get("test_type") or run.get("verb")
    verb_types = load_schema(project_path, "verb")
    verb_def = verb_types.get(verb_key, {}) or {}

    available = []
    for item in verb_def.get("status_values", []):
        if isinstance(item, dict):
            available.append({
                "type": item.get("name") or item.get("type") or "Unknown",
                "status": item.get("status") or "Exception",
                "fields": item.get("fields", [])
            })
        else:
            available.append({"type": str(item), "status": "Exception", "fields": []})

    return {
        "conjunctions": conjunctions,
        "available_types": available,
        "verb": verb_key
    }

# -----------------------------------------------------------------------------
# Status.json endpoints (linear + full schema)
# -----------------------------------------------------------------------------

def _summarize_steps(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(steps)
    completed = sum(1 for s in steps if bool(s.get("completed")))
    first_incomplete_idx: Optional[int] = next(
        (i for i, s in enumerate(steps) if not bool(s.get("completed"))),
        None
    )
    first_incomplete: Optional[Dict[str, Any]] = None
    if first_incomplete_idx is not None:
        s = steps[first_incomplete_idx]
        first_incomplete = {
            "index": first_incomplete_idx,
            "id": s.get("id"),
            "type": s.get("type"),
            "label": s.get("label"),
            "required": bool(s.get("required", True)),
            "source": s.get("source"),
            "completed": bool(s.get("completed", False)),
            "reason": s.get("reason"),
        }

    return {
        "mode": "linear",
        "steps": steps,
        "steps_total": total,
        "steps_completed": completed,
        "progress": f"{completed}/{total}",
        "first_incomplete": first_incomplete,
        "linear_steps_total": total,
        "linear_steps_completed": completed,
        "linear_progress": f"{completed}/{total}",
        "details": {
            "mode": "linear",
            "steps_total": total,
            "steps_completed": completed,
            "progress_text": f"{completed}/{total}",
            "first_incomplete": first_incomplete,
        },
    }

@router.get("/runlog/{project}/{group}/{run_id}/status.json")
def get_full_status(project: str, group: str, run_id: str):
    project_path = get_project_path(project)
    status_path = resolve_path(project_path, "status_file", verb_group=group, run_id=run_id)

    if not fs_exists(status_path):
        raise HTTPException(status_code=404, detail=f"Status.json not found for run {run_id}")

    try:
        data = load_data(status_path) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not read Status.json: {e}")

    verb_name = resolve_run_id_to_test_type(project_path, run_id)
    if verb_name:
        verb_schema = get_verb_schema(project_path, verb_name) or {}
        ls = (verb_schema or {}).get("linear_status") or {}
        if ls and ls.get("enabled") and (ls.get("steps") or []):
            from core.status import get_linear_status_progress
            _ = get_linear_status_progress(project_path, run_id)
            try:
                data = load_data(status_path) or data
            except Exception as e:
                debug(f"Failed to re-read status file: {e}")

    steps_source = data.get("linear_status") or data
    steps: List[Dict[str, Any]] = list((steps_source or {}).get("steps") or [])

    if steps:
        summary = _summarize_steps(steps)
        normalized = {**summary, **data}
        return JSONResponse(content=normalized)

    return JSONResponse(content=data)

@router.get("/runlog/{project}/{verb_group}/{run_id}/status")
def get_status_breakdown(project: str, verb_group: str, run_id: str):
    try:
        project_path = get_project_path(project)
        breakdown = get_status_breakdown_core(project_path, str(run_id))
        if breakdown.get("mode") == "linear":
            from core.status import get_linear_status_progress
            linear_details = get_linear_status_progress(project_path, str(run_id)) or {}
            breakdown["details"] = linear_details
        return {"ok": True, "status": breakdown}
    except Exception as e:
        debug(f"[status-refresh] error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error loading status for {run_id}: {e}")

# -----------------------------------------------------------------------------
# Adverbs
# -----------------------------------------------------------------------------

@router.get("/runlog/{project}/{group}/{run_id}/adverb")
def get_adverbs(project: str, group: str, run_id: str):
    project_path = get_project_path(project)

    dbg = {"steps": [], "errors": []}
    def stamp(msg, **extra):
        entry = {"msg": msg, **extra}
        dbg["steps"].append(entry)
        return entry

    adverb_file = resolve_path(project_path, "adverb_file", verb_group=group, run_id=run_id)
    current = load_data(adverb_file) or {}
    stamp("loaded_adverb_file", path=str(adverb_file), has_file=fs_exists(adverb_file), current_keys=list(current.keys()))

    pid_field = _group_pid_field(project_path, group)
    entries = load_verb_group_log(project_path, group) or []
    run = next((e for e in entries if str(e.get(pid_field)) == str(run_id)), None)
    if not run:
        stamp("run_not_found_in_group_log", group=group, pid_field=pid_field, run_id=run_id)
        verb_name = None
        verb_def = {}
        schema_map = {}
    else:
        verb_name = run.get("test_type") or run.get("verb")
        stamp("resolved_verb_name_from_group_log", verb=verb_name, group=group)

        verb_types = load_schema(project_path, "verb") or {}
        verb_def = verb_types.get(verb_name, {}) if verb_name else {}
        schema_map = verb_def.get("adverb_schema", {}) or {}
        stamp("loaded_adverb_schema", schema_keys=list(schema_map.keys()))

    ui = {}
    for key, entry in schema_map.items():
        entry = dict(entry)
        entry.setdefault("adverb", key)
        adverb_class = entry.get("adverb_class")
        stamp("process_adverb", key=key, adverb_class=adverb_class)

        cls = HANDLERS.get(adverb_class)
        if not cls:
            ui[key] = {"kind": "scalar", "field_type": entry.get("field_type", "string")}
            stamp("no_handler_fallback_scalar", key=key)
            continue

        try:
            try:
                handler = cls(entry)
            except TypeError:
                handler = cls(data=entry)
            stamp("handler_instantiated", key=key, handler=str(cls.__name__))
        except Exception as e:
            dbg["errors"].append({"key": key, "where": "ctor", "err": repr(e)})
            ui[key] = {"kind": "scalar", "field_type": entry.get("field_type", "string")}
            continue

        try:
            if adverb_class == "ReferenceList":
                ref_nouns = handler.get_reference_noun() or entry.get("reference_nouns") or []
                if isinstance(ref_nouns, str):
                    ref_nouns = [ref_nouns]

                all_vals: list[str] = []
                for nt in ref_nouns:
                    n_schema = get_noun_schema(project_path, nt) or {}
                    pid = (n_schema.get("primary_id_field")
                           or n_schema.get("primary_id")
                           or None)
                    if not pid:
                        continue
                    pid_u = pid.replace(" ", "_")
                    pid_s = pid.replace("_", " ")
                    rows = get_noun_items(project_path, nt)
                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        val = r.get(pid) or r.get(pid_u) or r.get(pid_s)
                        if val:
                            all_vals.append(str(val).strip())

                uniq = sorted({v for v in all_vals if v})
                opts = [{"value": v, "label": v} for v in uniq]
                ui[key] = {"kind": "ref_list", "options": opts}

            elif adverb_class == "Reference":
                ref = handler.get_reference_noun() or entry.get("reference_noun") or ""
                if isinstance(ref, list):
                    ref = ref[0] if ref else ""
                if not ref:
                    ui[key] = {"kind": "ref", "options": []}
                    continue

                n_schema = get_noun_schema(project_path, ref) or {}
                pid = (n_schema.get("primary_id_field")
                       or n_schema.get("primary_id")
                       or None)
                if not pid:
                    ui[key] = {"kind": "ref", "options": []}
                    continue

                pid_u = pid.replace(" ", "_")
                pid_s = pid.replace("_", " ")

                filters = entry.get("filters", {}) or {}
                def _passes(rec: dict) -> bool:
                    if not filters:
                        return True
                    for fk, fv in filters.items():
                        if rec.get(fk) != fv and rec.get(fk.replace(" ", "_")) != fv:
                            return False
                    return True

                rows = get_noun_items(project_path, ref)

                seen = set()
                opts: list[dict] = []
                for r in rows:
                    if not isinstance(r, dict) or not _passes(r):
                        continue
                    val = r.get(pid) or r.get(pid_u) or r.get(pid_s)
                    if not val:
                        continue
                    sval = str(val).strip()
                    if sval and sval not in seen:
                        seen.add(sval)
                        opts.append({"value": sval, "label": sval})

                opts.sort(key=lambda x: x["label"])
                ui[key] = {"kind": "ref", "options": opts}

            elif adverb_class == "Tag":
                opts = []
                try:
                    for o in handler.get_valid_options() or []:
                        opts.append({
                            "value": o.get("value"),
                            "label": o.get("value"),
                            "title": o.get("explanation", ""),
                            "display_in_label": bool(o.get("display_in_label")),
                        })
                except Exception as e:
                    dbg["errors"].append({"key": key, "where": "get_valid_options", "err": repr(e)})
                ui[key] = {"kind": "tag", "options": opts}

            elif adverb_class == "Attribute":
                ui[key] = {"kind": "scalar", "field_type": entry.get("field_type", "string")}

            elif adverb_class == "Picture":
                ui[key] = {"kind": "picture"}

            else:
                ui[key] = {"kind": "scalar", "field_type": entry.get("field_type", "string")}

        except Exception as e:
            dbg["errors"].append({"key": key, "where": "handler_flow", "err": repr(e)})
            ui[key] = {"kind": "scalar", "field_type": entry.get("field_type", "string")}

    available = [{**dict(entry), "adverb": key} for key, entry in schema_map.items()]

    return {
        "verb": verb_name,
        "adverbs": current,
        "available_types": available,
        "ui": ui,
        "file": str(adverb_file),
        "_debug": dbg,
    }

@router.post("/runlog/{project}/{group}/{run_id}/adverb/update")
def update_adverbs(project: str, group: str, run_id: str, payload: dict = Body(...)):
    project_path = get_project_path(project)

    status_file = resolve_path(project_path, "status_file", verb_group=group, run_id=run_id)
    status_doc = load_data(status_file) or {}
    ls = (status_doc.get("linear_status") or {})
    steps = list(ls.get("steps") or [])
    if steps:
        idx = ls.get("current_index")
        if idx is None:
            idx = next((i for i, s in enumerate(steps) if not bool(s.get("completed"))), len(steps))
        _ = steps[idx] if 0 <= idx < len(steps) else None

    adverb_file = resolve_path(project_path, "adverb_file", verb_group=group, run_id=run_id)
    new_vals = payload.get("adverbs")
    if not isinstance(new_vals, dict):
        raise HTTPException(status_code=400, detail="Body must include 'adverbs' object.")

    save_json(adverb_file, new_vals)
    return {"status": "success", "count": len(new_vals), "file": str(adverb_file)}

# -----------------------------------------------------------------------------
# NEW: Glide Grid endpoints (ported & primary-id agnostic)
# -----------------------------------------------------------------------------

@router.get("/grid/debug/whoami")
def whoami():
    # S3-safe: avoid os.path.getmtime; use fs_stat_mtime wrapper.
    ts = fs_stat_mtime(Path(__file__))
    return {
        "module": __name__,
        "file": __file__,
        "mtime": ts,  # may be None if backend doesn't provide mtime
        "routes_hint": "runlog_data_dump_gui hosts grid endpoints",
    }

@router.get("/grid/runs/{project}/{verb_group}")
def grid_runs(project: str, verb_group: str):
    proj = get_project_path(project)
    return {"project": project, "verb_group": verb_group, "runs": _list_run_ids(proj, verb_group)}

@router.get("/grid/load/{project}/{verb_group}/{run_id}")
def grid_load(project: str, verb_group: str, run_id: str):
    proj = get_project_path(project)
    path = resolve_path(proj, "data_entry", verb_group=verb_group, run_id=run_id)
    try:
        data = load_data(path) or []
    except Exception as e:
        raise HTTPException(404, f"Load error: {e}")
    return _normalize_to_grid(data)

from core.handlers.core_noun import _sanitize_table_name
import sqlite3

@router.post("/gui/grid/save/{project}/{verb_group}/{run_id}")
def grid_save(
    project: str,
    verb_group: str,
    run_id: str,
    payload: dict = Body(...),
    storage_backend: str = "both"  # "jsonl", "sql", or "both"
):
    proj_path = get_project_path(project) 
    headers = list(payload.get("headers") or [])
    rows    = list(payload.get("rows") or [])

    data_entry_path = resolve_path(proj_path, "data_entry", verb_group=verb_group, run_id=run_id)
    dump_dir = data_entry_path.parent
    fs_mkdirs(dump_dir)
    log_path = dump_dir / "grid_save_debug.log"

    rid = datetime.utcnow().strftime("%Y%m%dT%H%M%S.%fZ")
    def dbg(msg: str):
        line = f"[grid_save {rid}] {msg}"
        try:
            with fs_open_writebin(log_path) as f:
                f.write((line + "\n").encode("utf-8"))
        except Exception:
            pass
        debug(line)

    dbg(f"start project={project} verb_group={verb_group} run_id={run_id} backend={storage_backend}")
    dbg(f"incoming payload: headers={len(headers)} rows={len(rows)}")

    try:
        existing_shape = None
        try:
            existing_shape = load_data(data_entry_path)
        except Exception:
            existing_shape = None

        if isinstance(existing_shape, dict) and "headers" in existing_shape and "rows" in existing_shape:
            save_json(data_entry_path, {"headers": headers, "rows": rows})
            dbg("DataEntry.json write OK (headers+rows)")
        else:
            save_json(data_entry_path, rows)
            dbg("DataEntry.json write OK (list-only)")
    except Exception as e:
        dbg(f"ERROR writing DataEntry.json: {e!r}")
        raise HTTPException(400, f"Save error: {e}")

    try:
        test_type = resolve_run_id_to_test_type(proj_path, run_id)
        if not test_type:
            dbg("Skipping items/SQL: no test type found for run_id.")
            return {"status": "DataEntry.json saved, items/SQL skipped (no test type)."}

        verb_schema = get_verb_schema(proj_path, test_type) or {}
        noun_type_ref = verb_schema.get("data_entry_schema", {}).get("set_up_inputs", {}).get("noun_type_ref")
        if not noun_type_ref:
            dbg("Skipping items/SQL: no noun_type_ref in verb schema.")
            return {"status": "DataEntry.json saved, items/SQL skipped (no noun reference)."}

        noun_schema = get_noun_schema(proj_path, noun_type_ref) or {}
        pid_field = noun_schema.get("primary_id_field") or "id"
        pkey = noun_schema.get("primary_id_field") or "_rowid"

        current_run_items_with_id = []
        for r in rows:
            if isinstance(r, dict) and str(r.get(pid_field, "")).strip():
                r["_runID"] = run_id
                current_run_items_with_id.append(r)
        
        dbg(f"Resolved noun '{noun_type_ref}'. Found {len(current_run_items_with_id)} items with a primary ID to save.")

    except Exception as e:
        dbg(f"ERROR during schema lookup: {e!r}")
        raise HTTPException(status_code=500, detail=f"Schema resolution failed: {e}")

    if storage_backend in ["jsonl", "both"]:
        try:
            items_path = resolve_path(proj_path, "noun_items", noun_type=noun_type_ref)
            fs_mkdirs(items_path.parent)

            try:
                existing_items = get_noun_items(proj_path, noun_type_ref)
            except FileNotFoundError:
                existing_items = []

            other_run_items = [item for item in existing_items if isinstance(item, dict) and item.get("_runID") != run_id]
            final_list = other_run_items + current_run_items_with_id

            reordered_final_list = []
            for item in final_list:
                if not isinstance(item, dict):
                    reordered_final_list.append(item)
                    continue
                ordered_keys = [pid_field] if pid_field in item else []
                normal_keys = sorted([k for k in item if k != pid_field and not k.startswith('_')])
                underscore_keys = sorted([k for k in item if k.startswith('_')])
                ordered_keys.extend(normal_keys + underscore_keys)
                reordered_item = {k: item[k] for k in ordered_keys}
                reordered_final_list.append(reordered_item)

            rewrite_jsonl(items_path, reordered_final_list)
            dbg("items.jsonl updated")
        except Exception as e:
            dbg(f"ERROR updating items.jsonl: {e!r}\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"items.jsonl update failed: {e!r}")

    if storage_backend in ["sql", "both"]:
        dbg("Starting SQL update...")
        try:
            with _open_db(proj_path) as db:
                if db.kind == "pg" and _PSYCOPG_AVAILABLE:
                    table_name, primary_key_col = _resolve_pg_table_and_primary(db, project, noun_type_ref, noun_schema)
                    dbg(f"[pg] Using table: {table_name} (pkey: {primary_key_col})")

                    with db.conn.cursor() as cur:
                        cur.execute("""
                            SELECT column_name FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = %s
                        """, (table_name,))
                        columns = {row[0] for row in cur.fetchall()}
                        lower_columns = {c.lower() for c in columns}

                        for needed in (pid_field, "_runID"):
                            if needed not in columns and needed.lower() not in lower_columns:
                                dbg(f"[pg] Adding missing column '{needed}' to '{table_name}'")
                                cur.execute(f'ALTER TABLE public."{table_name}" ADD COLUMN "{needed}" TEXT')
                                columns.add(needed)
                                lower_columns.add(needed.lower())

                        dbg(f"[pg] Deleting old rows for run_id '{run_id}'")
                        cur.execute(f'DELETE FROM public."{table_name}" WHERE "_runID" = %s', (run_id,))
                        dbg(f"[pg] Deleted {cur.rowcount} rows.")

                        cur.execute("""
                            SELECT column_default 
                            FROM information_schema.columns 
                            WHERE table_schema = 'public' 
                            AND table_name = %s 
                            AND column_name = %s
                        """, (table_name, primary_key_col))
                        col_default_row = cur.fetchone()
                        is_auto_increment = col_default_row and col_default_row[0] and 'nextval' in col_default_row[0]

                        if is_auto_increment:
                            cur.execute("SELECT pg_get_serial_sequence(%s, %s)", (f'public."{table_name}"', primary_key_col))
                            seq_row = cur.fetchone()
                            seq_name = seq_row[0] if seq_row else None

                            if seq_name:
                                cur.execute(f'SELECT COALESCE(MAX("{primary_key_col}"), 0) + 1 FROM public."{table_name}"')
                                next_val = cur.fetchone()[0] or 1
                                cur.execute("SELECT setval(%s, %s, false)", (seq_name, int(next_val)))

                            cleaned_rows = []
                            for item in current_run_items_with_id:
                                if not item:
                                    continue
                                clean = {
                                    k: (None if v == "" else v)
                                    for k, v in item.items()
                                    if k != primary_key_col
                                }
                                clean = {k: v for k, v in clean.items() if (k in columns) or (k.lower() in lower_columns)}
                                if clean:
                                    cleaned_rows.append(clean)
                        else:
                            cleaned_rows = []
                            for item in current_run_items_with_id:
                                if not item:
                                    continue
                                clean = {k: (None if v == "" else v) for k, v in item.items()}
                                clean = {k: v for k, v in clean.items() if (k in columns) or (k.lower() in lower_columns)}
                                if clean:
                                    cleaned_rows.append(clean)

                        dbg(f"[pg] Inserting {len(cleaned_rows)} rows.")
                        for clean in cleaned_rows:
                            cols = list(clean.keys())
                            col_names = ", ".join([f'"{c}"' for c in cols])
                            placeholders = ", ".join(["%s"] * len(cols))
                            sql = f'INSERT INTO public."{table_name}" ({col_names}) VALUES ({placeholders})'
                            try:
                                cur.execute(sql, [clean[c] for c in cols])
                            except Exception as e:
                                dbg(f"[pg] FAILED to insert row (cols={cols}): {e!r}")
                                dbg(f"[pg] Values: {clean}")
                                raise

                        dbg(f"[pg] Insert complete.")

                else:
                    if db.kind == "pg":
                        dbg("[sqlite] Warning: psycopg not found, falling back to SQLite.")
                    
                    table_name = _sanitize_table_name(noun_type_ref)
                    _ensure_table_sqlite(db.conn, noun_type_ref, noun_schema)
                    dbg(f"[sqlite] Using table: {table_name}")
                    
                    cursor = db.conn.cursor()
                    cursor.execute(f'PRAGMA table_info("{table_name}")')
                    columns = {row[1] for row in cursor.fetchall()}
                    for needed in [pid_field, "_runID"]:
                        if needed not in columns:
                            dbg(f"[sqlite] Adding missing column '{needed}' to '{table_name}'")
                            cursor.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{needed}" TEXT')

                    dbg(f"[sqlite] Deleting old rows for run_id '{run_id}'")
                    cursor.execute(f'DELETE FROM "{table_name}" WHERE "_runID" = ?', (run_id,))
                    dbg(f"[sqlite] Deleted {cursor.rowcount} rows.")

                    for item in current_run_items_with_id:
                        _insert_row_sqlite(db.conn, table_name, item)
                    dbg(f"[sqlite] Inserted {len(current_run_items_with_id)} new rows.")
            
            dbg("SQL update complete and committed.")

        except Exception as e:
            error_detail = f"SQL database update failed: {e!r}"
            dbg(f"ERROR: {error_detail}\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=error_detail)

    return {
        "status": "Save successful",
        "rows_written_to_items": len(current_run_items_with_id),
        "storage_backend": storage_backend
    }

@router.get("/grid/dump/{project}/{verb_group}/{run_id}")
def grid_dump(project: str, verb_group: str, run_id: str):
    proj = get_project_path(project)
    try:
        dump = resolve_data_dump_contents(proj, verb_group=verb_group, run_id=run_id)
    except Exception as e:
        raise HTTPException(404, str(e))

    def _p(x: Path) -> str: return str(x)
    files = {
        "data_entry": _p(dump["files"]["data_entry"]),
        "status": _p(dump["files"]["status"]),
        "adverbs": _p(dump["files"]["adverbs"]),
        "other_files": {k: _p(v) for k, v in dump["files"]["other_files"].items()},
    }
    folders = {k: {"path": _p(v["path"]), "files": [_p(f) for f in v["files"]]} for k, v in dump["folders"].items()}
    return {"project": project, "verb_group": verb_group, "run_id": run_id, "files": files, "folders": folders}

@router.get("/grid/noun_info/{project}/{noun_type}")
def grid_noun_info(project: str, noun_type: str):
    proj = get_project_path(project)
    schema = get_noun_schema(proj, noun_type)
    if not schema:
        raise HTTPException(404, f"noun_type not found: {noun_type}")

    fields = schema.get("fields", {}) or {}
    primary = (
        schema.get("primary_id_field")
        or schema.get("primary_id")
        or (next(iter(fields.keys()), "id"))
    )
    headers_from_schema = list(fields.keys())

    autogenerate_id = bool(schema.get("autogenerate_id"))
    picture_fields = [
        name
        for name, cfg in (fields or {}).items()
        if isinstance(cfg, dict)
        and cfg.get("type") == "adjective"
        and str(cfg.get("adjective_class", "")).lower() == "picture"
    ]

    return {
        "primary_id": primary,
        "headers_from_schema": headers_from_schema,
        "autogenerate_id": autogenerate_id,
        "picture_fields": picture_fields,
    }

@router.get("/grid/reference_adjectives/{project}/{noun_type}")
def grid_reference_adjectives(project: str, noun_type: str):
    proj = get_project_path(project)
    noun_schema = get_noun_schema(proj, noun_type)
    if not noun_schema:
        raise HTTPException(404, f"noun_type not found: {noun_type}")

    fields = noun_schema.get("fields", {}) or {}
    detail: Dict[str, Dict[str, Any]] = {}

    for field, cfg in fields.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("type") == "adjective" and (cfg.get("adjective_class") or "").lower() == "reference":
            ref = _resolve_reference_for_field(proj, noun_type, field)
            if ref:
                detail[field] = {
                    "reference_noun": ref["reference_noun"],
                    "target_primary_id": ref["target_primary_id"],
                    "filters": ref["filters"],
                }

    return {
        "project": project,
        "noun_type": noun_type,
        "names": list(detail.keys()),
        "detail": detail,
    }

@router.get("/grid/ref_options/{project}/{noun_type}/{field}")
def grid_ref_options(project: str, noun_type: str, field: str):
    proj = get_project_path(project)
    ref = _resolve_reference_for_field(proj, noun_type, field)
    if not ref:
        return {"options": []}

    target_noun = ref["reference_noun"]
    target_pid = ref["target_primary_id"]
    filters = ref.get("filters", {}) or {}

    try:
        items = get_noun_items(proj, target_noun)
    except FileNotFoundError:
        return {"options": []}

    def pass_filters(row: dict) -> bool:
        for k, v in filters.items():
            if isinstance(v, (str, int, float, bool)) and row.get(k) != v:
                return False
        return True

    seen = set()
    options: List[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if not pass_filters(it):
            continue
        cand_keys = [target_pid, target_pid.replace("_", " "), target_pid.replace(" ", "_")]
        val_raw = None
        for ck in cand_keys:
            if ck in it:
                val_raw = it.get(ck)
                break
        val = str(val_raw or "").strip()
        if val and val not in seen:
            seen.add(val)
            options.append(val)

    return {"options": options}

@router.post("/grid/generate_id/{project}/{noun_type}")
def grid_generate_id(project: str, noun_type: str, body: dict = Body(...)):
    proj = get_project_path(project)
    schema = get_noun_schema(proj, noun_type)
    if not schema:
        raise HTTPException(404, f"noun_type not found: {noun_type}")

    existing = set(map(str, body.get("existing_ids", [])))
    new_id = generate_autogenerated_id(
        noun_type_name=noun_type,
        noun_schema=schema,
        noun_types_path=proj / "noun_types.json",
        existing_ids=existing,
    )
    return {"id": new_id}

@router.get("/grid/retest_options/{project}/{verb_group}/{run_id}")
def grid_retest_options(project: str, verb_group: str, run_id: str):
    proj = get_project_path(project)

    curr_test_type = resolve_run_id_to_test_type(proj, run_id)
    if not curr_test_type:
        return {"options": []}

    verb_types = load_schema(proj, "verb")
    verb_schema = verb_types.get(curr_test_type) or {}
    status_values = verb_schema.get("status_values", []) or []

    run_ref_labels: set[str] = set()
    for status in status_values:
        fields = status.get("fields", [])
        for f in fields:
            if isinstance(f, dict) and f.get("type") == "reference" and f.get("reference_noun") == "Run":
                label = f.get("label")
                if label:
                    run_ref_labels.add(label)

    if not run_ref_labels:
        return {"options": []}

    overrides = load_override(proj)
    linked_runs: set[str] = set()
    for row in overrides:
        if str(row.get("run")) != str(run_id):
            continue
        if row.get("verb") and row.get("verb") != curr_test_type:
            continue
        for label in run_ref_labels:
            val = row.get(label)
            if isinstance(val, list):
                for r in val:
                    if isinstance(r, str) and r.strip():
                        linked_runs.add(r.strip())
            elif isinstance(val, str) and val.strip():
                linked_runs.add(val.strip())

    if not linked_runs:
        return {"options": []}

    noun_types = load_schema(proj, "noun")

    def _noun_for_run(rid: str) -> tuple[Optional[str], Optional[str]]:
        test_type = resolve_run_id_to_test_type(proj, rid)
        if not test_type:
            return (None, None)
        v_schema = verb_types.get(test_type) or {}
        noun_type_ref = (
            v_schema.get("data_entry_schema", {})
            .get("set_up_inputs", {})
            .get("noun_type_ref")
        )
        if not noun_type_ref:
            return (None, None)
        n_schema = noun_types.get(noun_type_ref) or {}
        pid = n_schema.get("primary_id_field") or n_schema.get("primary_id") or "id"
        return (noun_type_ref, pid)

    options: set[str] = set()
    for prev_run in sorted(linked_runs):
        noun_type_ref, pid_field = _noun_for_run(prev_run)
        if not noun_type_ref or not pid_field:
            continue
        try:
            items = get_noun_items(proj, noun_type_ref)
        except FileNotFoundError:
            continue
        for it in items:
            if str(it.get("_runID", "")) == str(prev_run):
                pid = it.get(pid_field)
                if pid:
                    options.add(str(pid))

    return {"options": sorted(options)}

# -----------------------------------------------------------------------------
# Reference options for overrides UI (Run noun primary-id aware)
# -----------------------------------------------------------------------------

@router.get("/conjunction/reference_options/{project}/{noun_type}")
def get_reference_options(project: str, noun_type: str, request: Request):
    project_root = resolve_path(Path(), "project_root")
    project_path = project_root / project

    qp = request.query_params
    verb_group = qp.get("verb_group")
    verb_name  = qp.get("verb_name")
    statuses   = qp.getlist("status")

    if noun_type == "Run":
        if not verb_group:
            return {"options": []}
        pid_field = _group_pid_field(project_path, verb_group)
        runs = load_verb_group_log(project_path, verb_group)
        if verb_name:
            runs = [r for r in runs if r.get("test_type") == verb_name]
        if statuses:
            runs = [r for r in runs if r.get("status") in statuses]
        options = [{"label": str(r.get(pid_field)), "value": str(r.get(pid_field))}
                   for r in runs if r.get(pid_field) is not None]
        return {"options": options}

    try:
        items = get_noun_items(project_path, noun_type)
        noun_schema = get_noun_schema(project_path, noun_type)
    except FileNotFoundError:
        return {"options": []}

    pid_field = (noun_schema or {}).get("primary_id_field", "id")

    ignore_keys = {"verb_group", "verb_name", "status"}
    filters: Dict[str, List[str]] = {}
    for k, v in qp.multi_items():
        if k in ignore_keys:
            continue
        filters.setdefault(k, []).append(v)

    def passes_filters(rec: dict[str, Any]) -> bool:
        if not filters:
            return True
        for k, vals in filters.items():
            if str(rec.get(k)) not in {str(x) for x in vals}:
                return False
        return True

    options = [{"label": str(rec.get(pid_field)), "value": str(rec.get(pid_field))}
               for rec in items if pid_field in rec and passes_filters(rec)]
    return {"options": options}

@router.get("/schema/verb/{project}/{verb_name}")
def api_get_verb_schema(project: str, verb_name: str):
    try:
        project_root = resolve_path(Path(), "project_root")
        project_path = project_root / project
        schema = get_verb_schema(project_path, verb_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not schema:
        raise HTTPException(status_code=404, detail=f"Verb {verb_name} not found")
    debug("verb_schema", {"project": project, "verb_name": verb_name, "has_schema": bool(schema)})
    return schema

# -----------------------------------------------------------------------------
# Raw file uploads (per-pocket; zero processing)
# -----------------------------------------------------------------------------

_ALLOWED_EXTS = {
    ".csv", ".xlsx", ".jpeg", ".png", ".docx", ".odt", ".txt", ".pdf", ".html", ".ods", ".xcf"
}

def _validate_filename(name: str) -> str:
    if not name:
        raise HTTPException(status_code=400, detail="Filename is required.")
    base = Path(name).name
    if base != name or ".." in name or base.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    ext = Path(base).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"Extension {ext!r} not allowed.")
    return base

def _validate_pocket(project_path: Path, group: str, run_id: str, pocket: str) -> str:
    if not pocket or "/" in pocket or "\\" in pocket or pocket.startswith(".") or ".." in pocket:
        raise HTTPException(status_code=400, detail="Invalid pocket name.")
    pid_field = _group_pid_field(project_path, group)
    entries = load_verb_group_log(project_path, group) or []
    run = next((e for e in entries if str(e.get(pid_field)) == str(run_id)), None)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found in {group}")

    verb_key = run.get("test_type") or run.get("verb")
    if not verb_key:
        raise HTTPException(status_code=400, detail="Verb not set for this run.")

    verb_types = load_schema(project_path, "verb") or {}
    vdef = verb_types.get(verb_key) or {}
    raw_inputs = (vdef.get("data_entry_schema", {}) or {}).get("raw_data_inputs", []) or []

    if pocket not in raw_inputs:
        raise HTTPException(
            status_code=400,
            detail=f"Pocket {pocket!r} is not declared in raw_data_inputs for verb {verb_key!r}."
        )
    return pocket

def _pocket_dir_for_run(project_path: Path, group: str, run_id: str, pocket: str) -> Path:
    base = resolve_path(project_path, "data_dump_dir", verb_group=group, run_id=run_id)
    pdir = base / pocket
    fs_mkdirs(pdir)
    return pdir

@router.post("/runlog/{project}/{group}/{run_id}/raw/upload")
async def raw_upload_file(
    project: str,
    group: str,
    run_id: str,
    pocket: str = Form(..., description="One of the verb's raw_data_inputs"),
    file: UploadFile = File(...),
    filename: str | None = Form(None),
    overwrite: bool = Form(False),
):
    debug("[raw-upload] start", {
        "project": project, "group": group, "run_id": run_id,
        "pocket": pocket, "filename": filename, "overwrite": overwrite,
        "upload_name": getattr(file, "filename", None),
    })

    project_path = get_project_path(project)
    pocket = _validate_pocket(project_path, group, run_id, pocket)

    try:
        verb_name = resolve_run_id_to_test_type(project_path, run_id)
        verb_schema = get_verb_schema(project_path, verb_name) or {}
        linear_cfg = (verb_schema or {}).get("linear_status") or {}
    except Exception:
        verb_schema = {}
        linear_cfg = {}

    gating_applies = bool(linear_cfg.get("enabled")) and isinstance(linear_cfg.get("steps"), list) and bool(linear_cfg["steps"])

    if gating_applies:
        status_file = resolve_path(project_path, "status_file", verb_group=group, run_id=run_id)
        status_doc = load_data(status_file) or {}
        ls = (status_doc.get("linear_status") or {})
        steps = list(ls.get("steps") or [])

        if steps:
            # Current index = first incomplete (or len(steps) if all done)
            current_index = ls.get("current_index")
            if current_index is None:
                current_index = next((i for i, s in enumerate(steps) if not bool(s.get("completed"))), len(steps))

            def _is_raw(s: dict) -> bool:
                hay = " ".join(str(s.get(k, "")) for k in ("id", "label", "type", "source")).lower()
                hay = hay.replace("_", " ").replace("-", " ")
                return any(k in hay for k in ("raw data", "raw upload", "upload raw", "raw files"))

            # Find the index of the raw-data step
            raw_idx = next((i for i, s in enumerate(steps) if _is_raw(s)), None)

            # Gate rule: allow once we've reached or passed the raw step
            if raw_idx is not None and current_index < raw_idx:
                raise HTTPException(status_code=409, detail="Uploads are locked until the Raw Data step is reached.")

            # If the raw step specifies a pocket source, enforce it only BEFORE the raw step is reached
            step_source = steps[raw_idx].get("source") if raw_idx is not None else None
            if raw_idx is not None and current_index < raw_idx and step_source:
                if str(step_source).strip().lower() != str(pocket).strip().lower():
                    raise HTTPException(
                        status_code=409,
                        detail=f"Uploads for raw data are restricted to the '{step_source}' pocket until that step is reached."
                    )

    chosen_name = filename or (file.filename or "")
    chosen_name = _validate_filename(chosen_name)

    target_dir = _pocket_dir_for_run(project_path, group, run_id, pocket)
    target = (target_dir / chosen_name)

    # Always enforce: only one file per pocket
    # If overwrite is False and a file already exists, raise; if overwrite is True, clean first.
    existing_files = [
        f for f in fs_iterdir(target_dir)
        if fs_is_file(f) and f.suffix.lower() in _ALLOWED_EXTS
    ]

    if existing_files and not overwrite:
        raise HTTPException(
            status_code=409,
            detail="A file already exists in this pocket. Check 'Allow overwrite' to replace it."
        )

    # Remove all existing files before writing the new one
    try:
        for f in existing_files:
            fs_remove(f)
            debug(f"[raw-upload][cleanup] removed old file {f}")
    except Exception as e:
        debug(f"[raw-upload][cleanup] failed: {e!r}")

    # Write the new file with a 3 MB limit
    try:
        with fs_open_writebin(target) as out:
            max_bytes = 3 * 1024 * 1024  # 3 MB limit
            written = 0
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    try:
                        out.close()
                    except Exception:
                        pass
                    fs_remove(target)
                    raise HTTPException(
                        status_code=413,
                        detail="Raw upload exceeds 3 MB limit."
                    )
                out.write(chunk)
    finally:
        try:
            await file.close()
        except Exception:
            pass

    size = fs_stat_size(target) if fs_exists(target) else 0
    debug(f"[raw-upload] success {target} ({size} bytes) overwrite={overwrite}")
    return {
        "status": "ok",
        "pocket": pocket,
        "saved_as": str(target),
        "filename": target.name,
        "bytes": size,
        "relative": f"{pocket}/{target.name}",
    }

@router.delete("/runlog/{project}/{group}/{run_id}/raw/delete")
def raw_delete_file(
    project: str,
    group: str,
    run_id: str,
    pocket: str = Query(..., description="One of the verb's raw_data_inputs"),
    filename: str = Query(..., description="The exact filename to delete within the pocket"),
):
    project_path = get_project_path(project)
    pocket = _validate_pocket(project_path, group, run_id, pocket)

    base = _validate_filename(filename)
    pocket_dir = _pocket_dir_for_run(project_path, group, run_id, pocket)
    target = (pocket_dir / base)

    # Safety: ensure target under pocket_dir (no FS calls)
    if not _is_within(pocket_dir, target) or not fs_exists(target) or not fs_is_file(target):
        raise HTTPException(status_code=404, detail="File not found.")

    try:
        fs_remove(target)
    except Exception as e:
        debug(f"[raw-delete] error removing {target}: {e!r}")
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e!r}")

    debug(f"[raw-delete] removed {target}")
    return {"status": "ok", "pocket": pocket, "deleted": target.name}

@router.get("/runlog/{project}/{group}/{run_id}/raw/list")
def raw_list_files(
    project: str,
    group: str,
    run_id: str,
    pocket: str | None = Query(None, description="Optional pocket to filter; if omitted, lists all pockets"),
):
    project_path = get_project_path(project)

    def list_one(p: str) -> list[dict]:
        p = _validate_pocket(project_path, group, run_id, p)
        pdir = _pocket_dir_for_run(project_path, group, run_id, p)
        out = []
        if fs_exists(pdir):
            for f in sorted(fs_iterdir(pdir), key=lambda x: x.name):
                if fs_is_file(f) and f.suffix.lower() in _ALLOWED_EXTS:
                    out.append({"name": f.name, "bytes": fs_stat_size(f)})
        return out

    if pocket:
        return {"pocket": pocket, "files": list_one(pocket)}

    pid_field = _group_pid_field(project_path, group)
    entries = load_verb_group_log(project_path, group) or []
    run = next((e for e in entries if str(e.get(pid_field)) == str(run_id)), None)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found in {group}")

    verb_key = run.get("test_type") or run.get("verb")
    verb_types = load_schema(project_path, "verb") or {}
    vdef = verb_types.get(verb_key) or {}
    raw_inputs = (vdef.get("data_entry_schema", {}) or {}).get("raw_data_inputs", []) or []

    return {"pockets": {p: list_one(p) for p in raw_inputs}}

# -----------------------------------------------------------------------------
# Interpretation files (one file per "tab" from the verb schema)
# -----------------------------------------------------------------------------
import csv
import re

_name_clean_re = re.compile(r"[^A-Za-z0-9._ -]+")

def _safe_tab(tab: str) -> str:
    if not tab or not isinstance(tab, str):
        raise HTTPException(status_code=400, detail="Tab is required.")
    s = tab.strip()
    s = _name_clean_re.sub("_", s)
    s = s.strip(" ._-")
    if not s or s.startswith(".") or ".." in s or "/" in s or "\\" in s:
        raise HTTPException(status_code=400, detail="Invalid tab name.")
    return s

def _run_dump_dir(project_path: Path, group: str, run_id: str) -> Path:
    p = resolve_path(project_path, "data_dump_dir", verb_group=group, run_id=run_id)
    fs_mkdirs(p)
    return p

def _resolve_verb_name(project_path: Path, run_id: str, explicit: Optional[str] = None) -> Optional[str]:
    if explicit and explicit.strip():
        return explicit.strip()
    return resolve_run_id_to_test_type(project_path, run_id)

def _schema_tabs(project_path: Path, verb_name: Optional[str]) -> List[str]:
    if not verb_name:
        return []
    schema = get_verb_schema(project_path, verb_name) or {}
    tabs = (
        schema.get("data_entry_schema", {})
              .get("interpretation", {})
              .get("tabs", [])
    ) or []
    return [t for t in tabs if isinstance(t, str) and t.strip()]

def _existing_tab_file(dump_dir: Path, tab: str) -> Optional[Path]:
    for ext in _ALLOWED_EXTS:
        p = dump_dir / f"{tab}{ext}"
        if fs_exists(p) and fs_is_file(p):
            return p
    p = fs_glob_first(dump_dir, f"{tab}.*")
    if p and fs_is_file(p) and p.suffix.lower() in _ALLOWED_EXTS:
        return p
    return None

def _delete_all_tab_files(dump_dir: Path, tab: str, keep: Optional[Path] = None) -> List[str]:
    deleted: List[str] = []
    for ext in _ALLOWED_EXTS:
        cand = dump_dir / f"{tab}{ext}"
        # Avoid comparing resolve(); compare names/paths directly
        if keep is not None and str(cand) == str(keep):
            continue
        if fs_exists(cand) and fs_is_file(cand):
            try:
                fs_remove(cand)
                deleted.append(cand.name)
            except Exception as e:
                debug(f"[interpret-delete*] could not remove {cand}: {e!r}")
    return deleted

_NON_INTERP_FILENAMES = {
    "DataEntry.json", "Status.json", "Instructions.md", "adverbs.json", "run_entry.json"
}

@router.get("/runlog/{project}/{group}/{run_id}/interpret/list")
def interpret_list(
    project: str,
    group: str,
    run_id: str,
    tab: str | None = None,
    verb: str | None = Query(None, description="Optional verb/test name; overrides auto-detection"),
):
    project_path = get_project_path(project)
    dump_dir = _run_dump_dir(project_path, group, run_id)

    verb_name = _resolve_verb_name(project_path, run_id, explicit=verb)
    tabs = _schema_tabs(project_path, verb_name)

    existing_by_stem: dict[str, Path] = {}
    try:
        if fs_exists(dump_dir):
            for p in fs_iterdir(dump_dir):
                if not fs_is_file(p):
                    continue
                if p.name in _NON_INTERP_FILENAMES:
                    continue
                if p.suffix.lower() not in _ALLOWED_EXTS:
                    continue
                existing_by_stem.setdefault(p.stem, p)
    except FileNotFoundError:
        pass

    out = {"verb": verb_name, "tabs": tabs, "files": {}}
    for t in tabs:
        if tab and t != tab:
            continue
        f = existing_by_stem.get(t)
        if f:
            try:
                out["files"][t] = {"exists": True, "name": f.name, "bytes": fs_stat_size(f)}
            except FileNotFoundError:
                out["files"][t] = {"exists": False}
        else:
            out["files"][t] = {"exists": False}
    return out

@router.post("/runlog/{project}/{group}/{run_id}/interpret/upload")
async def interpret_upload(
    project: str,
    group: str,
    run_id: str,
    tab: str = Form(..., description="Tab label from verb schema interpretation.tabs"),
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
):
    project_path = get_project_path(project)
    dump_dir = _run_dump_dir(project_path, group, run_id)

    verb_name = _resolve_verb_name(project_path, run_id)
    tabs = _schema_tabs(project_path, verb_name)
    if tab not in tabs:
        raise HTTPException(status_code=400, detail=f"Tab {tab!r} is not defined by verb {verb_name!r}.")

    status_file = resolve_path(project_path, "status_file", verb_group=group, run_id=run_id)
    status_doc = load_data(status_file) or {}
    ls = (status_doc.get("linear_status") or {})
    steps = list(ls.get("steps") or [])
    if steps:
        current_index = ls.get("current_index")
        if current_index is None:
            current_index = next((i for i, s in enumerate(steps) if not bool(s.get("completed"))), len(steps))

        def _is_interp(s: dict) -> bool:
            hay = " ".join(str(s.get(k, "")) for k in ("id", "label", "type", "source")).lower()
            hay = hay.replace("_", " ").replace("-", " ")
            return any(k in hay for k in ("interpret", "interpretation", "parse", "parsing"))

        interp_idx = next((i for i, s in enumerate(steps) if _is_interp(s)), None)

        # Gate rule: allow once we've reached or passed the interpretation step
        if interp_idx is not None and current_index < interp_idx:
            raise HTTPException(status_code=409, detail="Interpretation uploads are locked until the Interpretation step is reached.")


    chosen_name = Path(file.filename or "").name
    if not chosen_name:
        raise HTTPException(status_code=400, detail="Filename required.")
    ext = Path(chosen_name).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"Extension {ext!r} not allowed.")

    target = dump_dir / f"{tab}{ext}"

    if fs_exists(target) and not overwrite:
        raise HTTPException(status_code=409, detail="File already exists. Pass overwrite=true to replace.")

    deleted = _delete_all_tab_files(dump_dir, tab, keep=(target if fs_exists(target) else None))

    try:
        with fs_open_writebin(target) as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    finally:
        try:
            await file.close()
        except Exception:
            pass

    size = fs_stat_size(target) if fs_exists(target) else 0
    debug(f"[interpret-upload] saved {target} ({size} bytes) overwrite={overwrite}")
    return {"status": "ok", "tab": tab, "filename": target.name, "bytes": size, "replaced": deleted}

@router.post("/runlog/{project}/{group}/{run_id}/interpret/reset")
async def interpret_reset_csvs(
    project: str,
    group: str,
    run_id: str,
):
    project_path = get_project_path(project)
    dump_dir = _run_dump_dir(project_path, group, run_id)

    verb_name = _resolve_verb_name(project_path, run_id)
    tabs = _schema_tabs(project_path, verb_name)
    if not tabs:
        raise HTTPException(status_code=404, detail=f"No interpretation tabs defined for verb {verb_name!r}")

    created, removed = [], {}
    for t in tabs:
        removed[t] = _delete_all_tab_files(dump_dir, t)
        target = dump_dir / f"{t}.csv"
        try:
            with fs_open_writebin(target) as csvfile:
                # Write a blank CSV header row
                csvfile.write((",\n").encode("utf-8"))
            created.append(str(target))
            debug(f"[interpret-reset] created blank CSV {target}")
        except Exception as e:
            debug(f"[interpret-reset] failed for {target}: {e!r}")

    return {"status": "ok", "created": created, "removed": removed}

@router.delete("/runlog/{project}/{group}/{run_id}/interpret/delete")
def interpret_delete(
    project: str,
    group: str,
    run_id: str,
    tab: str = Query(..., description="Tab label from verb schema interpretation.tabs"),
):
    project_path = get_project_path(project)
    dump_dir = _run_dump_dir(project_path, group, run_id)

    verb_name = _resolve_verb_name(project_path, run_id)
    tabs = _schema_tabs(project_path, verb_name)
    if tab not in tabs:
        raise HTTPException(status_code=400, detail=f"Tab {tab!r} is not defined by verb {verb_name!r}.")

    deleted = _delete_all_tab_files(dump_dir, tab)
    if not deleted:
        raise HTTPException(status_code=404, detail="No interpretation file found for this tab.")
    debug(f"[interpret-delete] removed {deleted}")
    return {"status": "ok", "deleted": deleted, "tab": tab}

# -----------------------------------------------------------------------------
# Gate operations (linear_status in Status.json)
# -----------------------------------------------------------------------------

def _project_path(project: str) -> Path:
    projects_root = resolve_path(Path(), "project_root")
    # Avoid .resolve() to keep S3-compat; rely on fs_exists
    pp = (projects_root / project)
    if not fs_exists(pp):
        raise HTTPException(404, f"Project '{project}' not found.")
    return pp

def _status_paths_for_run(pp: Path, group: str, run_id: str) -> Dict[str, Path]:
    dump_dir   = resolve_path(pp, "data_dump_dir", verb_group=group, run_id=run_id)
    status_path = resolve_path(pp, "status_file",   verb_group=group, run_id=run_id)
    return {"dump_dir": dump_dir, "status_path": status_path}

def _load_linear_status(status_path: Path) -> dict:
    doc = load_data(status_path) or {}
    ls = doc.get("linear_status") or {}
    return {"doc": doc, "linear_status": ls}

def _save_status_json(status_path: Path, doc: dict) -> None:
    save_json(status_path, doc)

def _recalc_current_index(steps: List[dict]) -> int:
    for i, s in enumerate(steps or []):
        if not bool(s.get("completed")):
            return i
    return len(steps or [])

@router.get("/runlog/{project}/{verb_group}/{run_id}/status/linear")
def get_linear_status_for_run(project: str, verb_group: str, run_id: str):
    pp = _project_path(project)

    verb = resolve_run_id_to_test_type(pp, run_id)
    if not verb:
        raise HTTPException(404, f"Run '{run_id}' not found (verb not resolved).")
    resolved_group = resolve_verb_group_from_test_type(pp, verb) or verb_group

    try:
        from core.status import get_linear_status_progress
        get_linear_status_progress(pp, str(run_id))
    except Exception as e:
        debug("[linear][status/linear][refresh][error]", {"run_id": run_id, "err": repr(e)})

    paths = _status_paths_for_run(pp, resolved_group, run_id)
    status_info = _load_linear_status(paths["status_path"])
    ls = status_info["linear_status"] or {}

    enabled = bool(ls.get("enabled", False))
    steps   = list(ls.get("steps") or [])
    total   = len(steps)
    completed = sum(1 for s in steps if bool(s.get("completed")))
    progress = f"{completed}/{total}"

    return {
        "ok": True,
        "verb": verb,
        "group": resolved_group,
        "run_id": run_id,
        "enabled": enabled,
        "steps_total": total,
        "steps_completed": completed,
        "progress": progress,
        "status_path": str(paths["status_path"]),
        "gates": [
            {
                "index": i,
                "id": s.get("id"),
                "type": s.get("type"),
                "label": s.get("label"),
                "required": bool(s.get("required", False)),
                "completed": bool(s.get("completed", False)),
            }
            for i, s in enumerate(steps) if (s.get("type") == "gate")
        ],
    }

@router.get("/runlog/{project}/{verb_group}/{run_id}/gate/list")
def list_gates_for_run(project: str, verb_group: str, run_id: str):
    pp = _project_path(project)

    verb = resolve_run_id_to_test_type(pp, run_id)
    if not verb:
        raise HTTPException(404, f"Run '{run_id}' not found (verb not resolved).")

    resolved_group = resolve_verb_group_from_test_type(pp, verb) or verb_group
    verb_schema = get_verb_schema(pp, verb) or {}
    ls_schema = (verb_schema.get("linear_status") or {})
    if not bool(ls_schema.get("enabled")):
        raise HTTPException(400, f"Verb '{verb}' is not linear-enabled.")

    _ensure_linear_status_fresh(pp, resolved_group, run_id)

    paths = _status_paths_for_run(pp, resolved_group, run_id)
    status_info = _load_linear_status(paths["status_path"])
    ls = status_info["linear_status"] or {}
    steps = list(ls.get("steps") or [])

    if not steps:
        raise HTTPException(404, "No linear steps found in Status.json.")

    gates = [
        {
            "index": i,
            "id": s.get("id"),
            "label": s.get("label"),
            "completed": bool(s.get("completed", False)),
        }
        for i, s in enumerate(steps) if s.get("type") == "gate"
    ]

    return {
        "ok": True,
        "verb": verb,
        "group": resolved_group,
        "run_id": run_id,
        "status_path": str(paths["status_path"]),
        "gates": gates,
    }

@router.post("/runlog/{project}/{verb_group}/{run_id}/gate/{step_id}/complete")
def complete_gate_step(
    project: str,
    verb_group: str,
    run_id: str,
    step_id: str,
    completed: bool = Query(True, description="True=sign off, False=reopen"),
):
    pp = _project_path(project)

    verb = resolve_run_id_to_test_type(pp, run_id)
    if not verb:
        raise HTTPException(404, f"Run '{run_id}' not found (verb not resolved).")
    resolved_group = resolve_verb_group_from_test_type(pp, verb) or verb_group

    verb_schema = get_verb_schema(pp, verb) or {}
    ls_schema = (verb_schema.get("linear_status") or {})
    if not bool(ls_schema.get("enabled")):
        raise HTTPException(400, f"Verb '{verb}' is not linear-enabled.")

    _ensure_linear_status_fresh(pp, resolved_group, run_id)

    paths = _status_paths_for_run(pp, resolved_group, run_id)
    status_info = _load_linear_status(paths["status_path"])
    doc = status_info["doc"]
    ls  = status_info["linear_status"] or {}
    steps = list(ls.get("steps") or [])

    if not steps:
        raise HTTPException(404, "No linear steps found in Status.json.")

    idx = None
    for i, s in enumerate(steps):
        if s.get("internal_id") == step_id:
            idx = i
            if s.get("type") != "gate":
                raise HTTPException(400, f"Step '{step_id}' is not a gate.")
            break
    if idx is None:
        raise HTTPException(404, f"Gate step '{step_id}' not found in Status.json.")

    steps[idx]["completed"] = bool(completed)

    ls["steps"] = steps
    ls["current_index"] = _recalc_current_index(steps)
    doc["linear_status"] = ls

    _save_status_json(paths["status_path"], doc)

    total = len(steps)
    done  = sum(1 for s in steps if bool(s.get("completed")))
    return {
        "ok": True,
        "verb": verb,
        "group": resolved_group,
        "run_id": run_id,
        "step_id": step_id,
        "completed": bool(completed),
        "steps_total": total,
        "steps_completed": done,
        "progress": f"{done}/{total}",
        "current_index": ls["current_index"],
        "status_path": str(paths["status_path"]),
    }

# -----------------------------------------------------------------------------
# Downloads (file & zip) — now streaming via S3/local shims
# -----------------------------------------------------------------------------

@router.get("/runlog/{project}/{group}/{run_id}/raw/download")
def raw_download_file(
    project: str,
    group: str,
    run_id: str,
    pocket: str,
    filename: str,
    inline: bool = False,
):
    project_path = get_project_path(project)
    pocket = _validate_pocket(project_path, group, run_id, pocket)
    base = _validate_filename(filename)

    target_dir = _pocket_dir_for_run(project_path, group, run_id, pocket)
    target = (target_dir / base)

    # Use S3-safe containment check
    if not _is_within(target_dir, target) or not fs_exists(target) or not fs_is_file(target):
        raise HTTPException(status_code=404, detail="File not found.")

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    disposition = "inline" if inline else "attachment"
    fh = fs_open_readbin(target)
    return StreamingResponse(
        fh,
        media_type=media_type,
        headers={"Content-Disposition": f'{disposition}; filename="{target.name}"'},
    )

@router.get("/runlog/{project}/{group}/{run_id}/interpret/download")
def interpret_download_file(
    project: str,
    group: str,
    run_id: str,
    tab: str,
    inline: bool = False,
):
    project_path = get_project_path(project)
    dump_dir = _run_dump_dir(project_path, group, run_id)

    verb_name = _resolve_verb_name(project_path, run_id)
    tabs = _schema_tabs(project_path, verb_name)
    if tab not in tabs:
        raise HTTPException(status_code=400, detail=f"Tab {tab!r} is not defined by verb {verb_name!r}.")

    f = _existing_tab_file(dump_dir, tab)
    if not f or not fs_exists(f):
        raise HTTPException(status_code=404, detail="Interpretation file not found for this tab.")

    media_type = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
    disposition = "inline" if inline else "attachment"
    fh = fs_open_readbin(f)
    return StreamingResponse(
        fh,
        media_type=media_type,
        headers={"Content-Disposition": f'{disposition}; filename="{f.name}"'},
    )

@router.get("/runlog/{project}/{group}/{run_id}/raw/download_zip")
def raw_download_zip(
    project: str,
    group: str,
    run_id: str,
    pocket: str,
):
    project_path = get_project_path(project)
    pocket = _validate_pocket(project_path, group, run_id, pocket)
    pdir = _pocket_dir_for_run(project_path, group, run_id, pocket)

    files = [f for f in sorted(fs_iterdir(pdir), key=lambda x: x.name)
             if fs_is_file(f) and f.suffix.lower() in _ALLOWED_EXTS]
    if not files:
        raise HTTPException(status_code=404, detail="No files found in this pocket.")

    buf = make_zip_stream([(f, f.name) for f in files])
    zip_name = f"{run_id}_{pocket}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_name}"'
        },
    )

@router.get("/runlog/{project}/{group}/{run_id}/interpret/download_zip")
def interpret_download_zip(
    project: str,
    group: str,
    run_id: str,
    tabs: Optional[List[str]] = Query(None, description="Optional list of tab names; defaults to all tabs."),
):
    project_path = get_project_path(project)
    dump_dir = _run_dump_dir(project_path, group, run_id)

    verb_name = _resolve_verb_name(project_path, run_id)
    all_tabs = _schema_tabs(project_path, verb_name)
    wanted = tabs or all_tabs
    wanted = [t for t in wanted if t in all_tabs]

    found: List[Path] = []
    for t in wanted:
        f = _existing_tab_file(dump_dir, t)
        if f and fs_exists(f):
            found.append(f)

    if not found:
        raise HTTPException(status_code=404, detail="No interpretation files found for the requested tabs.")

    buf = make_zip_stream([(f, f.name) for f in found])
    zip_name = f"{run_id}_interpretation.zip" if not tabs else f"{run_id}_interpretation_selected.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )

@router.get("/runlog/{project}/{verb_group}/{run_id}/status/step_ids")
def get_linear_step_ids(project: str, verb_group: str, run_id: str):
    pp = _project_path(project)

    verb = resolve_run_id_to_test_type(pp, run_id)
    if not verb:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found (verb not resolved).")
    resolved_group = resolve_verb_group_from_test_type(pp, verb) or verb_group

    schema = get_verb_schema(pp, verb) or {}
    ls = (schema.get("linear_status") or {})
    steps = list(ls.get("steps") or [])

    return {
        "ok": True,
        "verb": verb,
        "group": resolved_group,
        "run_id": run_id,
        "step_ids": [s.get("id") for s in steps],
        "steps": [
            {
                "index": i,
                "id": s.get("id"),
                "type": s.get("type"),
                "label": s.get("label"),
                "required": bool(s.get("required", True)),
                "source": s.get("source"),
            }
            for i, s in enumerate(steps)
        ],
    }
