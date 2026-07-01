# api/routers/backup/routes_downloads.py
#
# Download routes (project.zip + per-DB artifacts). Handlers moved VERBATIM from
# the former single-file api/routers/backup.py (no logic changes). Registered
# AFTER routes_backups and BEFORE routes_schedules so the route REGISTRATION
# ORDER matches the original file.

from fastapi import Query
from fastapi.responses import FileResponse
import os
import zipfile

from core.errors import AppError

from ._router import router, log
from .paths import _repo_root, _projects_root, _backups_root
from .fsio import _sha256_file
from .manifest import _load_manifest


# ──────────────────────────────────────────────────────────────────────────────
# Downloads
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/download/{backup_id}/project.zip")
def download_zip(backup_id: str, project: str = Query(...)):
    log.debug("=" * 90)
    log.debug(f"[download][BEGIN] project.zip request for project='{project}', backup_id='{backup_id}'")
    try:
        manifest, bdir = _load_manifest(project, backup_id)
        log.debug(f"[download] manifest loaded from {_backups_root()} -> {bdir}")
    except Exception as e:
        log.debug(f"[download][error] failed to load manifest: {e}")
        raise

    log.debug("[download][manifest] keys:", list(manifest.keys()))
    artifacts = manifest.get("artifacts", {})
    log.debug("[download][manifest.artifacts] keys:", list(artifacts.keys()))

    zip_meta = artifacts.get("project_zip", {})
    log.debug("[download][zip_meta]", zip_meta)

    zip_rel = zip_meta.get("path", "project.zip")
    z = bdir / zip_rel
    log.debug(f"[download][path] expected zip file: {z}")

    try:
        all_entries = [p.relative_to(bdir) for p in bdir.rglob("*")]
        log.debug(f"[download][bdir content] {len(all_entries)} entries under {bdir}")
        for p in all_entries[:50]:
            log.debug("   ↳", p)
        if len(all_entries) > 50:
            log.debug("   ... (truncated)")
    except Exception as e:
        log.debug(f"[download][error] failed to enumerate {bdir}: {e}")

    if not z.exists():
        log.debug(f"[download][missing] file not found: {z}")
        log.debug(f"[download][cwd] os.getcwd()={os.getcwd()}")
        log.debug(f"[download][repo_root]={_repo_root()}")
        log.debug(f"[download][backups_root]={_backups_root()}")
        log.debug(f"[download][projects_root]={_projects_root()}")
        alt_zips = list(bdir.glob("*.zip"))
        log.debug(f"[download][alt candidates] found {len(alt_zips)} zips: {[p.name for p in alt_zips]}")
        raise AppError("BACKUP_ZIP_NOT_FOUND", "project.zip not found in backup", status=404,
                       details={"project": project, "backup_id": backup_id})

    try:
        sz = z.stat().st_size
        sha = _sha256_file(z)
        log.debug(f"[download][file stats] size={sz:,} bytes, sha256={sha}")
    except Exception as e:
        log.debug(f"[download][error] failed to stat/hash file {z}: {e}")

    db_artifacts = artifacts.get("db") or {}
    log.debug(f"[download][db_artifacts] total={len(db_artifacts)}")
    for key, meta in db_artifacts.items():
        log.debug(f"    {key} backend={meta.get('backend')} dir={meta.get('dir')} path={bdir / 'db' / (meta.get('dir') or key)}")

    log.debug(f"[download][SERVE] returning FileResponse({z}) as {project}__{backup_id}__project.zip")
    log.debug("=" * 90)
    return FileResponse(z, filename=f"{project}__{backup_id}__project.zip")

@router.get("/download/{backup_id}/db/{key}")
def download_db_any(backup_id: str, key: str, project: str = Query(...)):
    manifest, bdir = _load_manifest(project, backup_id)
    meta = (manifest.get("artifacts", {}).get("db") or {}).get(key)
    if not meta:
        raise AppError("DB_ARTIFACT_NOT_FOUND", f"No DB artifact found for key '{key}'", status=404,
                       details={"project": project, "backup_id": backup_id, "key": key})
    backend = meta.get("backend", "sqlite")

    if backend == "sqlite":
        p = bdir / "db" / f"{key}.sqlite"
        if not p.exists():
            raise AppError("DB_ARTIFACT_NOT_FOUND", f"{key}.sqlite not found in backup", status=404,
                           details={"project": project, "backup_id": backup_id, "key": key})
        log.debug(f"[download] serving SQLite {p}")
        return FileResponse(p, filename=f"{project}__{backup_id}__{key}.sqlite")

    if backend == "pg":
        dir_from_manifest = meta.get("dir", key)
        d = bdir / "db" / dir_from_manifest
        if not d.exists():
            err_path = f"db/{dir_from_manifest}"
            log.debug(f"[download] Dump folder missing at expected path: {d}")
            raise AppError("DB_DUMP_FOLDER_MISSING", f"Dump folder missing: {err_path}", status=404,
                           details={"project": project, "backup_id": backup_id, "key": key, "path": err_path})
        out_zip = bdir / "db" / f"{key}.pg.zip"
        if not out_zip.exists():
            out_zip.parent.mkdir(parents=True, exist_ok=True)
            log.debug(f"[download] zipping Postgres dump {d} -> {out_zip}")
            with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for p in sorted(d.glob("*.csv")):
                    z.write(p, arcname=p.name)
        log.debug(f"[download] serving Postgres zip {out_zip}")
        return FileResponse(out_zip, filename=f"{project}__{backup_id}__{key}.pg.zip")

    raise AppError("UNSUPPORTED_DB_BACKEND", f"Unsupported backend type '{backend}'", status=400,
                   details={"project": project, "backup_id": backup_id, "key": key, "backend": backend})
