# api/routers/verb/routes_log_schema.py
#
# Log-schema endpoints (config remains file-based for UI editors). Handlers
# moved VERBATIM from api/routers/verb.py (no logic changes). Registered after
# the verb-CRUD routes to preserve the original registration order.

from fastapi import Body

from api.i_o import get_verb_group_log_config, save_json
from api.manifest.resolver import resolve_path

from ._router import router
from ._log import log
from ._helpers import _get_project_path
from ._compat import ensure_prefix, touch


# ─────────────────────────────────────────────────────────────
# Log Schema routes (config remains file-based for UI editors)
# ─────────────────────────────────────────────────────────────
@router.get("/verb/log-schema/{project}/{group}")
def get_log_schema(project: str, group: str):
    proj = _get_project_path(project)
    try:
        cfg = get_verb_group_log_config(proj, group)
        log.debug("[get_log_schema] ok", {"group": group, "primary_id": cfg.get("primary_id")})
        return cfg
    except FileNotFoundError:
        log.debug("[get_log_schema] missing config", {"group": group})
        return {"primary_id": None, "fields": {}}

@router.post("/verb/log-schema/{project}/{group}")
def save_log_schema(project: str, group: str, schema: dict = Body(...)):
    proj = _get_project_path(project)
    config_path = resolve_path(proj, "verb_group_log_config", verb_group=group)
    log_file = resolve_path(proj, "verb_group_log", verb_group=group)

    # ensure folders (S3-aware)
    ensure_prefix(config_path.parent)
    ensure_prefix(log_file.parent)

    # write schema file via i_o helper (S3-aware)
    save_json(config_path, schema)
    log.debug("[save_log_schema] wrote", {"config_path": str(config_path)})

    # touch the legacy .jsonl path (compat only; not used for data)
    touch(log_file)
    log.debug("[save_log_schema] touched legacy log file", {"log_file": str(log_file)})

    return {"status": "saved", "log_file": str(log_file), "config_file": str(config_path)}
