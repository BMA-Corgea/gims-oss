# api/iostore/fs_shims.py -- split out of api/i_o.py (wiring-neutral). S3-aware FS predicates/listing.
from __future__ import annotations
from pathlib import Path
from typing import Optional, Iterable, Iterator, Tuple, List
from api.json_proxy import S3_ENABLED, _is_s3_path
from utils.logger import get_logger

log = get_logger(__name__)


def s3_capabilities() -> dict:
    """
    Probe optional json_proxy capabilities so callers can degrade gracefully.
    """
    caps = {
        "exists": False,
        "isfile": False,
        "isdir": False,
        "stat": False,
        "listdir": False,
        "walk": False,
        "read_bytes": False,
        "write_bytes": False,
        "delete": False,
        "list_projects": False,
        "list_dirnames": False,
        "presign": False,
        "iterdir": False,
    }
    try:
        from api import json_proxy as _jp  # type: ignore
    except Exception:
        return caps

    for name in list(caps.keys()):
        caps[name] = bool(getattr(_jp, name, None))
    if not caps["list_dirnames"]:
        caps["list_dirnames"] = bool(getattr(_jp, "list_children", None) or getattr(_jp, "list_prefixes", None))
    return caps

def _s3_call(name: str, *args, default=None, **kwargs):
    """
    Safe invoker for optional json_proxy functions. Returns default if missing/fails.
    """
    try:
        from api import json_proxy as _jp  # type: ignore
    except Exception:
        return default
    fn = getattr(_jp, name, None)
    if not fn:
        if name == "list_dirnames":
            fn = getattr(_jp, "list_children", None) or getattr(_jp, "list_prefixes", None)
        if name == "iterdir":
            fn = getattr(_jp, "iterdir", None)
    if not fn:
        return default
    try:
        return fn(*args, **kwargs)  # type: ignore[misc]
    except Exception as e:
        log.debug(f"[s3_call:{name}] {repr(e)}")
        return default

def fs_exists(path: Path) -> bool:
    if not S3_ENABLED or not _is_s3_path(path):
        return path.exists()
    try:
        from api import json_proxy as _jp
        if hasattr(_jp, "exists"):
            return bool(_jp.exists(str(path)))
    except Exception as e:
        log.debug(f"[fs_exists] S3 call failed: {e!r}, falling back to local")
    return path.exists() # Fallback

def fs_is_file(path: Path) -> bool:
    if not S3_ENABLED or not _is_s3_path(path):
        return path.is_file()
    try:
        from api import json_proxy as _jp
        if hasattr(_jp, "is_file"):
            return bool(_jp.is_file(str(path)))
    except Exception as e:
        log.debug(f"[fs_is_file] S3 call failed: {e!r}, falling back to local")
    return path.is_file() # Fallback

def fs_is_dir(path: Path) -> bool:
    if not S3_ENABLED or not _is_s3_path(path):
        return path.is_dir()
    try:
        from api import json_proxy as _jp
        if hasattr(_jp, "is_dir"):
            return bool(_jp.is_dir(str(path)))
    except Exception as e:
        log.debug(f"[fs_is_dir] S3 call failed: {e!r}, falling back to local")
    return path.is_dir() # Fallback

def fs_makedirs(path: str | Path, exist_ok: bool = True) -> None:
    """
    Create a directory path, S3-aware. Uses json_proxy.fs_makedirs when available,
    else falls back to os.makedirs for local filesystem.
    """
    import os
    path = str(path)
    try:
        from api import json_proxy
    except Exception:
        json_proxy = None

    if S3_ENABLED and json_proxy and hasattr(json_proxy, "fs_makedirs"):
        try:
            json_proxy.fs_makedirs(path, exist_ok=exist_ok)
            return
        except Exception as e:
            log.warning(f"[fs_makedirs] json_proxy.fs_makedirs failed, fallback to os: {e!r}")

    os.makedirs(path, exist_ok=exist_ok)
    log.debug(f"[fs_makedirs] local mkdir: {path}")

def fs_stat(path: Path):
    """
    S3-aware stat() that returns a stat-like object with st_size and st_mtime attributes.
    """
    if not S3_ENABLED or not _is_s3_path(path):
        return path.stat()
    
    # Try S3 stat first
    try:
        from api import json_proxy as _jp
        if hasattr(_jp, "stat"):
            st = _jp.stat(str(path))
            if st is not None:
                return st
    except Exception as e:
        log.debug(f"[fs_stat] S3 stat failed: {e!r}")
    
    # Fallback: try to get size via read and construct a minimal stat
    try:
        size = fs_stat_size(path)
        class _StatResult:
            st_size = size
            st_mtime = 0
        return _StatResult()
    except Exception:
        pass
    
    # Final fallback
    class _FakeStat:
        st_size = -1
        st_mtime = 0
    return _FakeStat()

def fs_stat_size(path: Path) -> int:
    if not S3_ENABLED or not _is_s3_path(path):
        try:
            return path.stat().st_size
        except Exception:
            return 0
    try:
        from api import json_proxy as _jp
        if hasattr(_jp, "stat"):
            st = _jp.stat(str(path))
            if hasattr(st, "st_size"):
                return int(st.st_size)  # type: ignore
            if isinstance(st, dict) and "st_size" in st:
                return int(st["st_size"])
        return path.stat().st_size # Fallback
    except Exception:
        return 0

def fs_mkdirs(path: Path, exist_ok: bool = True):
    if not S3_ENABLED or not _is_s3_path(path):
        path.mkdir(parents=True, exist_ok=exist_ok)
        return
    try:
        from api import json_proxy as _jp
        if hasattr(_jp, "makedirs"):
            _jp.makedirs(str(path))
    except Exception:
        pass # S3 dirs are virtual, ignore errors
    return

def fs_iterdir(path: Path) -> List[Path]:
    """
    Return children "paths". For S3, we return Path-like shells with joined names.
    """
    if not S3_ENABLED or not _is_s3_path(path):
        try:
            return list(path.iterdir())
        except Exception:
            log.debug(f"[fs_iterdir] local iterdir failed for {path!r}", exc_info=True)
            return []
    
    try:
        from api import json_proxy as _jp
        if hasattr(_jp, "iterdir"):
            items = _jp.iterdir(str(path)) or []
            out: list[Path] = []
            for x in items:
                # json_proxy.iterdir returns Path objects already
                if isinstance(x, Path):
                    out.append(x)
                else:
                    # Fallback just in case
                    out.append(path / str(x))
            return out
        return list(path.iterdir()) # Fallback
    except Exception as e:
        log.debug(f"[fs_iterdir] S3 call failed: {e!r}")
        return []

def fs_walk(top: Path) -> Iterator[Tuple[str, List[str], List[str]]]:
    """
    S3-aware replacement for os.walk. Yields (root, dirs, files).
    """
    if not S3_ENABLED or not _is_s3_path(top):
        for root, dirs, files in __import__("os").walk(top):
            yield root, dirs, files
        return

    caps = s3_capabilities()
    if caps["walk"]:
        for root, dirs, files in _s3_call("walk", top, default=[]):
            yield root, dirs, files
        return

    # Fallback: single level
    dirs, files = [], []
    for child in fs_iterdir(top):
        name = Path(child).name
        if fs_is_dir(child):
            dirs.append(name)
        else:
            files.append(name)
    yield str(top), dirs, files

def fs_glob_first(folder: Path, stem: str, allowed_exts: Iterable[str]) -> Optional[Path]:
    """
    Return the first existing file with name {stem}{ext} among allowed_exts.
    """
    for ext in allowed_exts:
        cand = folder / f"{stem}{ext}"
        if fs_exists(cand) and fs_is_file(cand):
            return cand
    return None
