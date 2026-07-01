# core/status.py

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re

# Project helpers
from api.manifest.resolver import resolve_path
from api.i_o import (
    resolve_run_id_to_test_type,
    resolve_verb_group_from_test_type,
    list_verb_groups,
    get_verb_schema,
    get_noun_schema,
    load_schema,
    load_data,
    is_file_empty,
    get_override_schema,
    save_json,
    # FS shims
    fs_exists, fs_is_dir, fs_is_file, fs_iterdir,
)

# Debug control
from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()
DEBUG_VERBS: set[str] = set()  # e.g., {"LCMSMS"} to filter logs to one verb

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_NAME_CLEAN_RE = re.compile(r"[^A-Za-z0-9._ -]+")
_NON_INTERP_FILENAMES = {
    "DataEntry.json", "Status.json", "Instructions.md", "adverbs.json", "run_entry.json"
}

def _safe_tab(tab: str) -> str:
    """
    Sanitize a tab label into the basename convention used by your interpret API.
    """
    if not tab or not isinstance(tab, str):
        return ""
    s = tab.strip()
    s = _NAME_CLEAN_RE.sub("_", s)
    s = s.strip(" ._-")
    if not s or s.startswith(".") or ".." in s or "/" in s or "\\" in s:
        return ""
    return s

def _first_existing_with_stem(dirpath: Path, stem: str) -> Optional[Path]:
    """
    Return the first file under dirpath whose stem matches exactly.
    S3-aware via fs_iterdir/fs_is_file.
    """
    log.debug(f"[_first_existing_with_stem] dir={dirpath} stem={stem!r}")
    if not stem:
        return None
    try:
        if not (fs_exists(dirpath) and fs_is_dir(dirpath)):
            log.debug(f"[_first_existing_with_stem] dir missing or not dir: {dirpath}")
            return None
        
        children = fs_iterdir(dirpath)
        log.debug(f"[_first_existing_with_stem] children={len(children)}")
        
        for p in children:
            p = Path(str(p))
            name = p.name
            if not fs_is_file(p):
                log.debug(f"[_first_existing_with_stem] skip non-file: {name}")
                continue
            if name in _NON_INTERP_FILENAMES:
                log.debug(f"[_first_existing_with_stem] skip config: {name}")
                continue
            file_stem = Path(name).stem
            if file_stem == stem:
                log.debug(f"[_first_existing_with_stem] MATCH: {name}")
                return p
            else:
                log.debug(f"[_first_existing_with_stem] no match: {name} (stem={file_stem} vs {stem})")
        log.debug(f"[_first_existing_with_stem] no match for stem={stem!r}")
        return None
    except Exception as e:
        log.debug(f"[_first_existing_with_stem] ERROR: {e}")
        return None

def _rows_from_data_entry(data_entry_obj: Any) -> List[dict]:
    """
    Normalize DataEntry into a list[dict].
    Accepts list-of-dicts, or dict with 'rows', else empty.
    """
    log.debug("[data_entry] normalize: type=", type(data_entry_obj).__name__)
    rows: List[dict] = []
    if isinstance(data_entry_obj, list):
        rows = [x for x in data_entry_obj if isinstance(x, dict)]
    elif isinstance(data_entry_obj, dict):
        raw_rows = data_entry_obj.get("rows")
        if isinstance(raw_rows, list):
            rows = [x for x in raw_rows if isinstance(x, dict)]
    log.debug("[data_entry] normalized rows:", len(rows))
    return rows

def _resolve_paths_for_run(project_path: Path, verb_group: str, run_id: str) -> dict:
    """
    Return a dict of key file/dir paths for the run.
    """
    paths = {
        "dump_dir": resolve_path(project_path, "data_dump_dir", verb_group=verb_group, run_id=run_id),
        "status_path": resolve_path(project_path, "status_file", verb_group=verb_group, run_id=run_id),
        "data_entry_path": resolve_path(project_path, "data_entry", verb_group=verb_group, run_id=run_id),
        "adverbs_path": resolve_path(project_path, "adverb_file", verb_group=verb_group, run_id=run_id),
    }
    log.debug("[paths]", {k: str(v) for k, v in paths.items()})
    return paths

# ─────────────────────────────────────────────────────────────────────────────
# Core checkers (used by both classic + linear)
# ─────────────────────────────────────────────────────────────────────────────

def _check_raw_folder_has_file(dump_dir: Path, folder_name: str) -> bool:
    """
    True if the raw folder exists and contains at least one file.
    S3-aware: uses fs_* shims.
    """
    nm = (folder_name or "").strip()
    log.debug("[raw][check] folder:", nm, "dump_dir:", str(dump_dir))
    if not nm:
        return False
    
    raw_folder = dump_dir / nm
    log.debug(f"[raw][check] checking path: {raw_folder}")
    
    if not (fs_exists(raw_folder) and fs_is_dir(raw_folder)):
        log.debug("[raw][check] folder missing or not a dir")
        return False
    
    try:
        children = fs_iterdir(raw_folder)
        log.debug(f"[raw][check] children={len(children)}")
        for item in children:
            item = Path(str(item))
            is_file = fs_is_file(item)
            log.debug(f"[raw][check]   - {item.name} (is_file={is_file})")
            if is_file:
                log.debug("[raw][check] FOUND file -> OK")
                return True
    except FileNotFoundError:
        log.debug("[raw][check] FileNotFoundError")
        return False
    except Exception as e:
        log.debug(f"[raw][check] ERROR: {e}")
        return False
    
    log.debug("[raw][check] NO files found")
    return False

def _check_interpretation_tab_present(dump_dir: Path, tab_label: str | List[str]) -> bool:
    """
    Return True if the interpretation tab(s) exist and are non-empty when required.
    """
    labels: List[str] = [tab_label] if isinstance(tab_label, str) else (tab_label or [])
    log.debug("[interp][check] labels:", labels, "dump_dir:", str(dump_dir))
    if not labels:
        return False

    for lbl in labels:
        safe = _safe_tab(lbl)
        log.debug(f"[interp][check] label={lbl!r} safe={safe!r}")
        if not safe:
            log.debug("[interp][check] invalid safe label")
            return False
        
        try:
            if not (fs_exists(dump_dir) and fs_is_dir(dump_dir)):
                log.debug("[interp][check] dump_dir missing/not dir")
                return False
            
            children = fs_iterdir(dump_dir)
            log.debug(f"[interp][check] scanning children={len(children)} for stem={safe!r}")
            
            found = False
            for p in children:
                p = Path(str(p))
                name = p.name
                is_file = fs_is_file(p)
                log.debug(f"[interp][check]   - {name} (is_file={is_file})")
                if not is_file:
                    continue
                if name in _NON_INTERP_FILENAMES:
                    continue
                file_stem = Path(name).stem
                if file_stem == safe:
                    # If CSV, verify not empty
                    if name.lower().endswith(".csv") and is_file_empty(p):
                        log.debug(f"[interp][check] {name} is EMPTY CSV -> fail")
                        return False
                    log.debug(f"[interp][check] FOUND: {name}")
                    found = True
                    break
            
            if not found:
                log.debug(f"[interp][check] NO MATCH for stem={safe!r}")
                return False
                
        except Exception as e:
            log.debug(f"[interp][check] ERROR: {e}")
            return False
    
    return True

def _check_adverb_value_present(adverbs_json: dict, adverb_name: str) -> bool:
    """
    Value is present if it exists and is truthy/non-empty.
    """
    val = adverbs_json.get(adverb_name)
    present = False
    if val is None:
        present = False
    elif isinstance(val, list):
        present = len(val) > 0
    elif isinstance(val, str):
        present = bool(val.strip())
    else:
        present = bool(val)
    log.debug(f"[adverb][check] {adverb_name!r} present={present} value_type={type(val).__name__}")
    return present

def _check_data_entry_complete(rows: List[dict], noun_schema: Optional[dict]) -> Tuple[bool, dict]:
    """
    Return (is_complete, details).
    Required fields from noun schema must be present for non-empty rows.
    Duplicate primary IDs are allowed if _runID differs.
    """
    details = {"rows": len(rows), "missing_required": [], "duplicates": False}
    if not rows:
        log.debug("[data_entry][check] no rows -> incomplete")
        return (False, details)

    def _is_missing_val(v: Any) -> bool:
        return (v is None) or (isinstance(v, str) and v.strip() == "")

    required_fields: List[str] = []
    pk_field: Optional[str] = None
    if isinstance(noun_schema, dict):
        fields = noun_schema.get("fields", {}) if isinstance(noun_schema.get("fields"), dict) else {}
        required_fields = [fname for fname, fprops in fields.items() if fprops.get("required")]
        pk_field = noun_schema.get("primary_id_field")

    log.debug("[data_entry][check] required_fields:", required_fields, "pk_field:", pk_field)

    missing_fields = False
    seen: set = set()

    for i, row in enumerate(rows):
        nonempty = any((v.strip() if isinstance(v, str) else bool(v)) for v in row.values())
        log.debug(f"[data_entry][row {i}] nonempty={nonempty}")
        if not nonempty:
            continue

        for field in required_fields:
            val = row.get(field, None)
            if _is_missing_val(val):
                details["missing_required"].append(field)
                missing_fields = True
                log.debug(f"[data_entry][row {i}] missing required: {field!r}")

        if pk_field:
            pid = row.get(pk_field)
            rid = row.get("_runID")
            key = (pid, rid if rid is not None else "__no_run__")
            if pid is not None:
                if key in seen:
                    details["duplicates"] = True
                    missing_fields = True
                    log.debug(f"[data_entry][row {i}] DUPLICATE primary/run combo: {key}")
                else:
                    seen.add(key)

    log.debug("[data_entry][check] complete=", not missing_fields, "details=", details)
    return (not missing_fields, details)

# ─────────────────────────────────────────────────────────────────────────────
# LINEAR STATUS
# ─────────────────────────────────────────────────────────────────────────────

def get_linear_status_progress(project_path: Path, run_id: str) -> Optional[dict]:
    """
    Evaluate linear-status progress for a run and persist to Status.json.
    """
    from copy import deepcopy

    log.debug("[linear] BEGIN run_id=", run_id)
    try:
        verb_name = resolve_run_id_to_test_type(project_path, run_id)
        log.debug("[linear] resolved verb_name:", verb_name)
    except Exception as e:
        log.debug("[linear] failed to resolve verb:", repr(e))
        return None

    verb_schema = get_verb_schema(project_path, verb_name) or {}
    ls = (verb_schema or {}).get("linear_status") or {}
    log.debug("[linear] ls.enabled=", bool(ls.get("enabled")), "steps_count=", len(ls.get("steps") or []))
    if not (ls and ls.get("enabled") and isinstance(ls.get("steps"), list) and ls["steps"]):
        return None

    verb_group = resolve_verb_group_from_test_type(project_path, verb_name) or "Tests"
    log.debug("[linear] verb_group:", verb_group)

    dump_dir: Path = resolve_path(project_path, "data_dump_dir", verb_group=verb_group, run_id=run_id)
    status_path: Path = resolve_path(project_path, "status_file",   verb_group=verb_group, run_id=run_id)
    data_entry_path: Path = resolve_path(project_path, "data_entry", verb_group=verb_group, run_id=run_id)
    adverbs_path: Path = resolve_path(project_path, "adverb_file",  verb_group=verb_group, run_id=run_id)
    log.debug("[linear] paths:", {"dump_dir": str(dump_dir), "status": str(status_path), "data_entry": str(data_entry_path), "adverbs": str(adverbs_path)}, verb_key=verb_name)

    status_json = load_data(status_path) or {}
    data_entry_obj = load_data(data_entry_path)
    adverbs_json = load_data(adverbs_path) or {}
    rows = _rows_from_data_entry(data_entry_obj)
    log.debug("[linear] loaded status/data/adverbs rows=", len(rows), verb_key=verb_name)

    noun_type = (
        verb_schema.get("data_entry_schema", {})
                   .get("set_up_inputs", {})
                   .get("noun_type_ref")
    )
    noun_schema = get_noun_schema(project_path, noun_type) if noun_type else None
    log.debug("[linear] noun_type:", noun_type, "noun_schema_exists=", bool(noun_schema), verb_key=verb_name)

    ls_doc = status_json.get("linear_status") or {}
    if not isinstance(ls_doc.get("steps"), list):
        ls_doc["steps"] = []
    status_json["linear_status"] = ls_doc

    prev_by_id: Dict[str, dict] = {
        str(s.get("internal_id", s.get("id"))): s for s in ls_doc["steps"] if s.get("id")
    }
    log.debug("[linear] prev_by_id keys:", list(prev_by_id.keys()), verb_key=verb_name)

    old_steps_from_status = ls_doc.get("steps", [])

    breakdown: List[dict] = []
    status_updated = False
    schema_steps = ls.get("steps", [])
    total = len(schema_steps)
    completed_in_a_row = 0
    first_incomplete: Optional[dict] = None
    rollback_mode = False

    normalized_steps: List[dict] = []

    for idx, schema_step in enumerate(schema_steps):
        sdoc = deepcopy(schema_step)

        stype = sdoc.get("type")
        raw_id = sdoc.get("id")
        required = bool(sdoc.get("required", True))
        source = sdoc.get("source")
        log.debug(f"[linear][step {idx}] type={stype} id={raw_id} required={required} source={source}", verb_key=verb_name)

        if schema_step.get("label"):
            human_id = str(schema_step["label"])
        elif stype in ("adverb", "raw_upload", "interpretation") and source:
            human_id = f"{stype.replace('_', ' ').title()} - {source}"
        elif stype == "gate":
            human_id = f"Gate (Step {idx+1})"
        else:
            human_id = stype.replace('_', ' ').title()

        done, reason = False, ""

        if rollback_mode:
            reason = "Earlier step missing"
            log.debug(f"[linear][step {idx}] rollback_mode active", verb_key=verb_name)
        elif not required:
            done, reason = True, "Optional"
        elif stype == "data_entry":
            ok, det = _check_data_entry_complete(rows, noun_schema)
            done, reason = ok, "" if ok else "Missing required fields"
            log.debug(f"[linear][step {idx}] data_entry ok={ok} details={det}", verb_key=verb_name)
        elif stype == "raw_upload":
            pocket_to_check = (source or "").strip()
            has_file = _check_raw_folder_has_file(dump_dir, pocket_to_check)
            done, reason = has_file, "" if has_file else f"Missing raw files in '{pocket_to_check}'"
            log.debug(f"[linear][step {idx}] raw_upload has_file={has_file}", verb_key=verb_name)
        elif stype == "interpretation":
            ok = _check_interpretation_tab_present(dump_dir, source or "")
            done, reason = ok, "" if ok else f"Missing interpretation for '{source}'"
            log.debug(f"[linear][step {idx}] interpretation ok={ok}", verb_key=verb_name)
        elif stype == "adverb":
            ok = _check_adverb_value_present(adverbs_json, source or "")
            done, reason = ok, "" if ok else f"Adverb '{source}' missing"
            log.debug(f"[linear][step {idx}] adverb ok={ok}", verb_key=verb_name)
        elif stype == "gate":
            prev = prev_by_id.get(str(raw_id), {})
            done = bool(prev.get("completed", False))
            reason = "" if done else "Gate not completed"
            log.debug(f"[linear][step {idx}] gate done={done}", verb_key=verb_name)
        else:
            done, reason = True, "Unknown step type (non-blocking)"
            log.debug(f"[linear][step {idx}] unknown type -> non-blocking", verb_key=verb_name)

        if not done and required:
            rollback_mode = True
            log.debug(f"[linear][step {idx}] sets rollback_mode", verb_key=verb_name)

        sdoc["internal_id"] = raw_id
        sdoc["id"] = human_id
        sdoc["completed"] = bool(done)

        if stype == "gate":
            prev = prev_by_id.get(str(raw_id), {})
            sdoc["completed"] = bool(prev.get("completed", False))

        normalized_steps.append(sdoc)

        entry = {**sdoc, "index": idx, "reason": reason}
        breakdown.append(entry)

        if stype != "gate":
            prev_done = bool(prev_by_id.get(str(raw_id), {}).get("completed", False))
            if prev_done != sdoc["completed"]:
                status_updated = True
                log.debug(f"[linear][step {idx}] completion changed prev={prev_done} now={sdoc['completed']}", verb_key=verb_name)

        if sdoc["completed"] and first_incomplete is None:
            completed_in_a_row += 1
        elif first_incomplete is None:
            first_incomplete = entry

    ls_doc["steps"] = normalized_steps
    cur_idx = next((i for i, s in enumerate(normalized_steps) if not s.get("completed")), len(normalized_steps))
    ls_doc["current_index"] = cur_idx
    status_json["linear_status"] = ls_doc

    structural_change = (old_steps_from_status != normalized_steps)
    log.debug("[linear] structural_change=", structural_change, "status_updated=", status_updated, verb_key=verb_name)

    if status_updated or structural_change:
        try:
            save_json(status_path, status_json)
            log.debug("[linear][persist] wrote Status.json", str(status_path), f"current_index={cur_idx}", verb_key=verb_name)
        except Exception as e:
            log.debug("[linear][persist][error]", repr(e), str(status_path), verb_key=verb_name)

    result = {
        "mode": "linear",
        "verb": verb_name,
        "group": verb_group,
        "steps_completed": completed_in_a_row,
        "steps_total": total,
        "progress_text": f"{completed_in_a_row}/{total}",
        "first_incomplete": first_incomplete,
        "breakdown": breakdown,
        "status_path": str(status_path),
    }
    log.debug("[linear][result]", result, verb_key=verb_name)
    return result

# ─────────────────────────────────────────────────────────────────────────────
# CLASSIC: get_status_breakdown_core
# ─────────────────────────────────────────────────────────────────────────────

def get_status_breakdown_core(project_path: Path, run_id: str) -> dict:
    """
    GUI-backend version. Auto-resolves verb/group and computes breakdown.
    If the verb is linear-enabled, returns a linear summary instead.
    """
    log.debug("[classic] BEGIN run_id=", run_id)

    # Identify verb
    try:
        verb_name = resolve_run_id_to_test_type(project_path, run_id)
        log.debug("[classic] resolved verb_name:", verb_name)
    except Exception as e:
        log.debug("[classic] resolve_run_id_to_test_type failed:", repr(e))
        return {
            "raw_data": "Not Uploaded",
            "data_entry": "Pending",
            "interpretation": "Pending",
            "adverb_info": "Pending",
        }

    # Linear?
    verb_schema = get_verb_schema(project_path, verb_name) or {}
    ls = (verb_schema or {}).get("linear_status") or {}
    log.debug("[classic] linear_enabled=", bool(ls.get("enabled")), "steps_count=", len(ls.get("steps") or []), verb_key=verb_name)
    if ls and ls.get("enabled") and (ls.get("steps") or []):
        linear = get_linear_status_progress(project_path, run_id) or {}
        override_rows = get_override_schema(project_path, run_id) or []
        lines: List[str] = []
        for row in override_rows:
            otype = row.get("type", "Unknown")
            status = str(row.get("status", "EXCEPTION")).upper()
            resolved = row.get("resolution")
            if isinstance(resolved, list) and len(resolved) > 0:
                lines.append(f"RESOLVED: {otype}")
            else:
                lines.append(f"{status}: {otype}")
        override_status = "\n".join(lines) if lines else None

        out = {
            "mode": "linear",
            "linear_progress": linear.get("progress_text", "0/0"),
            "linear_steps_completed": linear.get("steps_completed", 0),
            "linear_steps_total": linear.get("steps_total", 0),
            "first_incomplete": linear.get("first_incomplete"),
            "details": linear,
        }
        if override_status:
            out["override_status"] = override_status
        log.debug("[classic][linear] out:", out, verb_key=verb_name)
        return out

    # Resolve group
    verb_group = resolve_verb_group_from_test_type(project_path, verb_name)
    log.debug("[classic] initial verb_group:", verb_group, verb_key=verb_name)
    if not verb_group:
        for g in list_verb_groups(project_path) or []:
            dd = resolve_path(project_path, "data_dump_dir", verb_group=g, run_id=run_id)
            if fs_exists(dd) and fs_is_dir(dd):
                verb_group = g
                break
    if not verb_group:
        verb_group = "Tests"
    log.debug("[classic] final verb_group:", verb_group, verb_key=verb_name)

    paths = _resolve_paths_for_run(project_path, verb_group, run_id)
    dump_dir: Path = paths["dump_dir"]
    status_path: Path = paths["status_path"]
    data_entry_path: Path = paths["data_entry_path"]
    adverbs_path: Path = paths["adverbs_path"]

    breakdown: Dict[str, str] = {}

    # 1) RAW DATA
    raw_inputs: List[str] = (
        verb_schema.get("data_entry_schema", {})
                   .get("raw_data_inputs", [])
    ) or []
    log.debug("[classic][raw] inputs:", raw_inputs, verb_key=verb_name)

    missing_raw: List[str] = []
    if raw_inputs:
        for folder_name in raw_inputs:
            nm = (folder_name or "").strip()
            if not nm:
                continue
            has_file = _check_raw_folder_has_file(dump_dir, nm)
            log.debug(f"[classic][raw] '{nm}':", "OK" if has_file else "MISSING", verb_key=verb_name)
            if not has_file:
                missing_raw.append(nm)
        breakdown["raw_data"] = "Uploaded" if not missing_raw else ("Missing → " + ", ".join(missing_raw))
    else:
        breakdown["raw_data"] = "Not Uploaded"

    # 2) DATA ENTRY
    noun_type = (
        verb_schema.get("data_entry_schema", {})
                   .get("set_up_inputs", {})
                   .get("noun_type_ref")
    )
    noun_schema = get_noun_schema(project_path, noun_type) if noun_type else None
    data_entry_obj = load_data(data_entry_path)
    rows = _rows_from_data_entry(data_entry_obj)
    log.debug("[classic][data_entry] rows:", len(rows), "noun_type:", noun_type, "noun_schema_exists:", bool(noun_schema), verb_key=verb_name)

    if not rows:
        breakdown["data_entry"] = "Pending"
    else:
        ok, det = _check_data_entry_complete(rows, noun_schema)
        log.debug("[classic][data_entry] ok=", ok, "details=", det, verb_key=verb_name)
        breakdown["data_entry"] = "Complete" if ok else "Missing Required Fields"

    # 3) INTERPRETATION
    interp_cfg = (verb_schema.get("data_entry_schema", {}) or {}).get("interpretation", {}) or {}
    interp_tabs: List[str] = interp_cfg.get("tabs", []) or []
    interp_method = interp_cfg.get("method", "parsed")
    status_json = load_data(status_path)
    manual_approved = bool(((status_json or {}).get("interpretation") or {}).get("manual_approval"))
    log.debug("[classic][interp] tabs:", interp_tabs, "method:", interp_method, "manual_approved:", manual_approved, verb_key=verb_name)

    if not interp_tabs:
        breakdown["interpretation"] = "Complete" if not manual_approved else "Manually Completed"
    else:
        all_good = True
        for tab in interp_tabs:
            present = _check_interpretation_tab_present(dump_dir, tab)
            log.debug(f"[classic][interp] {tab!r}: {'OK' if present else 'MISSING'}", verb_key=verb_name)
            if not present:
                all_good = False
        if all_good:
            breakdown["interpretation"] = "Uploaded" if interp_method == "uploaded" else "Parsed"
        else:
            breakdown["interpretation"] = "Manually Completed" if manual_approved else "Pending"

    # 4) ADVERBS
    adverbs_json = load_data(adverbs_path) or {}
    required_names: List[str] = []

    global_adv_schema = load_schema(project_path, "adverb") or []
    if isinstance(global_adv_schema, list):
        for entry in global_adv_schema:
            if entry.get("verb") == verb_name and entry.get("required"):
                nm = entry.get("adverb")
                if isinstance(nm, str) and nm.strip():
                    required_names.append(nm)

    embedded = verb_schema.get("adverb_schema")
    if isinstance(embedded, dict):
        for nm, spec in embedded.items():
            if spec.get("required"):
                required_names.append(nm)

    required_names = sorted(set(required_names))
    missing_adv = [nm for nm in required_names if not _check_adverb_value_present(adverbs_json, nm)]
    breakdown["adverb_info"] = "Complete" if not missing_adv else "Pending"
    log.debug("[classic][adverb] required:", required_names, "missing:", missing_adv, verb_key=verb_name)

    # 5) OVERRIDES
    override_rows = get_override_schema(project_path, run_id) or []
    log.debug("[classic][override] entries:", len(override_rows), verb_key=verb_name)

    lines: List[str] = []
    for row in override_rows:
        otype = row.get("type", "Unknown")
        status = str(row.get("status", "EXCEPTION")).upper()
        resolved = row.get("resolution")
        if isinstance(resolved, list) and len(resolved) > 0:
            lines.append(f"RESOLVED: {otype}")
        else:
            lines.append(f"{status}: {otype}")

    if lines:
        breakdown["override_status"] = "\n".join(lines)

    log.debug("[classic] final breakdown:", breakdown, verb_key=verb_name)
    return breakdown

# ─────────────────────────────────────────────────────────────────────────────
# Render helper
# ─────────────────────────────────────────────────────────────────────────────

def render_status_bar(breakdown: dict, blocks_per_zone: int = 3) -> str:
    """
    Render a visual status bar.
    """
    if breakdown.get("mode") == "linear":
        completed = int(breakdown.get("linear_steps_completed", 0))
        total = int(breakdown.get("linear_steps_total", 0)) or 1
        max_blocks = total * blocks_per_zone
        filled = completed * blocks_per_zone
        bar = "█" * filled + "░" * (max_blocks - filled)
        percent = int((completed / total) * 100)
        return f"Progress: [{bar}] {percent}% ({completed}/{total})"

    complete_states = {"Uploaded", "Complete", "Parsed", "Manually Completed"}
    completed_count = sum(1 for s in breakdown.values() if s in complete_states)
    total = len([k for k in breakdown.keys() if k != "override_status"])
    total = total or 1
    max_blocks = total * blocks_per_zone
    filled = completed_count * blocks_per_zone
    bar = "█" * filled + "░" * (max_blocks - filled)
    percent = int((completed_count / total) * 100)
    return f"Progress: [{bar}] {percent}%"
