# api/routers/backup/fsio.py
#
# Hashing + (S3-aware) JSON/file I/O helpers. Moved VERBATIM from the former
# single-file api/routers/backup.py (no logic changes).

from pathlib import Path
from typing import Any
import hashlib
import json

# Import i_o to leverage its S3-aware JSON helpers (save_json/read_json) when available.
from api import i_o

from ._router import log


# ──────────────────────────────────────────────────────────────────────────────
# Hashing & local fs utils
# (Note: JSON I/O is S3-aware via helpers further below)
# ──────────────────────────────────────────────────────────────────────────────
def _sha256_file(path: Path) -> str:
    log.debug("hashing:", path)
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _write_text(path: Path, text: str, encoding="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)

# Legacy JSON helpers (kept for fallback) — will be bypassed by S3-aware wrappers
def _write_json(path: Path, obj: Any):
    _write_text(path, json.dumps(obj, indent=2))

def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception as e:
        log.debug("read_json error:", path, e)
        return default

# ──────────────────────────────────────────────────────────────────────────────
# S3-aware JSON wrappers
# (Mirror the approach from adjective/adverb GUIs: lean on api.i_o when present)
# ──────────────────────────────────────────────────────────────────────────────
def _save_json_s3(path: Path, obj: Any):
    """
    S3-aware JSON write. Uses i_o.save_json if available (which routes to S3 when
    the path is under a managed root, e.g., projects/ or backups/), falling back
    to local write otherwise.
    """
    try:
        if hasattr(i_o, "save_json"):
            log.debug("[json][save] via i_o.save_json →", path)
            i_o.save_json(path, obj)
        else:
            log.debug("[json][save] fallback local →", path)
            _write_json(path, obj)
    except Exception as e:
        log.debug("[json][save][error]", path, e)
        raise

def _read_json_s3(path: Path, default: Any = None) -> Any:
    """
    S3-aware JSON read. Uses i_o.read_json if available, else falls back to
    local read. If default is provided and the file is missing, returns default.
    """
    try:
        if hasattr(i_o, "read_json"):
            log.debug("[json][read] via i_o.read_json ←", path)
            return i_o.read_json(path, default=default) if "default" in i_o.read_json.__code__.co_varnames else i_o.read_json(path)
        # Fallback to local handling
        if default is not None:
            return _read_json(path, default)
        return json.loads(path.read_text())
    except FileNotFoundError:
        if default is not None:
            return default
        raise
    except Exception as e:
        log.debug("[json][read][error]", path, e)
        if default is not None:
            return default
        raise
