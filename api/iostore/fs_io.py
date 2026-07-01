# api/iostore/fs_io.py -- split out of api/i_o.py (wiring-neutral). S3-aware byte IO + zip + project list.
from __future__ import annotations
import io
import builtins
import zipfile
from pathlib import Path
from typing import Iterable, Tuple, List
from api.json_proxy import read_text, write_text, S3_ENABLED, _is_s3_path
from api.manifest.resolver import resolve_path
from .fs_shims import s3_capabilities, _s3_call, fs_mkdirs, fs_iterdir, fs_is_dir
from utils.logger import get_logger

log = get_logger(__name__)


def fs_remove(path: Path) -> bool:
    if not S3_ENABLED or not _is_s3_path(path):
        try:
            path.unlink(missing_ok=True)
            return True
        except Exception as e:
            log.debug(f"[fs_remove] local failed: {e!r}")
            return False
    caps = s3_capabilities()
    if caps["delete"]:
        ok = _s3_call("delete", path, default=False)
        return bool(ok)
    try:
        write_text(path, "", encoding="utf-8")
        return True
    except Exception:
        return False

def fs_write_bytes(path: Path, data: bytes) -> None:
    """
    S3- and local-aware binary write helper.
    - Creates parent dirs automatically (both local and S3)
    - Uses json_proxy's fs_open_writebin for S3 targets
    - Falls back gracefully to local path.write_bytes()
    """
    try:
        # Always ensure parent directories exist first
        fs_mkdirs(path.parent)

        # If S3 disabled or not an S3 path, just write locally
        if not S3_ENABLED or not _is_s3_path(path):
            path.write_bytes(data)
            return

        # Otherwise, stream directly to S3
        with fs_open_writebin(path) as f:
            f.write(data)
        log.debug(f"[fs_write_bytes] wrote to S3: {path}")
    except Exception as e:
        log.debug(f"[fs_write_bytes][error] failed for {path!r}: {e!r}")
        raise

def fs_read_bytes(path: Path) -> bytes:
    """
    S3-aware binary read.
    """
    if not S3_ENABLED or not _is_s3_path(path):
        return path.read_bytes()
    
    try:
        with fs_open_readbin(path) as f:
            return f.read()
    except Exception as e:
        log.debug(f"[fs_read_bytes] failed: {e!r}")
        raise

def fs_copy(src: Path, dst: Path) -> None:
    """
    S3-aware file copy. Copies a single file from src to dst.
    """
    if not S3_ENABLED or (not _is_s3_path(src) and not _is_s3_path(dst)):
        # Both local - use standard copy
        import shutil
        fs_mkdirs(dst.parent)
        shutil.copy2(src, dst)
        return
    
    # At least one path is S3 - read and write
    try:
        data = fs_read_bytes(src)
        fs_write_bytes(dst, data)
    except Exception as e:
        log.debug(f"[fs_copy] failed: {e!r}")
        raise

def fs_copytree(src: Path, dst: Path, dirs_exist_ok: bool = True) -> None:
    """
    S3-aware recursive directory copy. Copies entire directory tree from src to dst.
    """
    if not S3_ENABLED or (not _is_s3_path(src) and not _is_s3_path(dst)):
        # Both local - use standard copytree
        import shutil
        shutil.copytree(src, dst, dirs_exist_ok=dirs_exist_ok)
        return
    
    # At least one path is S3 - recursive copy
    try:
        fs_mkdirs(dst)
        for item in fs_iterdir(src):
            item_dst = dst / item.name
            if fs_is_dir(item):
                fs_copytree(item, item_dst, dirs_exist_ok=dirs_exist_ok)
            else:
                fs_copy(item, item_dst)
    except Exception as e:
        log.debug(f"[fs_copytree] failed: {e!r}")
        raise

def fs_open_readbin(path: Path):
    """
    Open a readable binary stream for local or S3.
    """
    if not S3_ENABLED or not _is_s3_path(path):
        return builtins.open(path, "rb")
    try:
        from api import json_proxy as _jp  # type: ignore
    except Exception:
        return builtins.open(path, "rb")
    if getattr(_jp, "read_bytes", None):
        data = _jp.read_bytes(path)  # type: ignore[attr-defined]
        return io.BytesIO(data)
    data = read_text(path, encoding="utf-8", errors="ignore")
    return io.BytesIO(data.encode("utf-8"))

def fs_open_writebin(path: Path):
    """
    Open a writable binary stream for local or S3. Upload on close.
    """
    if not S3_ENABLED or not _is_s3_path(path):
        return builtins.open(path, "wb")

    try:
        from api import json_proxy as _jp  # type: ignore
    except Exception:
        return builtins.open(path, "wb")

    has_wb = bool(getattr(_jp, "write_bytes", None))
    buf = io.BytesIO()

    def _close_and_upload():
        body = buf.getvalue()
        if has_wb:
            _jp.write_bytes(path, body)  # type: ignore[attr-defined]
        else:
            write_text(path, body.decode("latin-1"), encoding="latin-1")
        buf.close = lambda: None
        super(io.BytesIO, buf).close()

    buf.close = _close_and_upload  # type: ignore[assignment]
    return buf

def make_zip_stream(files: Iterable[Tuple[Path, str]]) -> io.BytesIO:
    """
    Create an in-memory ZIP from (path, arcname) pairs.
    S3-aware: reads via fs_open_readbin for each path.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p, arc in files:
            try:
                with fs_open_readbin(p) as fh:
                    zf.writestr(arc, fh.read())
            except Exception as e:
                log.debug(f"[make_zip_stream] skip {p}: {e!r}")
                continue
    buf.seek(0)
    return buf

def io_list_projects() -> List[str]:
    """
    Return project folder names under the resolved project_root.
    - S3: uses json_proxy.list_projects() if available; else prefixes under root
    - Local: classic iterdir approach (hidden dirs ignored)
    """
    root = resolve_path(Path(), "project_root")

    # S3 mode?
    if S3_ENABLED and _is_s3_path(root):
        caps = s3_capabilities()
        if caps["list_projects"]:
            names = _s3_call("list_projects", default=[]) or []
            return sorted(str(n).rstrip("/").split("/")[-1] for n in names if str(n).strip())
        children = _s3_call("list_dirnames", root, default=[]) or []
        out = []
        for c in children:
            name = str(c).rstrip("/").split("/")[-1]
            if name and not name.startswith("."):
                out.append(name)
        return sorted(set(out))

    try:
        return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    except Exception:
        log.warning("[io_list_projects] failed to list local project_root", {"root": str(root)}, exc_info=True)
        return []
