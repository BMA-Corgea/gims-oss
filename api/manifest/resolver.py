from __future__ import annotations
from pathlib import Path
import json
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple
from importlib import import_module
from functools import lru_cache

# ------------------------------------------------------------
# CONTROLS
# ------------------------------------------------------------
# Backward-compat flags (kept but superseded by env-driven levels)
DEBUG_ENABLED = False
RDS_ENABLED = False

# Log levels
_LOG_LEVELS = {"OFF": 0, "ERROR": 1, "WARN": 2, "INFO": 3, "TRACE": 4}
_LOG_NAME = os.getenv("GIMS_RESOLVER_LOG", "INFO").upper()
LOG_LEVEL = _LOG_LEVELS.get(_LOG_NAME, 3)

# Sampling (for hot spammy lines): print first N, then every Mth
SAMPLE_FIRST = int(os.getenv("GIMS_RESOLVER_SAMPLE_FIRST", "3"))
SAMPLE_EVERY = int(os.getenv("GIMS_RESOLVER_SAMPLE_EVERY", "25"))

# Optional immediate flush
FORCE_FLUSH = os.getenv("GIMS_RESOLVER_FLUSH", "0") in ("1", "true", "TRUE")

# ------------------------------------------------------------
# LOGGING (fast + sampled + leveled)
# ------------------------------------------------------------
_seen_counts: Dict[Tuple[str, str], int] = {}

def _should_log(level: int) -> bool:
    if not DEBUG_ENABLED:
        return False
    return level <= LOG_LEVEL

def _fmt_payload(payload: Any) -> str:
    if payload is None:
        return ""
    try:
        # Limit huge payloads
        s = json.dumps(payload, ensure_ascii=False, default=str)
        if len(s) > 2000:
            s = s[:2000] + " …(truncated)"
        return s
    except Exception:
        s = str(payload)
        return (s[:2000] + " …(truncated)") if len(s) > 2000 else s

def _sampled_log(key: Tuple[str, str]) -> bool:
    """Return True if this occurrence should be printed (based on sampling)."""
    cnt = _seen_counts.get(key, 0) + 1
    _seen_counts[key] = cnt
    if cnt <= SAMPLE_FIRST:
        return True
    return (cnt - SAMPLE_FIRST) % SAMPLE_EVERY == 0

def _log(level_name: str, tag: str, payload: Any = None, sample_key: str | None = None) -> None:
    if not _should_log(_LOG_LEVELS[level_name]):
        return
    msg = f"[resolver] {tag}"
    if payload is not None:
        msg += " " + _fmt_payload(payload)

    # Sampling for hot paths
    if sample_key:
        key = (level_name, sample_key)
        if not _sampled_log(key):
            return

    try:
        sys.stdout.write(msg + "\n")
        if FORCE_FLUSH:
            sys.stdout.flush()
    except Exception:
        pass

def log_error(tag: str, payload: Any = None): _log("ERROR", tag, payload)
def log_warn(tag: str, payload: Any = None):  _log("WARN",  tag, payload)
def log_info(tag: str, payload: Any = None):  _log("INFO",  tag, payload)
def log_trace(tag: str, payload: Any = None, sample_key: str | None = None):
    _log("TRACE", tag, payload, sample_key=sample_key)

# Back-compat for existing imports/calls
def debug(*args, **kwargs):
    if not _should_log(_LOG_LEVELS["INFO"]):
        return
    # Preserve original "debug" feel at INFO level
    msg_parts = []
    for arg in args:
        try:
            msg_parts.append(json.dumps(arg, ensure_ascii=False, default=str))
        except Exception:
            msg_parts.append(str(arg))
    if kwargs:
        try:
            msg_parts.append(json.dumps(kwargs, ensure_ascii=False, default=str))
        except Exception:
            msg_parts.append(str(kwargs))
    try:
        sys.stdout.write("[resolver] " + " ".join(msg_parts) + "\n")
        if FORCE_FLUSH:
            sys.stdout.flush()
    except Exception:
        pass

# ------------------------------------------------------------
# PATHS & MANIFESTS
# ------------------------------------------------------------
def _resource_path(relative_path: str | Path) -> Path:
    try:
        base_path = Path(sys._MEIPASS)
    except Exception:
        base_path = Path(os.path.abspath("."))
    return base_path / Path(relative_path)

def _compute_repo_root() -> Path:
    rp = _resource_path("").resolve()
    # Avoid repeated upward walking
    while 'gims-oss' not in rp.name and rp.parent != rp:
        rp = rp.parent
    return rp

_REPO_ROOT = _compute_repo_root()

_manifest_dir = Path(__file__).parent
_layout_map_path = _manifest_dir / "local_layout_map.json"
_rds_manifest_path = _manifest_dir / "rds_manifest.json"

log_info("RDS_ENABLED flag", {"value": RDS_ENABLED})

if not _layout_map_path.exists():
    raise FileNotFoundError(f"Layout map not found at {_layout_map_path}")

layout_map = json.loads(_layout_map_path.read_text())
rds_manifest: Dict[str, Any] = {}
rds_resolver_module = None

if RDS_ENABLED:
    log_info("RDS mode", {"status": "ON", "checking": str(_rds_manifest_path)})
    if _rds_manifest_path.exists():
        rds_manifest = json.loads(_rds_manifest_path.read_text())
        resolver_module_name = rds_manifest.get("resolver_module")
        if resolver_module_name:
            try:
                rds_resolver_module = import_module(resolver_module_name)
                log_info("RDS resolver imported", {"module": resolver_module_name})
            except ImportError as e:
                log_warn("RDS resolver import failed", {"module": resolver_module_name, "error": repr(e)})
                rds_resolver_module = None
        else:
            log_warn("RDS manifest missing 'resolver_module' → fallback to SQLite")
    else:
        log_warn("RDS manifest not found → fallback to SQLite", {"path": str(_rds_manifest_path)})
else:
    log_info("RDS mode", {"status": "OFF", "behavior": "local SQLite"})

# ------------------------------------------------------------
# PUBLIC RESOLVER FUNCTIONS
# ------------------------------------------------------------
def is_rds_key(key: str) -> bool:
    """Return True if this key is configured for RDS in the layout map."""
    template = layout_map.get(key)
    is_rds = bool(template and str(template).startswith("RDS+"))
    # TRACE only (very chatty in hot paths)
    log_trace(f"is_rds_key({key})", {"is_rds": is_rds}, sample_key=f"is_rds_key:{key}:{is_rds}")
    return is_rds

@lru_cache(maxsize=64)
def _repo_root_for_logging() -> str:
    return str(_REPO_ROOT)

@lru_cache(maxsize=32)
def get_db_uri(key: str, project: Optional[str] = None) -> str:
    """
    Return a database connection URI.
    - If RDS_ENABLED and resolver module exists → delegate to cloud resolver.
    - Else → construct local SQLite file URI.
    """
    log_info("get_db_uri() called", {"key": key, "project": project})
    _is_rds = is_rds_key(key)
    should_use_rds = bool(RDS_ENABLED and rds_resolver_module and _is_rds)

    log_info("get_db_uri decision", {
        "RDS_ENABLED": RDS_ENABLED,
        "rds_resolver_loaded": bool(rds_resolver_module),
        "is_rds_key": _is_rds,
        "should_use_rds": should_use_rds,
    })

    # ---- Try RDS route
    if should_use_rds:
        try:
            uri = rds_resolver_module.get_rds_connection_uri(
                key, rds_manifest, project=project
            )
            log_info("RDS URI resolved", {"key": key, "project": project})
            return uri
        except Exception as e:
            log_warn("RDS resolver failed → fallback to SQLite", {"key": key, "project": project, "error": repr(e)})

    # ---- Fallback to local SQLite path
    base_path = Path()
    if project and key not in ("project_root", "logins_dir", "logins_db"):
        try:
            projects_root_dir = resolve_path(Path(), "project_root")
            base_path = projects_root_dir / str(project)
            log_trace("project base path", {"key": key, "base_path": str(base_path)}, sample_key=f"base:{key}")
        except Exception as e:
            log_warn("Could not resolve project base path → using root", {"project": project, "error": repr(e)})
            base_path = Path()

    local_path = resolve_path(base_path, key, project=project)
    sqlite_uri = f"sqlite+aiosqlite:///{local_path.as_posix()}"
    log_info("SQLite URI generated", {"key": key, "sqlite_uri": sqlite_uri})
    return sqlite_uri

# ------------------------------------------------------------
# resolve_path with memoization
# ------------------------------------------------------------
# Simple in-process memo for resolve_path results
_resolve_cache: Dict[Tuple[str, str, Tuple[Tuple[str, Any], ...]], Path] = {}

def _resolve_cache_key(project_path: Path, key: str, kwargs: Dict[str, Any]) -> Tuple[str, str, Tuple[Tuple[str, Any], ...]]:
    base = "<root>" if project_path == Path() else str(project_path)
    # sort kwargs for stable key
    items = tuple(sorted(kwargs.items()))
    return (base, key, items)

def resolve_path(project_path: Path, key: str, **kwargs) -> Path:
    """
    Resolve a logical key into a full, concrete local filesystem path.
    If the key is an RDS key, it resolves the *local* counterpart template.
    Memoized for speed; TRACE logs are sampled to avoid spam.
    """
    ck = _resolve_cache_key(project_path, key, kwargs)
    if ck in _resolve_cache:
        return _resolve_cache[ck]

    template = layout_map.get(key)
    if not template:
        raise KeyError(f"Layout key '{key}' not found in layout map.")

    # If it's an RDS key, use the part after "RDS+" as the local path template
    if str(template).startswith("RDS+"):
        template = template.split("+", 1)[1]

    # Root-level keys always resolve relative to repo root
    if key in ("project_root", "logins_dir", "logins_db", "wasm"):
        try:
            relative_path = template.format(**kwargs)
        except KeyError as e:
            raise ValueError(f"Missing placeholder '{e.args[0]}' for key '{key}'")
        full_path = _REPO_ROOT / relative_path
        _resolve_cache[ck] = full_path
        # TRACE only & sampled
        log_trace(f"resolve_path({key}) root →", str(full_path), sample_key=f"rp:{key}:root")
        return full_path

    try:
        relative_path = template.format(**kwargs)
    except KeyError as e:
        raise ValueError(f"Missing placeholder '{e.args[0]}' for key '{key}'")

    if project_path == Path():
        full_path = _resource_path(relative_path)
    else:
        full_path = project_path / relative_path

    _resolve_cache[ck] = full_path
    log_trace(f"resolve_path({key}) →", str(full_path), sample_key=f"rp:{key}")
    return full_path

# ------------------------------------------------------------
# UNCHANGED HELPERS
# ------------------------------------------------------------
def resolve_data_dump_contents(project_path: Path, verb_group: str, run_id: str) -> dict:
    base_dir = resolve_path(project_path, "data_dump_dir", verb_group=verb_group, run_id=run_id)
    if not base_dir.exists():
        raise FileNotFoundError(f"[resolver] Data dump not found: {base_dir}")

    out = {
        "files": {
            "data_entry": base_dir / "DataEntry.json",
            "status": base_dir / "Status.json",
            "adverbs": base_dir / "adverbs.json",
            "other_files": {}
        },
        "folders": {}
    }

    for item in base_dir.iterdir():
        if item.is_dir():
            files = [f for f in item.glob("*") if f.is_file()]
            out["folders"][item.name] = {"path": item, "files": files}
        elif item.is_file():
            if item.name not in ("DataEntry.json", "Status.json", "adverbs.json"):
                out["files"]["other_files"][item.name] = item
    return out

def get_canonical_module_tags():
    return [
        "noun-configure", "adjective-editor", "verb-editor", "adverb-editor",
        "conjunction-editor", "investigation", "deep-search", "audit",
        "archive-workbench", "runlog-workbench", "noun-workbench",
        "verb-workbench", "camera", "custom-upload",
        "prepositional-phrase-runner", "template", "account-roles",
        "grid-test", "parser-test", "nodes-compliance", "backup",
    ]
