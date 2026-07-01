# api/routers/runlog_workbench/downloads.py
"""Downloads (file & zip) — streaming via S3/local shims."""

import mimetypes
from pathlib import Path
from typing import List, Optional

from fastapi import Query
from fastapi.responses import StreamingResponse

from ._router import router
from ._shared import (
    AppError,
    get_project_path,
    _is_within,
    _ALLOWED_EXTS,
    fs_exists,
    fs_is_file,
    fs_iterdir,
    fs_open_readbin,
    make_zip_stream,
)
from .raw_files import _validate_pocket, _validate_filename, _pocket_dir_for_run
from .interpret import _run_dump_dir, _resolve_verb_name, _schema_tabs, _existing_tab_file

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
        raise AppError("FILE_NOT_FOUND", "File not found.", status=404,
                       details={"group": group, "run_id": run_id, "pocket": pocket,
                                "filename": base})

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
        raise AppError("TAB_NOT_DEFINED", f"Tab {tab!r} is not defined by verb {verb_name!r}.",
                       status=400, details={"tab": tab, "verb": verb_name})

    f = _existing_tab_file(dump_dir, tab)
    if not f or not fs_exists(f):
        raise AppError("INTERPRET_FILE_NOT_FOUND", "Interpretation file not found for this tab.",
                       status=404, details={"group": group, "run_id": run_id, "tab": tab})

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
        raise AppError("POCKET_EMPTY", "No files found in this pocket.", status=404,
                       details={"group": group, "run_id": run_id, "pocket": pocket})

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
        raise AppError("INTERPRET_FILES_NOT_FOUND", "No interpretation files found for the requested tabs.",
                       status=404, details={"group": group, "run_id": run_id, "tabs": wanted})

    buf = make_zip_stream([(f, f.name) for f in found])
    zip_name = f"{run_id}_interpretation.zip" if not tabs else f"{run_id}_interpretation_selected.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )
