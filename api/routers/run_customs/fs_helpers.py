# api/routers/run_customs/fs_helpers.py
# S3-aware filesystem helpers (split verbatim from run_customs.py).
from __future__ import annotations

from pathlib import Path

from api import i_o
from api.manifest.resolver import RDS_ENABLED

from utils.logger import get_logger
log = get_logger("api.routers.run_customs")

# ---------------------------------------------------------------------------
# S3-aware helpers (mirror adjective_gui style: prefer i_o, fallback to Path)
# ---------------------------------------------------------------------------

def unlink_local(path: Path):
    """
    Force a true LOCAL filesystem delete, regardless of json_proxy or RDS mode.
    json_proxy tries to treat project-root paths as S3 paths; this bypasses that.
    """
    try:
        real = Path(str(path))  # ensures absolute local path resolution
        real.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def is_rds_mode() -> bool:
    """
    True when the project is running with Postgres+S3 (RDS) backend.
    EXACTLY matches json_proxy's determination of S3 mode.
    """
    return bool(RDS_ENABLED)

def _read_text_via_io(p: Path, encoding: str = "utf-8") -> str:
    """
    Read file contents, preferring i_o.open_file (S3-aware) and falling back to local Path.
    """
    fopen = getattr(i_o, "open_file", None)
    if callable(fopen):
        try:
            log.debug("[io.read_text] via i_o.open_file:", str(p))
            with fopen(p, mode="rb") as fh:
                data = fh.read()
            return data.decode(encoding, errors="replace")
        except FileNotFoundError:
            log.debug("[io.read_text][miss] not found via i_o:", str(p))
            raise
        except Exception as e:
            log.debug("[io.read_text][warn] i_o.open_file failed, fallback to Path:", repr(e))
    log.debug("[io.read_text] via Path.read_text:", str(p))
    return Path(p).read_text(encoding=encoding)

def _path_exists_via_io(p: Path) -> bool:
    """
    Fast existence check using i_o.fs_exists when present, else fallback to Path.exists().
    """
    fs_exists = getattr(i_o, "fs_exists", None)
    if callable(fs_exists):
        try:
            exists = bool(fs_exists(p))
            log.debug("[io.exists] via i_o.fs_exists:", str(p), "->", exists)
            return exists
        except Exception as e:
            log.debug("[io.exists][warn] fs_exists failed; fallback:", repr(e))
    try:
        exists = Path(p).exists()
        log.debug("[io.exists] via Path.exists:", str(p), "->", exists)
        return exists
    except Exception as e:
        log.debug("[io.exists][error] Path.exists failed:", repr(e))
        return False
