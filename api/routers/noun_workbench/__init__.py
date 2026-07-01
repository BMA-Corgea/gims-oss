# api/routers/noun_workbench/__init__.py
"""NounWorkbench router package.

This package is the drop-in replacement for the old single-module
``api/routers/noun_workbench.py``. It exposes the SAME public surface — the
module-level ``router`` (with the SAME routes, paths, methods, and registration
ORDER), plus ``get_project_path`` and ``validate_item_against_schema`` — so every
existing importer keeps working:

    from api.routers.noun_workbench import router as noun_workbench_gui_router   # api/app.py
    from api.routers.noun_workbench import get_project_path                       # runlog_workbench.py

The shared ``router`` lives in ``_router.py`` so the role-named submodules can
decorate it without a circular import. The route submodules are imported below in
the ORIGINAL top-to-bottom order of the pre-split file, so the ``@router``
decorators register in the exact same sequence (FastAPI route order is
load-bearing for path shadowing).
"""

# Shared singletons (router + pinned package logger).
from ._router import DEBUG_ENABLED, log, router

# Helpers / resolvers (re-exported for callers that import them from this package).
# Importing ``paths`` also registers the first route (`/project_path`).
from .paths import get_project_path, project_path_endpoint
from .validation import (
    _autogen_enforced_blank,
    _generate_primary,
    _is_valid_date,
    _list_ids_for_noun,
    validate_item_against_schema,
)
from .uploads import _normalize_id, _parse_upload_to_rows, _preview_rows

# Route submodules — imported in the ORIGINAL order so the decorators register
# on ``router`` in the exact same sequence as the pre-split file:
#   paths          -> /project_path
#   routes_read    -> /projects, /{project}/{noun_type}/items, /{project},
#                     /{project}/{noun_type}/schema, .../references/{field_name}
#   routes_write   -> .../validate, .../instance/{instance_id}, .../create,
#                     .../update/{instance_id}
#   routes_bulk    -> .../bulk_preview, .../bulk_commit
from .routes_read import (
    get_items,
    get_reference_options,
    get_schema,
    list_nouns,
    list_projects,
)
from .routes_write import (
    create_single,
    load_instance,
    update_single,
    validate_single,
)
from .routes_bulk import bulk_commit, bulk_preview

__all__ = [
    # primary public surface
    "router",
    "get_project_path",
    "validate_item_against_schema",
    # logging singletons (kept for parity with the pre-split module)
    "log",
    "DEBUG_ENABLED",
    # handlers
    "project_path_endpoint",
    "list_projects",
    "get_items",
    "list_nouns",
    "get_schema",
    "get_reference_options",
    "validate_single",
    "load_instance",
    "create_single",
    "update_single",
    "bulk_preview",
    "bulk_commit",
    # helpers
    "_is_valid_date",
    "_list_ids_for_noun",
    "_autogen_enforced_blank",
    "_generate_primary",
    "_parse_upload_to_rows",
    "_normalize_id",
    "_preview_rows",
]
