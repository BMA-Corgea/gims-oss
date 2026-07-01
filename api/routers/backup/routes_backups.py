# api/routers/backup/routes_backups.py
#
# Project + backup lifecycle routes (list / manifest / backup-now / validate /
# restore / delete). Handlers moved VERBATIM from the former single-file
# api/routers/backup.py (no logic changes). Registered FIRST so the route
# REGISTRATION ORDER matches the original file.

from fastapi import Body, Query
from pathlib import Path
from typing import Dict, Any
import zipfile
import os
import shutil

from core.errors import AppError
from api import i_o

from ._router import router, log
from .paths import _project_path, _backups_root, _dated_backup_dir
from .fsio import _sha256_file, _save_json_s3, _read_json_s3
from .local_capture import _collect_sqlite_artifacts, _zip_project_tree
from .pg_dump import _collect_pg_artifacts
from .manifest import (
    _manifest_skeleton,
    _write_checksums_txt,
    _find_backup_dir,
    _load_manifest,
    _validate_artifacts,
)
from .restore import _clone_restore
from .models import BackupNowRequest, RestoreRequest


# ──────────────────────────────────────────────────────────────────────────────
# Routes: Projects
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return i_o.io_list_projects()
    except Exception:
        # Optional: return empty list on failure instead of 500
        log.warning("[list_projects] failed to list projects", exc_info=True)
        return []

# ──────────────────────────────────────────────────────────────────────────────
# Routes: Backups
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/backups")
def list_backups(project: str = Query(..., description="Project name under /projects")):
    root = _backups_root() / project
    if not root.exists():
        return {"project": project, "backups": []}
    entries = []
    for dated in sorted(root.iterdir(), reverse=True):
        if not dated.is_dir():
            continue
        for bdir in sorted(dated.iterdir(), reverse=True):
            if not bdir.is_dir():
                continue
            m = bdir / "SNAPSHOT_MANIFEST.json"
            if not m.exists():
                continue
            try:
                # S3-aware manifest read:
                manifest = _read_json_s3(m)
                size = None
                zmeta = (manifest.get("artifacts", {}).get("project_zip") or {})
                zpath = bdir / zmeta.get("path", "project.zip")
                if zpath.exists():
                    try:
                        size = zpath.stat().st_size
                    except Exception:
                        size = None
                entries.append({
                    "backup_id": manifest.get("backup_id") or bdir.name,
                    "created_at": manifest.get("created_at"),
                    "type": manifest.get("type"),
                    "notes": manifest.get("notes"),
                    "dated_dir": dated.name,
                    "size_bytes": size
                })
            except Exception as e:
                log.debug("manifest parse error:", m, e)
                continue
    return {"project": project, "backups": entries}

@router.get("/backups/{backup_id}")
def get_backup_manifest(backup_id: str, project: str = Query(...)):
    manifest, bdir = _load_manifest(project, backup_id)
    manifest["_paths"] = {
        "folder": bdir.as_posix(),
        "project_zip": (bdir / (manifest.get("artifacts", {}).get("project_zip") or {}).get("path", "project.zip")).as_posix(),
    }
    return manifest

@router.post("/backup-now")
def backup_now(req: BackupNowRequest = Body(...)):
    log.debug("="*80)
    log.debug("[BACKUP_NOW_START]")
    log.debug("[1. PROJECT] Validating project path...")
    project_path = _project_path(req.project)
    if not project_path.exists():
        log.debug(f"[1. PROJECT][ERROR] Project path not found: {project_path}")
        raise AppError("PROJECT_NOT_FOUND", f"Project '{req.project}' not found", status=404,
                       details={"project": req.project})
    log.debug(f"[1. PROJECT][OK] Project path found: {project_path}")

    log.debug("[2. MANIFEST] Creating skeleton...")
    manifest = _manifest_skeleton(req.project, req.type, created_by="(api)")
    if req.notes:
        manifest["notes"] = req.notes
    log.debug(f"[2. MANIFEST][OK] Skeleton created. backup_id={manifest['backup_id']}")

    log.debug("[3. FOLDERS] Creating backup directory structure...")
    backup_dir = _dated_backup_dir(req.project, manifest["backup_id"])
    db_out_dir = backup_dir / "db"
    artifacts: Dict[str, Any] = {}
    log.debug(f"[3. FOLDERS][OK] Backup root created: {backup_dir}")
    log.debug(f"[3. FOLDERS][OK] DB output dir set: {db_out_dir}")

    log.debug(f"[4. DB] Collecting DB artifacts (type={req.type})...")
    if req.type in {"sqlite", "hybrid"}:
        log.debug("[4a. DB-PG] Checking for Postgres artifacts...")
        pg_map = _collect_pg_artifacts(req.project, project_path, db_out_dir)
        log.debug(f"[4a. DB-PG][OK] Postgres artifact collection complete. Found {len(pg_map)} DBs.")

        log.debug("[4b. DB-SQLITE] Checking for SQLite artifacts...")
        sqlite_map = _collect_sqlite_artifacts(project_path, db_out_dir)
        log.debug(f"[4b. DB-SQLITE][OK] SQLite artifact collection complete. Found {len(sqlite_map)} DBs.")

        log.debug("[4c. DB-MERGE] Merging DB artifact maps (PG overrides SQLite)...")
        db_map: Dict[str, Any] = {}
        db_map.update(sqlite_map)
        db_map.update(pg_map)
        artifacts["db"] = db_map
        log.debug(f"[4c. DB-MERGE][OK] Merge complete. Final DB keys: {list(db_map.keys())}")
    else:
        log.debug(f"[4. DB][SKIP] Skipping DB artifact collection (type is '{req.type}')")

    log.debug(f"[5. ZIP-CREATE] Creating initial project.zip (type={req.type})...")
    if req.type in {"zip", "hybrid"}:
        zip_path = backup_dir / "project.zip"
        log.debug(f"[5. ZIP-CREATE] Zipping project tree from {project_path} -> {zip_path}")
        zip_info = _zip_project_tree(project_path, zip_path)
        log.debug(f"[5. ZIP-CREATE][OK] Initial zip created. Size={zip_info['size']}, Files={zip_info['files']}, SHA={zip_info['sha256']}")

        log.debug(f"[6. ZIP-APPEND] Appending 'db' folder ({db_out_dir}) to {zip_path}...")
        file_count_added = 0
        if db_out_dir.exists() and any(db_out_dir.iterdir()):
            log.debug("[6. ZIP-APPEND] 'db' folder exists and is not empty. Opening zip in 'a' (append) mode...")
            try:
                with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                    log.debug("[6. ZIP-APPEND] Walking {db_out_dir} to find files to add...")
                    for root, _, files in os.walk(db_out_dir):
                        for fname in files:
                            fpath = Path(root) / fname
                            arcname = str(fpath.relative_to(backup_dir)).replace("\\", "/")
                            if arcname.endswith(".pg.zip"):
                                log.debug(f"  -> [SKIP] Ignoring lazy-zip file: {arcname}")
                                continue
                            log.debug(f"  -> [ADD] Adding {fpath.name} as {arcname}")
                            z.write(fpath, arcname=arcname)
                            file_count_added += 1
                log.debug(f"[6. ZIP-APPEND][OK] Finished appending. Added {file_count_added} files.")
            except Exception as e:
                log.debug(f"[6. ZIP-APPEND][ERROR] Failed to append files to zip: {e}")
                raise
        else:
            log.debug("[6. ZIP-APPEND][SKIP] 'db' folder is missing or empty. Nothing to append.")

        log.debug("[7. ZIP-RECALC] Recalculating final zip info (size, sha, file count)...")
        new_size = zip_path.stat().st_size
        log.debug(f"[7. ZIP-RECALC]  -> New size: {new_size}")
        new_sha = _sha256_file(zip_path)
        log.debug(f"[7. ZIP-RECALC]  -> New SHA: {new_sha}")
        final_file_count = 0
        try:
            log.debug("[7. ZIP-RECALC] Opening zip in 'r' mode to count files...")
            with zipfile.ZipFile(zip_path, "r") as z:
                final_file_count = len(z.infolist())
            log.debug(f"[7. ZIP-RECALC]  -> New file count: {final_file_count}")
        except Exception as e:
            log.debug(f"[7. ZIP-RECALC][WARN] Could not read zip for file count: {e}. Falling back to estimate.")
            final_file_count = zip_info.get("files", 0) + file_count_added
            log.debug(f"[7. ZIP-RECALC]  -> Estimated file count: {final_file_count}")

        zip_info["size"] = new_size
        zip_info["files"] = final_file_count
        zip_info["sha256"] = new_sha
        log.debug(f"[7. ZIP-RECALC][OK] Final zip info: size={new_size}, files={final_file_count}, sha={new_sha}")
        artifacts["project_zip"] = zip_info
    else:
        log.debug(f"[5. ZIP-CREATE][SKIP] Skipping zip creation (type is '{req.type}')")

    log.debug("[8. MANIFEST-WRITE] Writing final manifest...")
    manifest["artifacts"] = artifacts
    manifest["destination"] = backup_dir.as_posix()
    # S3-aware write:
    _save_json_s3(backup_dir / "SNAPSHOT_MANIFEST.json", manifest)
    log.debug("[8. MANIFEST-WRITE][OK] SNAPSHOT_MANIFEST.json written.")

    if req.paranoid:
        log.debug("[9. CHECKSUMS] Paranoid mode: writing checksums.txt...")
        _write_checksums_txt(backup_dir, manifest)
        log.debug("[9. CHECKSUMS][OK] checksums.txt written.")
    else:
        log.debug("[9. CHECKSUMS][SKIP] Paranoid mode off.")

    log.debug("[10. COMPLETE] Backup complete.")
    log.debug("="*80)
    return {
        "ok": True,
        "backup_id": manifest["backup_id"],
        "project": req.project,
        "folder": backup_dir.as_posix(),
        "manifest": manifest,
    }

@router.post("/validate/{backup_id}")
def validate_backup(backup_id: str, project: str = Body(..., embed=True)):
    results = _validate_artifacts(project, backup_id)
    return {"project": project, "backup_id": backup_id, **results}

@router.post("/restore/{backup_id}")
def restore_backup(backup_id: str, req: RestoreRequest = Body(...)):
    if req.mode not in {"clone", "inplace"}:
        raise AppError("INVALID_RESTORE_MODE", "mode must be clone | inplace", status=400,
                       details={"mode": req.mode})
    if req.mode == "inplace":
        raise AppError("RESTORE_MODE_NOT_IMPLEMENTED", "in-place restore not implemented yet", status=400,
                       details={"mode": req.mode})
    return _clone_restore(req.project, backup_id, req.new_project, req.scope)

@router.delete("/backups/{backup_id}")
def delete_backup(backup_id: str, project: str = Body(..., embed=True)):
    bdir = _find_backup_dir(project, backup_id)
    if not bdir:
        raise AppError("BACKUP_NOT_FOUND", "Backup not found", status=404,
                       details={"project": project, "backup_id": backup_id})
    log.debug("deleting backup:", bdir)
    shutil.rmtree(bdir)
    return {"ok": True, "deleted": bdir.as_posix(), "project": project, "backup_id": backup_id}
