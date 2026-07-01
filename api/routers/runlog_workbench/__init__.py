# api/routers/runlog_workbench/__init__.py
"""Runlog workbench router package.

This package replaces the former single-file ``api/routers/runlog_workbench.py``.
It is split into role-named submodules that all decorate the SAME ``router``
(defined in ``_router``). The route submodules are imported below in the EXACT
top-to-bottom order of the original file so the ``@router`` decorators register
in the identical sequence (route order matters for FastAPI path shadowing).

``from api.routers.runlog_workbench import router`` keeps working (api/app.py
imports it that way).
"""

# Shared singletons (router + pinned package logger).
from ._router import DEBUG_ENABLED, log, router

# Shared import surface / cross-cutting helpers (also re-exported from here).
from ._shared import HANDLERS, get_project_path

# Route submodules — imported in the ORIGINAL contiguous order so the decorators
# register on ``router`` in the exact same sequence as the pre-split file:
#   view             -> /runlog/{p}/{vg}
#   data_dump        -> .../dump, /runlog_data_dump/projects,
#                       /runlog_data_dump/verb_groups/{p}
#   overrides        -> .../override/update, .../override
#   status           -> .../status.json, .../status
#   adverbs          -> .../adverb, .../adverb/update
#   grid             -> /grid/debug/whoami, /grid/runs, /grid/load,
#                       /gui/grid/save, /grid/dump
#   grid_references  -> /grid/noun_info, /grid/reference_adjectives,
#                       /grid/ref_options, /grid/generate_id, /grid/retest_options
#   reference_lookups-> /conjunction/reference_options, /schema/verb
#   raw_files        -> .../raw/upload, .../raw/delete, .../raw/list
#   interpret        -> .../interpret/list, .../interpret/upload,
#                       .../interpret/reset, .../interpret/delete
#   gates            -> .../status/linear, .../gate/list, .../gate/{step_id}/complete
#   downloads        -> .../raw/download, .../interpret/download,
#                       .../raw/download_zip, .../interpret/download_zip
#   step_ids         -> .../status/step_ids
from . import view
from . import data_dump
from . import overrides
from . import status
from . import adverbs
from . import grid
from . import grid_references
from . import reference_lookups
from . import raw_files
from . import interpret
from . import gates
from . import downloads
from . import step_ids

__all__ = [
    "router",
    "get_project_path",
    "HANDLERS",
    "log",
    "DEBUG_ENABLED",
]
