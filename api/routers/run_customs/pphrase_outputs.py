# api/routers/run_customs/pphrase_outputs.py
# Phrase-output tree browsing helpers (split verbatim from run_customs.py).
from __future__ import annotations

from pathlib import Path
from datetime import datetime

from api import i_o
from core.errors import AppError

from utils.logger import get_logger
log = get_logger("api.routers.run_customs")


def _safe_join(base: Path, rel: str) -> Path:
    """
    Join a user-supplied path under base and refuse escapes.
    We avoid Path.resolve() (not S3-friendly). Instead, reject
    absolute paths and any '..' segments.
    """
    rel_path = Path(rel)
    if rel_path.is_absolute() or any(part == ".." for part in rel_path.parts):
        raise AppError("INVALID_PATH_SEGMENT", "Invalid path segment", status=400,
                       details={"path": rel})
    return base / rel_path

def _stat_dict(p: Path) -> dict:
    statf = getattr(i_o, "fs_stat", None)
    if not callable(statf):
        # Fallback for local-only; should not hit in S3 mode
        s = Path(p).stat()
        return {
            "size": s.st_size,
            "mtime": datetime.fromtimestamp(s.st_mtime).isoformat(timespec="seconds"),
        }
    s = statf(p)
    size = s.get("st_size") if isinstance(s, dict) else getattr(s, "st_size", None)
    # mtime is optional on S3; omit if not available
    out = {"size": int(size) if size is not None else 0}
    try:
        mtime = s.get("st_mtime") if isinstance(s, dict) else getattr(s, "st_mtime", None)
        if mtime:
            out["mtime"] = datetime.fromtimestamp(float(mtime)).isoformat(timespec="seconds")
    except Exception:
        pass
    return out

def _tree(dir_path: Path, base: Path, depth: int) -> dict:
    """
    Return a JSON-friendly tree:
      { name, path, type: 'dir'|'file', size?, mtime?, children?[] }
    S3-aware via i_o.fs_iterdir / fs_is_dir / fs_is_file.
    """
    node = {
        "name": dir_path.name if dir_path != base else dir_path.name,
        "path": str(Path("") if dir_path == base else dir_path.relative_to(base)),
        "type": "dir",
        "children": [],
    }
    if depth < 0:
        return node

    entries = []
    try:
        entries = list(i_o.fs_iterdir(dir_path))
    except Exception:
        log.debug("[pphrase.tree][warn] failed to list directory; returning node without children",
                  str(dir_path), exc_info=True)
        return node

    dirs = sorted([e for e in entries if i_o.fs_is_dir(e)], key=lambda p: p.name.lower())
    files = sorted([e for e in entries if i_o.fs_is_file(e)], key=lambda p: p.name.lower())

    for d in dirs:
        node["children"].append(_tree(d, base, depth - 1))

    for f in files:
        child = {
            "name": f.name,
            "path": str(f.relative_to(base)),
            "type": "file",
            **_stat_dict(f),
        }
        node["children"].append(child)

    return node
