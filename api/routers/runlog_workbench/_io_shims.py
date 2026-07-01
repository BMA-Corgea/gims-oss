# api/routers/runlog_workbench/_io_shims.py
"""S3-aware filesystem shims for the runlog workbench package.

Moved VERBATIM from the original ``runlog_workbench.py`` (the S3-awareness block
and the archive-style ``_jp_*`` listers). Prefer ``api.i_o`` shims if present;
else provide local shims that delegate to ``api.json_proxy`` (when available) or
the local filesystem.
"""

from pathlib import Path
from typing import List, Optional, Iterable, Tuple

from ._router import log
from api.manifest.resolver import resolve_path

# -----------------------------------------------------------------------------
# S3-awareness: prefer api.i_o shims if present; else provide local shims that
# delegate to api.json_proxy (when available) or local filesystem.
# -----------------------------------------------------------------------------
_HAS_S3 = False
_json_proxy = None

try:
    from api import json_proxy as _json_proxy  # Optional S3 layer
    _HAS_S3 = True
except Exception:
    _json_proxy = None
    _HAS_S3 = False

# Try to import fs_* shims from api.i_o if your codebase already has them.
_fs = {}
try:
    from api.i_o import (
        fs_exists, fs_is_file, fs_is_dir, fs_iterdir, fs_walk, fs_mkdirs,
        fs_open_readbin, fs_open_writebin, fs_write_bytes, fs_remove,
        fs_stat_size, fs_glob_first, make_zip_stream,
    )  # type: ignore
    _fs.update({"external_shims": True})
except Exception:
    _fs.update({"external_shims": False})

# Provide fallback shims if api.i_o didn't export them
if not _fs.get("external_shims"):
    import os
    import io
    import zipfile
    from pathlib import Path
    from typing import Iterable, List, Optional, Tuple

    def fs_exists(p: Path) -> bool:
        if _HAS_S3 and hasattr(_json_proxy, "exists"):
            return bool(_json_proxy.exists(str(p)))
        return p.exists()

    def fs_is_file(p: Path) -> bool:
        if _HAS_S3 and hasattr(_json_proxy, "is_file"):
            return bool(_json_proxy.is_file(str(p)))
        return p.is_file()

    def fs_is_dir(p: Path) -> bool:
        if _HAS_S3 and hasattr(_json_proxy, "is_dir"):
            return bool(_json_proxy.is_dir(str(p)))
        return p.is_dir()

    def fs_iterdir(p: Path) -> Iterable[Path]:
        """
        Return an iterable of Path objects (never strings).
        json_proxy.iterdir() already yields Path-like entries on S3.
        """
        if _HAS_S3 and hasattr(_json_proxy, "iterdir"):
            items = _json_proxy.iterdir(str(p)) or []
            out: list[Path] = []
            for x in items:
                if isinstance(x, Path):
                    out.append(x)
                else:
                    # Normalize possible string/Key return into a Path relative to parent
                    out.append(Path(x) if ("/" in str(x) or "\\" in str(x)) else (p / str(x)))
            return out
        return list(p.iterdir())

    def fs_walk(p: Path):
        if _HAS_S3 and hasattr(_json_proxy, "walk"):
            # json_proxy.walk should behave like os.walk, returning (root, dirs, files)
            return _json_proxy.walk(str(p))
        return os.walk(p)

    def fs_mkdirs(p: Path) -> None:
        if _HAS_S3 and hasattr(_json_proxy, "makedirs"):
            _json_proxy.makedirs(str(p))
            return
        p.mkdir(parents=True, exist_ok=True)

    def fs_open_readbin(p: Path):
        if _HAS_S3 and hasattr(_json_proxy, "open"):
            return _json_proxy.open(str(p), "rb")
        return open(p, "rb")

    def fs_open_writebin(p: Path):
        if _HAS_S3 and hasattr(_json_proxy, "open"):
            return _json_proxy.open(str(p), "wb")
        return open(p, "wb")

    def fs_write_bytes(p: Path, data: bytes) -> None:
        if _HAS_S3 and hasattr(_json_proxy, "write_bytes"):
            _json_proxy.write_bytes(str(p), data)
            return
        fs_mkdirs(p.parent)
        p.write_bytes(data)

    def fs_remove(p: Path) -> None:
        if _HAS_S3 and hasattr(_json_proxy, "remove"):
            _json_proxy.remove(str(p))
            return
        try:
            os.remove(p)
        except FileNotFoundError:
            pass

    def fs_stat_size(p: Path) -> int:
        if _HAS_S3 and hasattr(_json_proxy, "stat"):
            st = _json_proxy.stat(str(p))
            # Be lenient: accept dict-like or os.stat_result-like
            if hasattr(st, "st_size"):
                return int(st.st_size)  # type: ignore
            if isinstance(st, dict) and "st_size" in st:
                return int(st["st_size"])
            raise RuntimeError("json_proxy.stat returned unexpected type")
        return p.stat().st_size

    def fs_glob_first(d: Path, pattern: str) -> Optional[Path]:
        if _HAS_S3 and hasattr(_json_proxy, "glob_first"):
            g = _json_proxy.glob_first(str(d), pattern)
            return Path(g) if g else None
        return next(iter(d.glob(pattern)), None)

    def make_zip_stream(files: List[Tuple[Path, str]]):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path, arcname in files:
                with fs_open_readbin(path) as fh:
                    zf.writestr(arcname, fh.read())
        buf.seek(0)
        return buf

def s3_read_text(p: Path, encoding: str = "utf-8") -> str:
    if _HAS_S3 and hasattr(_json_proxy, "read_text"):
        return _json_proxy.read_text(str(p), encoding=encoding)  # type: ignore
    return p.read_text(encoding=encoding)

def s3_write_text(p: Path, text: str, encoding: str = "utf-8") -> None:
    if _HAS_S3 and hasattr(_json_proxy, "write_text"):
        _json_proxy.write_text(str(p), text, encoding=encoding)  # type: ignore
        return
    fs_mkdirs(p.parent)
    p.write_text(text, encoding=encoding)

def fs_stat_mtime(p: Path) -> Optional[float]:
    """S3-aware mtime (seconds since epoch) if available, else None if not provided."""
    if _HAS_S3 and hasattr(_json_proxy, "stat"):
        try:
            st = _json_proxy.stat(str(p))
            if hasattr(st, "st_mtime"):
                return float(st.st_mtime)  # type: ignore
            if isinstance(st, dict) and "st_mtime" in st:
                return float(st["st_mtime"])
            # Some backends expose ISO timestamps; ignore if not numeric
        except Exception:
            return None
    try:
        return p.stat().st_mtime
    except Exception:
        return None

def _jp_list_projects() -> List[str]:
    """Archive-style: list project roots via json_proxy if available."""
    if _HAS_S3 and hasattr(_json_proxy, "list_projects"):
        try:
            return sorted(list(_json_proxy.list_projects() or []))
        except Exception:
            log.debug("[jp_list_projects] json_proxy.list_projects failed; falling back to local scan",
                      exc_info=True)
    # Fallback to local scan
    root = resolve_path(Path(), "project_root")
    if fs_exists(root):
        return sorted([p.name for p in fs_iterdir(root) if fs_is_dir(p)])
    return ["demo", "test_project"]

def _jp_list_dirnames(root: Path) -> List[str]:
    """Archive-style: list subdirectories under a given root path."""
    if _HAS_S3 and hasattr(_json_proxy, "list_dirnames"):
        try:
            return sorted(list(_json_proxy.list_dirnames(str(root)) or []))
        except Exception:
            log.debug("[jp_list_dirnames] json_proxy.list_dirnames failed; falling back to local scan",
                      {"root": str(root)}, exc_info=True)
    try:
        return sorted([p.name for p in fs_iterdir(root) if fs_is_dir(p)])
    except Exception:
        log.debug("[jp_list_dirnames] local scan failed; returning []",
                  {"root": str(root)}, exc_info=True)
        return []
