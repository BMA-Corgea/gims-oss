# api/routers/noun_workbench/_router.py
"""Shared singletons for the ``noun_workbench`` package.

The package is split into several role-named submodules that all decorate the
SAME ``router``. Defining ``router`` (and the package ``log``) here — rather than
in ``__init__`` — lets every ``routes_*`` submodule import them without a
circular import back through the package ``__init__``.

The logger name is pinned to ``"api.routers.noun_workbench"`` so log records are
identical to the pre-split single-module file.
"""

from fastapi import APIRouter

# ──────────────────────────────────────────────────────────────────────────────
# Debug control
# ──────────────────────────────────────────────────────────────────────────────
from utils.logger import get_logger

log = get_logger("api.routers.noun_workbench")
DEBUG_ENABLED = log.is_debug()

router = APIRouter(prefix="/api/noun_workbench", tags=["NounWorkbench"])
