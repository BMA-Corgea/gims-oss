# api/routers/backup/__init__.py
#
# Package successor to the former single-file api/routers/backup.py — the
# /api/storage Storage/Backup router. Public surface is unchanged:
# `from api.routers.backup import router` still works (api/app.py relies on it),
# and the import-time backup-hook registration still fires at package import.
#
# Route registration ORDER is preserved by importing the route submodules in the
# SAME top-to-bottom sequence the handlers appeared in the original file:
#   1) projects + backup lifecycle  (routes_backups)
#   2) downloads                     (routes_downloads)
#   3) schedules + schedule/tick     (routes_schedules)

# 1) Shared router + logger (own module → no import cycles).
from ._router import router, log, DEBUG_ENABLED  # noqa: F401

# 2) Helper / model modules (no routes; order among these is irrelevant).
from . import paths           # noqa: E402,F401
from . import fsio            # noqa: E402,F401
from . import local_capture   # noqa: E402,F401
from . import pg_dump         # noqa: E402,F401
from . import models          # noqa: E402,F401
from . import manifest        # noqa: E402,F401
from . import restore         # noqa: E402,F401
from . import scheduling      # noqa: E402,F401

# 3) Route submodules — imported in the ORIGINAL top-to-bottom order so the
#    @router decorators run in sequence and route REGISTRATION ORDER is preserved.
from . import routes_backups    # noqa: E402,F401
from . import routes_downloads  # noqa: E402,F401
from . import routes_schedules  # noqa: E402,F401

# 4) Re-export the public names referenced by the import-time hook block below
#    (and by external/hook code).
from .routes_schedules import schedule_tick  # noqa: E402,F401
from .scheduling import _load_schedules       # noqa: E402,F401

__all__ = ["router", "schedule_tick", "_load_schedules"]

# 5) Inversion seam (Phase 6): register this GUI's backup-schedule entry points with the
# core hook so orchestration nodes (nodes/auto_backup_node.py) can trigger scheduled
# backups WITHOUT importing the GUI layer. See core/orchestration/backup_hook.py.
# MUST run at package import (app.py imports the package at startup).
from core.orchestration.backup_hook import (  # noqa: E402
    register_schedule_tick as _register_schedule_tick,
    register_schedules_loader as _register_schedules_loader,
)
_register_schedule_tick(schedule_tick)
_register_schedules_loader(_load_schedules)
