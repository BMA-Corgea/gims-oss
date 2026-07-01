# api/routers/verb/_log.py
#
# Shared logger for the verb package. Uses the original module-qualified
# name ("api.routers.verb") so log output is identical to the pre-split file.

from utils.logger import get_logger

log = get_logger("api.routers.verb")
DEBUG_ENABLED = log.is_debug()
