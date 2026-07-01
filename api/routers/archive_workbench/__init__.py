# api/routers/archive_workbench/__init__.py
#
# Thin package facade. The implementation was split into:
#   _seams.py  — the 12 pytest monkeypatch seams + router + seam-reading service layer
#   routes.py  — the 18 route handlers (registered on `router` at import, original order)
# plus the pre-existing helper submodules (db_meta, index_tables, sql_exec,
# archive_index, noun_store, plans). Tests patch seams on the `_seams` module.
from __future__ import annotations

from ._seams import router  # noqa: F401  — mounted by api/app.py

# Re-export the monkeypatch seams for back-compat (canonical home is now ._seams).
from ._seams import (  # noqa: F401
    resolve_path, get_db_uri, load_schema, get_verb_schema,
    get_verb_group_log_config, load_verb_group_log,
    _jp_list_projects, _jp_project_exists,
    _HAS_S3, DEBUG_ENABLED, _PSYCOPG_AVAILABLE, json_proxy,
)

from . import routes  # noqa: F401,E402  — registers all route handlers on `router`
