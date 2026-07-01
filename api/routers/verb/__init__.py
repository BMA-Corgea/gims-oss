# api/routers/verb/__init__.py
#
# Package successor to the former single-file api/routers/verb.py.
# Public surface is unchanged: `from api.routers.verb import router` still works
# (api/app.py relies on this).
#
# Route registration ORDER is preserved by importing the route submodules in
# the SAME top-to-bottom sequence the handlers appeared in the original file:
#   1) verb CRUD + /noun/valid-refs   (routes_verb)
#   2) /verb/log-schema/*             (routes_log_schema)
#   3) /verb/status-workflow/*        (routes_status_workflow)
# This keeps the literal-before-param ordering intact
# (e.g. "/verb/projects" before "/verb/{project}", and
#  "/verb/status-workflow/step-types" before "/verb/status-workflow/{project}/{verb_name}").

from ._log import log, DEBUG_ENABLED
from ._router import router

# Importing these modules executes their @router decorators, registering each
# handler against the shared router in the original order.
from . import routes_verb            # noqa: E402,F401
from . import routes_log_schema      # noqa: E402,F401
from . import routes_status_workflow # noqa: E402,F401

__all__ = ["router", "log", "DEBUG_ENABLED"]
