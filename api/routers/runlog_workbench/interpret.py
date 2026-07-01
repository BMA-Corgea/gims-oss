# api/routers/runlog_workbench/interpret.py
"""Interpretation files (one file per "tab" from the verb schema) + helpers.

The tab helpers (``_run_dump_dir``, ``_resolve_verb_name``, ``_schema_tabs``,
``_existing_tab_file``, ``_delete_all_tab_files``) are defined here and re-used by
the downloads submodule.
"""

import re
from pathlib import Path
from typing import List, Optional

from fastapi import Query, UploadFile, File, Form

from ._router import router
from ._shared import (
    AppError,
    get_project_path,
    resolve_path,
    load_data,
    resolve_run_id_to_test_type,
    get_verb_schema,
    fs_exists,
    fs_is_file,
    fs_iterdir,
    fs_mkdirs,
    fs_remove,
    fs_open_writebin,
    fs_stat_size,
    fs_glob_first,
    _ALLOWED_EXTS,
    log,
)

# -----------------------------------------------------------------------------
# Interpretation files (one file per "tab" from the verb schema)
# -----------------------------------------------------------------------------

_name_clean_re = re.compile(r"[^A-Za-z0-9._ -]+")

def _safe_tab(tab: str) -> str:
    if not tab or not isinstance(tab, str):
        raise AppError("TAB_REQUIRED", "Tab is required.", status=400)
    s = tab.strip()
    s = _name_clean_re.sub("_", s)
    s = s.strip(" ._-")
    if not s or s.startswith(".") or ".." in s or "/" in s or "\\" in s:
        raise AppError("INVALID_TAB_NAME", "Invalid tab name.", status=400,
                       details={"tab": tab})
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
                log.debug(f"[interpret-delete*] could not remove {cand}: {e!r}")
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
        raise AppError("TAB_NOT_DEFINED", f"Tab {tab!r} is not defined by verb {verb_name!r}.",
                       status=400, details={"tab": tab, "verb": verb_name})

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
            raise AppError("INTERPRET_UPLOAD_LOCKED",
                           "Interpretation uploads are locked until the Interpretation step is reached.",
                           status=409, details={"group": group, "run_id": run_id, "tab": tab})


    chosen_name = Path(file.filename or "").name
    if not chosen_name:
        raise AppError("FILENAME_REQUIRED", "Filename required.", status=400)
    ext = Path(chosen_name).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise AppError("EXTENSION_NOT_ALLOWED", f"Extension {ext!r} not allowed.", status=400,
                       details={"filename": chosen_name, "extension": ext})

    target = dump_dir / f"{tab}{ext}"

    if fs_exists(target) and not overwrite:
        raise AppError("INTERPRET_FILE_EXISTS", "File already exists. Pass overwrite=true to replace.",
                       status=409, details={"group": group, "run_id": run_id, "tab": tab})

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
    log.debug(f"[interpret-upload] saved {target} ({size} bytes) overwrite={overwrite}")
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
        raise AppError("NO_INTERPRETATION_TABS", f"No interpretation tabs defined for verb {verb_name!r}",
                       status=404, details={"verb": verb_name})

    created, removed = [], {}
    for t in tabs:
        removed[t] = _delete_all_tab_files(dump_dir, t)
        target = dump_dir / f"{t}.csv"
        try:
            with fs_open_writebin(target) as csvfile:
                # Write a blank CSV header row
                csvfile.write((",\n").encode("utf-8"))
            created.append(str(target))
            log.debug(f"[interpret-reset] created blank CSV {target}")
        except Exception as e:
            log.debug(f"[interpret-reset] failed for {target}: {e!r}")

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
        raise AppError("TAB_NOT_DEFINED", f"Tab {tab!r} is not defined by verb {verb_name!r}.",
                       status=400, details={"tab": tab, "verb": verb_name})

    deleted = _delete_all_tab_files(dump_dir, tab)
    if not deleted:
        raise AppError("INTERPRET_FILE_NOT_FOUND", "No interpretation file found for this tab.",
                       status=404, details={"group": group, "run_id": run_id, "tab": tab})
    log.debug(f"[interpret-delete] removed {deleted}")
    return {"status": "ok", "deleted": deleted, "tab": tab}
