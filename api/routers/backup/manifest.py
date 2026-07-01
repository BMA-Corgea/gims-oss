# api/routers/backup/manifest.py
#
# Snapshot-manifest helpers: skeleton creation, per-file checksums, backup-dir
# lookup, S3-aware manifest load, and artifact validation. Moved VERBATIM from
# the former single-file api/routers/backup.py (no logic changes).

from pathlib import Path
from typing import Optional, Tuple

from core.errors import AppError

from ._router import log
from .paths import _new_id, _now_iso, _backups_root
from .fsio import _sha256_file, _write_text, _read_json_s3


# ──────────────────────────────────────────────────────────────────────────────
# Manifest helpers (S3-aware JSON)
# ──────────────────────────────────────────────────────────────────────────────
def _manifest_skeleton(project: str, btype: str, created_by="system") -> dict:
    return {
        "backup_id": _new_id("bkp-"),
        "project": project,
        "type": btype,
        "created_at": _now_iso(),
        "created_by": created_by,
        "engine": {"app_version": None, "migration": None, "git": None},
        "artifacts": {},
        "retention_class": "ad-hoc",
        "destination": None,   # primary path (local backups folder or S3-mounted)
        "notes": None,
    }

def _write_checksums_txt(folder: Path, manifest: dict):
    lines = []
    artifacts = manifest.get("artifacts", {})
    if "project_zip" in artifacts:
        entry = artifacts["project_zip"]
        lines.append(f"{entry.get('sha256')}  {entry.get('path')}")
    db_map = (artifacts.get("db") or {})
    for key, meta in db_map.items():
        if meta.get("backend") == "sqlite":
            db_rel = f"db/{key}.sqlite"
            p = Path(folder) / db_rel
            if p.exists():
                lines.append(f"{_sha256_file(p)}  {db_rel}")
    if lines:
        _write_text(folder / "checksums.txt", "\n".join(lines) + "\n")

def _find_backup_dir(project: str, backup_id: str, base: Optional[Path] = None) -> Path | None:
    root = (base or _backups_root()) / project
    if not root.exists():
        return None
    for dated in sorted(root.iterdir()):
        if not dated.is_dir():
            continue
        candidate = dated / backup_id
        if candidate.exists():
            return candidate
    return None

def _load_manifest(project: str, backup_id: str, base: Optional[Path] = None) -> Tuple[dict, Path]:
    bdir = _find_backup_dir(project, backup_id, base=base)
    if not bdir:
        raise AppError("BACKUP_NOT_FOUND", "Backup not found", status=404,
                       details={"project": project, "backup_id": backup_id})
    mpath = bdir / "SNAPSHOT_MANIFEST.json"
    # S3-aware read:
    manifest = _read_json_s3(mpath)
    return manifest, bdir

def _validate_artifacts(project: str, backup_id: str, base: Optional[Path] = None) -> dict:
    log.debug("=" * 90)
    log.debug(f"[VALIDATE][BEGIN] project={project!r}, backup_id={backup_id!r}")

    try:
        manifest, bdir = _load_manifest(project, backup_id, base=base)
        log.debug(f"[VALIDATE][LOAD] Manifest loaded from {bdir}")
    except Exception as e:
        log.debug(f"[VALIDATE][ERROR] Failed to load manifest: {e}")
        raise AppError("MANIFEST_LOAD_FAILED", f"Could not load manifest: {e}", status=500,
                       details={"project": project, "backup_id": backup_id})

    results = {"project_zip": None, "db": {}, "ok": True}
    artifacts = manifest.get("artifacts", {})
    log.debug(f"[VALIDATE][MANIFEST] artifact keys = {list(artifacts.keys())}")

    # Validate project.zip
    try:
        if "project_zip" in artifacts:
            meta = artifacts["project_zip"]
            path = bdir / meta["path"]
            log.debug(f"[VALIDATE][ZIP] Checking project.zip → {path}")

            if not path.exists():
                log.debug(f"[VALIDATE][ZIP][FAIL] Missing file: {path}")
                results["project_zip"] = {"ok": False, "error": "missing"}
                results["ok"] = False
            else:
                sha = _sha256_file(path)
                expected = meta.get("sha256")
                ok = sha == expected
                results["project_zip"] = {"ok": ok, "sha256": sha, "expected": expected}
                log.debug(f"[VALIDATE][ZIP][OK={ok}] sha256={sha}, expected={expected}")
                if not ok:
                    results["ok"] = False
        else:
            log.debug("[VALIDATE][ZIP][SKIP] No project_zip entry in manifest")
    except Exception as e:
        log.debug(f"[VALIDATE][ZIP][ERROR] Exception during zip validation: {e}")
        results["ok"] = False

    # Validate DB artifacts
    db_map = artifacts.get("db") or {}
    log.debug(f"[VALIDATE][DB] Found {len(db_map)} DB entries: {list(db_map.keys())}")

    for key, meta in db_map.items():
        backend = meta.get("backend", "?")
        log.debug(f"[VALIDATE][DB][{key}] backend={backend} meta={meta}")
        try:
            if backend == "sqlite":
                path = bdir / "db" / f"{key}.sqlite"
                if not path.exists():
                    log.debug(f"[VALIDATE][DB][{key}][FAIL] Missing {path}")
                    results["db"][key] = {"ok": False, "error": "missing"}
                    results["ok"] = False
                else:
                    sha = _sha256_file(path)
                    results["db"][key] = {"ok": True, "sha256": sha}
                    log.debug(f"[VALIDATE][DB][{key}][OK] sha256={sha}")

            elif backend == "pg":
                dir_rel = meta.get("dir", f"db/{key}")
                if not dir_rel.startswith("db/"):
                    dir_rel = f"db/{dir_rel}"
                d = bdir / dir_rel
                log.debug(f"[VALIDATE][DB][{key}] Checking PG dir {d}")
                if not d.exists():
                    results["db"][key] = {"ok": False, "error": f"Missing directory {d}"}
                    results["ok"] = False
                    log.debug(f"[VALIDATE][DB][{key}][FAIL] Missing directory {d}")
                else:
                    csvs = list(d.glob("*.csv"))
                    results["db"][key] = {"ok": len(csvs) > 0, "files": len(csvs)}
                    log.debug(f"[VALIDATE][DB][{key}][OK] {len(csvs)} csv files")
                    if len(csvs) == 0:
                        results["ok"] = False

            else:
                log.debug(f"[VALIDATE][DB][{key}][FAIL] Unknown backend: {backend}")
                results["db"][key] = {"ok": False, "error": f"unknown backend '{backend}'"}
                results["ok"] = False

        except Exception as e:
            log.debug(f"[VALIDATE][DB][{key}][ERROR] Exception: {e}")
            results["db"][key] = {"ok": False, "error": str(e)}
            results["ok"] = False

    log.debug(f"[VALIDATE][SUMMARY] ok={results['ok']} project_zip={results['project_zip']} db={list(results['db'].keys())}")
    log.debug("=" * 90)
    return results
