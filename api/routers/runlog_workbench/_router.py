# api/routers/runlog_workbench/_router.py
"""Shared singletons for the ``runlog_workbench`` package.

The package is split into several role-named submodules that all decorate the
SAME ``router``. Defining ``router`` (and the package ``log``) here — rather than
in ``__init__`` — lets every route submodule import them without a circular
import back through the package ``__init__``.

The logger name is pinned to ``"api.routers.runlog_workbench"`` so log records
are byte-identical to the pre-split single-module file (which used
``get_logger(__name__)``).
"""

from fastapi import APIRouter

# Debug control - set to False to disable all backend debug logging
from utils.logger import get_logger
log = get_logger("api.routers.runlog_workbench")
DEBUG_ENABLED = log.is_debug()

router = APIRouter()
