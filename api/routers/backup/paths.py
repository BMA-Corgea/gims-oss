# api/routers/backup/paths.py
#
# Path/layout/id helpers (resolver-aware). Moved VERBATIM from the former
# single-file api/routers/backup.py (no logic changes).

from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
import uuid

from api.i_o import load_local_layout_map
from api.manifest.resolver import resolve_path  # RDS-aware resolver


# ──────────────────────────────────────────────────────────────────────────────
# Path helpers
# ──────────────────────────────────────────────────────────────────────────────
def _api_dir() -> Path:
    return Path(__file__).resolve().parent

def _repo_root() -> Path:
    from utils.paths import repo_root
    return repo_root()

def _layout_name() -> str:
    layout = load_local_layout_map(_api_dir())
    return layout.get("project_root", "projects")

def _projects_root() -> Path:
    # Prefer resolver if it provides a dedicated key; else fallback to local convention.
    try:
        return resolve_path(Path(), "project_root")
    except Exception:
        return _repo_root() / _layout_name()

def _project_path(project_name: str) -> Path:
    return _projects_root() / project_name

def _backups_root() -> Path:
    """
    Backups live beside /projects at repo root (avoid recursion). If the resolver
    supports a 'backups_root' key, prefer it to enable S3-backed layouts.
    """
    try:
        return resolve_path(Path(), "backups_root")
    except Exception:
        return _repo_root() / "backups"

def _cfg_root() -> Path:
    # Simple JSON-backed config store (used for schedules)
    return _ensure_dir(_backups_root() / "_config")

def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _now_iso() -> str:
    return _now_utc().replace(microsecond=0).isoformat()

def _new_id(prefix: str = "") -> str:
    return (prefix + uuid.uuid4().hex[:12]).lower()

def _dated_backup_dir(project: str, backup_id: str, base: Optional[Path] = None) -> Path:
    base = base or _backups_root()
    d = _now_utc()
    return _ensure_dir(base / project / f"{d:%Y-%m-%d}" / backup_id)
