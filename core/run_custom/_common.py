# core/run_custom/_common.py
# Shared module header for the run_custom package: top-of-file imports,
# the S3-aware FS bridge, config/AppError/resolver, and the package logger.
# These are the names the other submodules import from `._common`.
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Set, Callable, Tuple
from pathlib import Path
from copy import deepcopy
import ast

#changed my mind, the WASM runtime is frustrating me
from api.manifest.resolver import resolve_path

# ──────────────────────────────────────────────────────────────
# S3-aware FS bridge (fallback to local if json_proxy absent)
# ──────────────────────────────────────────────────────────────
try:
    # Prefer the project’s S3/json_proxy-aware helpers
    from api.i_o import (  # type: ignore
        fs_exists, fs_is_dir, fs_is_file, fs_iterdir, fs_listdir,
        fs_mkdirs, fs_read_bytes, fs_write_bytes,
        fs_open_readbin, fs_open_writebin,
        fs_copy, fs_copytree, fs_remove, fs_rmtree,
        S3_ENABLED as _S3_ENABLED_FLAG,
    )
    S3_ENABLED = _S3_ENABLED_FLAG
except Exception:
    # Minimal local fallbacks (keep signatures compatible)
    S3_ENABLED = False

    def fs_exists(p: Path) -> bool: return Path(p).exists()
    def fs_is_dir(p: Path) -> bool: return Path(p).is_dir()
    def fs_is_file(p: Path) -> bool: return Path(p).is_file()
    def fs_iterdir(p: Path):         return list(Path(p).iterdir())
    def fs_listdir(p: Path):         return [x.name for x in Path(p).iterdir()]
    def fs_mkdirs(p: Path) -> None:  Path(p).mkdir(parents=True, exist_ok=True)
    def fs_read_bytes(p: Path) -> bytes: return Path(p).read_bytes()
    def fs_write_bytes(p: Path, data: bytes) -> None:
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_bytes(data)
    def fs_open_readbin(p: Path):    return open(p, "rb")
    def fs_open_writebin(p: Path):   # returns a file-like handle
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        return open(p, "wb")
    def fs_copy(src: Path, dst: Path) -> None:
        from shutil import copy2
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        copy2(src, dst)
    def fs_copytree(src: Path, dst: Path) -> None:
        from shutil import copytree
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        copytree(src, dst, dirs_exist_ok=True)
    def fs_remove(p: Path) -> None:
        try:
            Path(p).unlink(missing_ok=True)  # py3.8+: handle as needed if older
        except TypeError:
            try:
                Path(p).unlink()
            except FileNotFoundError:
                pass
    def fs_rmtree(p: Path) -> None:
        from shutil import rmtree
        if Path(p).exists():
            rmtree(p)

    def io_debug(*a, **k):  # no-op fallback
        pass

# Debug control - set to False to disable all backend debug logging
from utils.logger import get_logger
from utils import config
from core.errors import AppError
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()
