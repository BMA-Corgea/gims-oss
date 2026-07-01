# api/routers/runlog_workbench/raw_files.py
"""Raw file uploads (per-pocket; zero processing) + pocket helpers.

The ``_validate_filename`` / ``_validate_pocket`` / ``_pocket_dir_for_run``
helpers are defined here (their "home" area) and re-used by the downloads
submodule.
"""

from pathlib import Path

from fastapi import Query, UploadFile, File, Form

from ._router import router
from ._shared import (
    AppError,
    get_project_path,
    _group_pid_field,
    _is_within,
    resolve_path,
    load_data,
    load_verb_group_log,
    load_schema,
    resolve_run_id_to_test_type,
    get_verb_schema,
    fs_exists,
    fs_is_file,
    fs_iterdir,
    fs_mkdirs,
    fs_remove,
    fs_open_writebin,
    fs_stat_size,
    _ALLOWED_EXTS,
    log,
)

# -----------------------------------------------------------------------------
# Raw file uploads (per-pocket; zero processing)
# -----------------------------------------------------------------------------

def _validate_filename(name: str) -> str:
    if not name:
        raise AppError("FILENAME_REQUIRED", "Filename is required.", status=400)
    base = Path(name).name
    if base != name or ".." in name or base.startswith("."):
        raise AppError("INVALID_FILENAME", "Invalid filename.", status=400,
                       details={"filename": name})
    ext = Path(base).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise AppError("EXTENSION_NOT_ALLOWED", f"Extension {ext!r} not allowed.", status=400,
                       details={"filename": name, "extension": ext})
    return base

def _validate_pocket(project_path: Path, group: str, run_id: str, pocket: str) -> str:
    if not pocket or "/" in pocket or "\\" in pocket or pocket.startswith(".") or ".." in pocket:
        raise AppError("INVALID_POCKET_NAME", "Invalid pocket name.", status=400,
                       details={"pocket": pocket})
    pid_field = _group_pid_field(project_path, group)
    entries = load_verb_group_log(project_path, group) or []
    run = next((e for e in entries if str(e.get(pid_field)) == str(run_id)), None)
    if not run:
        raise AppError("RUN_NOT_FOUND", f"Run {run_id} not found in {group}", status=404,
                       details={"group": group, "run_id": run_id})

    verb_key = run.get("test_type") or run.get("verb")
    if not verb_key:
        raise AppError("VERB_NOT_SET", "Verb not set for this run.", status=400,
                       details={"group": group, "run_id": run_id})

    verb_types = load_schema(project_path, "verb") or {}
    vdef = verb_types.get(verb_key) or {}
    raw_inputs = (vdef.get("data_entry_schema", {}) or {}).get("raw_data_inputs", []) or []

    if pocket not in raw_inputs:
        raise AppError(
            "POCKET_NOT_DECLARED",
            f"Pocket {pocket!r} is not declared in raw_data_inputs for verb {verb_key!r}.",
            status=400,
            details={"pocket": pocket, "verb": verb_key, "group": group, "run_id": run_id},
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
    log.debug("[raw-upload] start", {
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
        # NOTE: on failure the upload gate is disabled (fail-open) — surface why.
        log.warning("[raw-upload] could not resolve verb/linear config; upload gating disabled",
                    {"group": group, "run_id": run_id}, exc_info=True)
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
                raise AppError("UPLOAD_LOCKED", "Uploads are locked until the Raw Data step is reached.",
                               status=409, details={"group": group, "run_id": run_id, "pocket": pocket})

            # If the raw step specifies a pocket source, enforce it only BEFORE the raw step is reached
            step_source = steps[raw_idx].get("source") if raw_idx is not None else None
            if raw_idx is not None and current_index < raw_idx and step_source:
                if str(step_source).strip().lower() != str(pocket).strip().lower():
                    raise AppError(
                        "UPLOAD_POCKET_RESTRICTED",
                        f"Uploads for raw data are restricted to the '{step_source}' pocket until that step is reached.",
                        status=409,
                        details={"group": group, "run_id": run_id, "pocket": pocket, "required_pocket": step_source},
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
        raise AppError(
            "POCKET_FILE_EXISTS",
            "A file already exists in this pocket. Check 'Allow overwrite' to replace it.",
            status=409,
            details={"group": group, "run_id": run_id, "pocket": pocket},
        )

    # Remove all existing files before writing the new one
    try:
        for f in existing_files:
            fs_remove(f)
            log.debug(f"[raw-upload][cleanup] removed old file {f}")
    except Exception as e:
        log.debug(f"[raw-upload][cleanup] failed: {e!r}")

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
                    raise AppError(
                        "UPLOAD_TOO_LARGE",
                        "Raw upload exceeds 3 MB limit.",
                        status=413,
                        details={"group": group, "run_id": run_id, "pocket": pocket,
                                 "max_bytes": max_bytes},
                    )
                out.write(chunk)
    finally:
        try:
            await file.close()
        except Exception:
            pass

    size = fs_stat_size(target) if fs_exists(target) else 0
    log.debug(f"[raw-upload] success {target} ({size} bytes) overwrite={overwrite}")
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
        raise AppError("FILE_NOT_FOUND", "File not found.", status=404,
                       details={"group": group, "run_id": run_id, "pocket": pocket,
                                "filename": base})

    try:
        fs_remove(target)
    except Exception as e:
        log.debug(f"[raw-delete] error removing {target}: {e!r}")
        raise AppError("FILE_DELETE_FAILED", f"Failed to delete file: {e!r}", status=500,
                       details={"group": group, "run_id": run_id, "pocket": pocket,
                                "filename": base})

    log.debug(f"[raw-delete] removed {target}")
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
        raise AppError("RUN_NOT_FOUND", f"Run {run_id} not found in {group}", status=404,
                       details={"group": group, "run_id": run_id})

    verb_key = run.get("test_type") or run.get("verb")
    verb_types = load_schema(project_path, "verb") or {}
    vdef = verb_types.get(verb_key) or {}
    raw_inputs = (vdef.get("data_entry_schema", {}) or {}).get("raw_data_inputs", []) or []

    return {"pockets": {p: list_one(p) for p in raw_inputs}}
