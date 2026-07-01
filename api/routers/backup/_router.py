# api/routers/backup/_router.py
#
# Shared APIRouter + module logger for the backup package. Kept in its own
# module so the route submodules (routes_backups / routes_downloads /
# routes_schedules) and the helper modules can reference the *same* router and
# logger without import cycles.
#
# (Original: api/routers/backup.py defined `router`, `log` and `DEBUG_ENABLED`
#  inline near the top of the file.)

from fastapi import APIRouter

from utils.logger import get_logger

log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()

router = APIRouter(prefix="/api/storage", tags=["Storage/Backup"])
