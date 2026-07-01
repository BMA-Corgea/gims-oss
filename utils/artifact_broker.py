"""Trusted host-side gateway between a tool sandbox and the project tree (Phase 6 / R15).

The old container runners bind-mounted a **writable host directory** straight into an untrusted
tool container and trusted whatever the tool wrote (the pre-run check validated only the *declared*
manifest paths, never the *actual* writes). That allowed arbitrary host writes, symlink-escape and
resource exhaustion.

The artifact-broker model instead has the container write only to an **isolated ephemeral
directory** (never the project tree). After the container exits, this gateway inspects every
produced file and copies through only the ones that pass — into the real destination. Each file is
checked for:

  * **path containment** — the resolved real path stays inside the ephemeral source root
    (no ``..`` escape, no absolute redirection),
  * **no symlinks** — neither the file nor any directory component is a symlink (blocks
    symlink-follow escape when the host copies it out),
  * **extension allow-list** — the suffix is in :func:`utils.config.allowed_artifact_types`,
  * **magic-byte match** — binary types (pdf/png/xlsx/docx) must carry their real signature;
    text types (csv/json/txt/html) must not be a disguised executable (ELF/PE/Mach-O/shebang/NUL),
  * **size cap** — at most :func:`utils.config.artifact_max_bytes` per file,
  * **count cap** — at most :func:`utils.config.artifact_max_count` files per run.

Survivors are copied **content-only** (no exec bits, mode 0644). Everything else is dropped and
recorded in the returned report so nothing fails silently.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from utils import config
from utils.logger import get_logger

log = get_logger(__name__)

# Binary types whose first bytes must match a known signature.
_MAGIC: Dict[str, tuple[bytes, ...]] = {
    "pdf": (b"%PDF-",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    # OOXML containers (xlsx/docx) are ZIP archives.
    "xlsx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    "docx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
}

# Text types: accept anything that is NOT an obvious executable / binary.
_TEXT_TYPES = {"csv", "json", "txt", "html"}

# Leading bytes that mark an executable — rejected even for text-typed artifacts.
_EXECUTABLE_MAGIC: tuple[bytes, ...] = (
    b"\x7fELF",          # ELF (Linux)
    b"MZ",               # DOS/PE (Windows .exe/.dll)
    b"\xfe\xed\xfa\xce",  # Mach-O 32 BE
    b"\xfe\xed\xfa\xcf",  # Mach-O 64 BE
    b"\xcf\xfa\xed\xfe",  # Mach-O 64 LE
    b"\xca\xfe\xba\xbe",  # Mach-O universal / Java class
    b"#!",               # script shebang
)


@dataclass
class BrokerReport:
    committed: List[str] = field(default_factory=list)            # destination paths written
    rejected: List[Dict[str, str]] = field(default_factory=list)  # {"path":..., "reason":...}

    @property
    def ok(self) -> bool:
        return not self.rejected

    def as_dict(self) -> Dict[str, object]:
        return {"committed": list(self.committed), "rejected": list(self.rejected)}


def _ext(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def _has_symlink_component(path: Path, root: Path) -> bool:
    """True if ``path`` itself or any directory between ``root`` and it is a symlink."""
    cur = path
    while True:
        if cur.is_symlink():
            return True
        if cur == root or cur.parent == cur:
            return False
        cur = cur.parent


def validate_artifact(
    path: Path,
    *,
    src_root: Path,
    allowed_types: Optional[set[str]] = None,
    max_bytes: Optional[int] = None,
) -> tuple[bool, str]:
    """Validate one produced file. Returns ``(ok, reason)`` — ``reason`` is empty when ok."""
    allowed_types = config.allowed_artifact_types() if allowed_types is None else allowed_types
    max_bytes = config.artifact_max_bytes() if max_bytes is None else max_bytes

    path = Path(path)
    src_root = Path(src_root).resolve()

    # 1) no symlinks anywhere on the way down (before we resolve / read anything)
    if _has_symlink_component(path, src_root):
        return False, "symlink not allowed"

    # 2) path containment — the real path must stay within the ephemeral source root
    try:
        real = path.resolve()
        real.relative_to(src_root)
    except (ValueError, OSError):
        return False, "path escapes the sandbox output root"

    if not real.is_file():
        return False, "not a regular file"

    # 3) extension allow-list
    ext = _ext(real)
    if ext not in allowed_types:
        return False, f"extension '.{ext}' not in the allowed artifact types"

    # 4) size cap
    try:
        size = real.stat().st_size
    except OSError as e:
        return False, f"stat failed: {e}"
    if size > max_bytes:
        return False, f"exceeds size cap ({size} > {max_bytes} bytes)"

    # 5) content / magic-byte check
    try:
        with open(real, "rb") as fh:
            head = fh.read(512)
    except OSError as e:
        return False, f"read failed: {e}"

    if any(head.startswith(sig) for sig in _EXECUTABLE_MAGIC):
        return False, "content looks like an executable"

    if ext in _MAGIC:
        if not any(head.startswith(sig) for sig in _MAGIC[ext]):
            return False, f"content does not match a real .{ext} file"
    elif ext in _TEXT_TYPES:
        if b"\x00" in head:  # NUL byte => binary, not the declared text type
            return False, f"declared text (.{ext}) but content is binary"

    return True, ""


def collect_artifacts(
    src_dir: Path,
    dst_dir: Path,
    *,
    allowed_types: Optional[set[str]] = None,
    max_bytes: Optional[int] = None,
    max_count: Optional[int] = None,
) -> BrokerReport:
    """Validate every file under ``src_dir`` (the ephemeral container output) and copy survivors
    into ``dst_dir`` preserving their relative layout. Returns a :class:`BrokerReport`.

    Copies are content-only (mode 0644, no exec bit). Walk does NOT follow symlinks. Files beyond
    the per-run count cap are rejected (recorded, not silently dropped)."""
    allowed_types = config.allowed_artifact_types() if allowed_types is None else allowed_types
    max_bytes = config.artifact_max_bytes() if max_bytes is None else max_bytes
    max_count = config.artifact_max_count() if max_count is None else max_count

    src_dir = Path(src_dir).resolve()
    dst_dir = Path(dst_dir)
    report = BrokerReport()

    if not src_dir.is_dir():
        return report

    committed = 0
    for root, dirs, files in os.walk(src_dir, followlinks=False):
        # Drop symlinked subdirectories from the walk entirely.
        dirs[:] = [d for d in dirs if not Path(root, d).is_symlink()]
        for name in sorted(files):
            fpath = Path(root, name)
            rel = fpath.relative_to(src_dir)

            if committed >= max_count:
                report.rejected.append({"path": str(rel), "reason": f"exceeds artifact count cap ({max_count})"})
                continue

            ok, reason = validate_artifact(
                fpath, src_root=src_dir, allowed_types=allowed_types, max_bytes=max_bytes
            )
            if not ok:
                log.warning("[artifact_broker] dropped", str(rel), "->", reason)
                report.rejected.append({"path": str(rel), "reason": reason})
                continue

            dst = dst_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fpath, dst)   # content only — no perms/exec bits carried over
            try:
                os.chmod(dst, 0o644)
            except OSError:
                pass
            report.committed.append(str(dst))
            committed += 1

    log.debug("[artifact_broker] committed", len(report.committed), "rejected", len(report.rejected))
    return report
