# api/routers/backup/local_capture.py
#
# Local capture engine: project-tree ZIP creation + SQLite hot-copy snapshots.
# Moved VERBATIM from the former single-file api/routers/backup.py (no logic
# changes). KNOWN_DB_KEYS lives here (the canonical DB-key list).

from pathlib import Path
from typing import Tuple, Dict
import zipfile
import sqlite3
import shutil
import os

from api.manifest.resolver import resolve_path  # RDS-aware resolver

from ._router import log
from .fsio import _sha256_file


# ──────────────────────────────────────────────────────────────────────────────
# ZIP creation (project tree)
# ──────────────────────────────────────────────────────────────────────────────
DEFAULT_EXCLUDES = {
    "__pycache__",
    ".venv",
    ".git",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".DS_Store",
    ".idea",
    ".vscode",
    "backups",  # extra safety if someone put backups/ inside a project by mistake
}

def _should_exclude(rel_parts: Tuple[str, ...]) -> bool:
    return any(seg in DEFAULT_EXCLUDES for seg in rel_parts)

def _zip_project_tree(project_path: Path, out_zip: Path) -> dict:
    log.debug("zipping project:", project_path, "->", out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    cnt = 0
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if not _should_exclude(Path(root, d).relative_to(project_path).parts)]
            for fname in files:
                fpath = Path(root) / fname
                rel = fpath.relative_to(project_path)
                if _should_exclude(rel.parts):
                    continue
                z.write(fpath, arcname=str(rel).replace("\\", "/"))
                cnt += 1
    size = out_zip.stat().st_size
    sha = _sha256_file(out_zip)
    log.debug(f"zip done: {cnt} files, size={size}, sha256={sha}")
    return {"path": out_zip.name, "sha256": sha, "size": size, "files": cnt}

# ──────────────────────────────────────────────────────────────────────────────
# SQLite hot copy (legacy/offline)
# ──────────────────────────────────────────────────────────────────────────────
KNOWN_DB_KEYS = [
    "object_sql_db",   # nouns/objects database
    "archive_sql_db",  # archive database
    "nodes_db",        # audit/compliance logs
    "logins_db",       # accounts & projects
]

def _sqlite_quick_check(db: Path) -> str:
    """Return 'ok' if `db` passes SQLite's quick_check, else the first error line.
    Best-effort: a connect/exec failure (e.g. severe corruption) is itself a failure."""
    try:
        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute("PRAGMA quick_check;").fetchone()
            return (row[0] if row else "no result") or "no result"
        finally:
            conn.close()
    except Exception as e:
        return f"quick_check failed: {e!r}"


def _sqlite_hot_copy(src: Path, dest: Path) -> dict:
    log.debug("sqlite hot copy:", src, "->", dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ok = False
    try:
        src_conn = sqlite3.connect(str(src))
        try:
            dest_conn = sqlite3.connect(str(dest))
            with dest_conn:
                src_conn.backup(dest_conn)
            dest_conn.close()
            ok = True
        finally:
            src_conn.close()
    except Exception as e:
        log.debug("sqlite backup() failed, falling back to copy2:", e)
    if not ok:
        shutil.copy2(src, dest)
    size = dest.stat().st_size
    sha = _sha256_file(dest)
    rel = str(dest.relative_to(dest.parent.parent)).replace("\\", "/")
    # Validate the snapshot so a corrupt source can't silently propagate into every
    # backup undetected (as happened with archive_sql_db). The artifact is still kept
    # so a partial salvage is possible, but the corruption is recorded + warned on.
    integrity = _sqlite_quick_check(dest)
    if integrity != "ok":
        log.warning(f"backup snapshot integrity check FAILED for {dest.name}: {integrity}")
    return {"path": rel, "sha256": sha, "size": size, "integrity": integrity}

def _collect_sqlite_artifacts(project_path: Path, db_out_dir: Path) -> Dict[str, dict]:
    artifacts: Dict[str, dict] = {}
    for key in KNOWN_DB_KEYS:
        try:
            src = resolve_path(project_path, key)
        except Exception as e:
            log.debug(f"resolve_path failed for {key}:", e)
            continue
        if not src.exists():
            log.debug(f"db not found for {key} ->", src)
            continue

        try:
            original_rel = str(src.relative_to(project_path)).replace("\\", "/")
        except ValueError:
            log.debug(f"db '{key}' is outside project path, skipping sqlite backup:", src)
            continue  # Skip this key

        dest = db_out_dir / f"{key}.sqlite"
        info = _sqlite_hot_copy(src, dest)
        info["original_rel"] = original_rel
        info["key"] = key
        info["backend"] = "sqlite"
        artifacts[key] = info
        log.debug("db snapshot:", key, "->", info)
    return artifacts
