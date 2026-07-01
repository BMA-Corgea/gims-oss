"""Repository / resource path resolution — one implementation for the whole app.

Replaces ~10 divergent ``_repo_root()`` / ``_compute_repo_root()`` copies, none of
which agreed and one of which walked up looking for a directory literally named
``GIMS-Project`` (so renaming or relocating the repo broke storage). This anchors on
the source layout and a sentinel file instead of a hard-coded folder name, and honours
the ``GIMS_ROOT`` override and PyInstaller's frozen layout.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

# Files that mark the repository root (any one is sufficient). No folder-name guessing.
_ROOT_SENTINELS = ("requirements.txt", "main.py", ".git", "pyproject.toml")


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """Absolute path to the repository / application root.

    Resolution order:
      1. ``GIMS_ROOT`` environment variable (explicit override).
      2. PyInstaller frozen bundle (``sys._MEIPASS`` / executable dir).
      3. Source layout: this file is ``<root>/utils/paths.py`` → ``parents[1]``,
         validated (and, if needed, corrected) by walking up for a sentinel file.
    """
    env = os.environ.get("GIMS_ROOT")
    if env:
        return Path(env).expanduser().resolve()

    if getattr(sys, "frozen", False):  # pragma: no cover - packaged builds
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
        return Path(base).resolve()

    here = Path(__file__).resolve()
    candidate = here.parents[1]  # <root>/utils/paths.py -> <root>
    for d in (candidate, *candidate.parents):
        if any((d / s).exists() for s in _ROOT_SENTINELS):
            return d
    return candidate


def resource_path(*parts: str) -> Path:
    """Path to a resource under the repo root, e.g. ``resource_path('projects')``."""
    return repo_root().joinpath(*parts)


def projects_dir() -> Path:
    """The ``projects/`` directory under the repo root."""
    return repo_root() / "projects"
