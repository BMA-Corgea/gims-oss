# api/routers/backup/restore.py
#
# Restore engine: clone a backup into a new project (files from project.zip +
# SQLite snapshots and/or Postgres CSV dumps). Moved VERBATIM from the former
# single-file api/routers/backup.py (no logic changes).

from pathlib import Path
from typing import Optional
from datetime import datetime
import zipfile
import shutil

from core.errors import AppError

from ._router import log
from .paths import _project_path
from .manifest import _load_manifest
from .pg_dump import _get_key_dsn, _pg_conn


def _clone_restore(project: str, backup_id: str, new_project: Optional[str], scope: Optional[str]) -> dict:
    manifest, bdir = _load_manifest(project, backup_id)
    src_project_path = _project_path(project)
    if not src_project_path.exists():
        log.debug("warning: original project path not found, continuing:", src_project_path)
    target_name = new_project or f"{project} (restored {datetime.utcnow().strftime('%Y-%m-%d_%H%M%S')})"
    safe_name = "".join(ch for ch in target_name if ch.isalnum() or ch in ("-", "_", " ", ".")).strip()
    if not safe_name:
        raise AppError("INVALID_PROJECT_NAME", "Invalid new project name", status=400,
                       details={"requested_name": target_name})
    target_path = _project_path(safe_name)
    if target_path.exists():
        raise AppError("PROJECT_ALREADY_EXISTS", "Target project already exists", status=409,
                       details={"project": safe_name})
    log.debug("clone restore target:", target_path)
    target_path.mkdir(parents=True, exist_ok=False)
    scope = (scope or "").lower().strip() or None

    # Files restore (project.zip)
    if scope != "db_only" and "project_zip" in manifest.get("artifacts", {}):
        zip_meta = manifest["artifacts"]["project_zip"]
        zip_path = bdir / zip_meta["path"]
        if not zip_path.exists():
            raise AppError("BACKUP_ZIP_MISSING", "project.zip missing in backup", status=500,
                           details={"project": project, "backup_id": backup_id})
        log.debug("extracting ZIP:", zip_path)
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(target_path)

    # DB restore: SQLite snapshots and/or Postgres CSV dumps
    if scope != "files_only":
        db_map = manifest.get("artifacts", {}).get("db") or {}
        for key, meta in db_map.items():
            backend = meta.get("backend")

            if backend == "sqlite":
                snap_path = bdir / "db" / f"{key}.sqlite"
                if not snap_path.exists():
                    log.debug("sqlite artifact missing, skipping:", key)
                    continue
                rel = meta.get("original_rel")
                if not rel:
                    log.debug("sqlite artifact missing original_rel:", key)
                    continue
                dest = target_path / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                log.debug("placing sqlite:", snap_path, "->", dest)
                shutil.copy2(snap_path, dest)
                continue

            if backend == "pg":
                dsn = _get_key_dsn(key)
                if not dsn:
                    log.debug(f"[pg][restore] no DSN for {key}, skipping")
                    continue
                dir_rel = meta.get("dir", f"db/{key}")
                src_dir = bdir / dir_rel
                if not src_dir.exists():
                    log.debug(f"[pg][restore] missing dump folder for {key}, skipping")
                    continue
                log.debug(f"[pg][restore] restoring {key} from {src_dir} → {dsn}")
                try:
                    with _pg_conn(dsn) as conn:
                        with conn.cursor() as cur:
                            for csv_path in sorted(src_dir.glob("*.csv")):
                                table_name = Path(csv_path.name).stem.replace('"', "")
                                sql = f'COPY public."{table_name}" FROM STDIN WITH CSV HEADER'
                                log.debug(f"[pg][restore][COPY] {csv_path.name} → {table_name}")
                                with open(csv_path, "r", encoding="utf-8") as f:
                                    try:
                                        cur.copy(sql, f.read())
                                    except Exception as e:
                                        log.debug(f"[pg][restore][error] {table_name}: {e}")
                        conn.commit()
                except Exception as e:
                    log.debug(f"[pg][restore] failed for {key}: {e}")
                continue

    return {
        "ok": True,
        "restored_to": target_path.as_posix(),
        "backup_id": backup_id,
        "project": project,
        "new_project": safe_name,
    }
