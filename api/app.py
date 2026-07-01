# api/app.py — the ASGI entrypoint (was gui/gui_main.py; gui/gui_main.py is now a back-compat shim).

from __future__ import annotations

import sys
# NOTE (Phase 5): the global `sys.modules["json"] = json_proxy` monkeypatch was removed.
# S3-aware JSON I/O now goes through explicit api.i_o helpers (load_schema / save_schema /
# read_text / write_text). The patch was a no-op for the codebase's actual call patterns —
# every json.load/dump site passes a file object (open()/Path.open()), never a bare path, so
# json_proxy's path-form S3 branch was never exercised; for local paths it delegated to stdlib.
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse

# ─── Orchestration (nodes/modules/launcher) ───────────────────────────────
# These keep core clean and simply wire existing routers/nodes as "modules".
from core.orchestration.registry import registry
from modules.launcher import launcher_module
from modules.deep_search import deep_search_module
from modules.archive import archive_module
from modules.account_roles import account_roles_module
from modules.runlog_workbench import runlog_workbench_module
from modules.investigation import investigation_module
from modules.inspect import inspect_module
from modules.audit import audit_module
from modules.noun_configure import noun_configure_module
from modules.adjective_editor import adjective_editor_module
from modules.verb_editor import verb_editor_module
from modules.adverb_editor import adverb_editor_module
from modules.conjunction import conjunction_module
from modules.camera import camera_module
from modules.prepositional_phrase import prepositional_phrase_module
from modules.custom_upload import custom_upload_module
from modules.noun_workbench import noun_workbench_module
from modules.verb_workbench import verb_workbench_module
from modules.template import template_module
from modules.backup import backup_module
from modules.nodes_compliance import nodes_compliance_module
from modules.tutorial import tutorial_module

# ─── Routers (the HTTP/JSON API layer; HTML lives in gui/components) ───────
#     All routers now live under api/routers/ (was gui/*_gui.py — dropped the
#     misleading _gui suffix; these are FastAPI routers, not GUI).
from api.routers.noun              import router as noun_gui_router
from api.routers.investigation     import router as investigation_gui_router
from api.routers.adjective         import router as adjective_gui_router
from api.routers.verb              import router as verb_gui_router
from api.routers.conjunction       import router as conjunction_gui_router
from api.routers.adverb            import router as adverb_gui_router
from api.routers.pos               import router as word_router_router
from api.routers.deep_search       import router as deep_search_gui_router
from api.routers.camera            import router as camera_gui_router
from api.routers.runlog_workbench  import router as runlog_workbench_gui_router
from api.routers.project           import router as project_api_router
# (api.routers.grid + api.routers.grid_adapter removed — both were dead duplicates of the
#  runlog_workbench grid endpoints; every live /grid/* path is served by runlog_workbench
#  (grid.py + grid_references.py), registered earlier. grid_adapter's routes had zero live
#  consumers and included an unsafe JSON-only save + an arbitrary-SQL endpoint.)
from api.routers.custom_upload     import router as custom_upload_gui_router
from api.routers.run_customs       import router as run_customs_gui_router
from api.routers.noun_workbench    import router as noun_workbench_gui_router
from api.routers.verb_workbench    import router as verb_workbench_gui_router
from api.routers.audit             import router as audit_gui_router
from api.routers.archive_workbench import router as archive_workbench_gui_router
from api.routers.template          import router as template_gui_router
from api.routers.account_roles     import router as account_roles_gui_router
from api.routers.backup            import router as backup_gui_router
from api.routers.nodes_compliance  import router as compliance_gui_router

app = FastAPI(title="GIMS GUI + Orchestration")

# ─── One error contract: every error renders to a single (backward-compatible) JSON
#     envelope, and unhandled exceptions become clean, logged 500s. (Phase 2)
from core.errors import register_error_handlers
register_error_handlers(app)

def resource_path(rel_path: str) -> Path:
    """Get absolute path to resource, works for dev and PyInstaller bundle."""
    if getattr(sys, 'frozen', False):
        base_path = Path(sys._MEIPASS)   # temp folder where PyInstaller unpacks
    else:
        base_path = Path(__file__).resolve().parent.parent
    return base_path / rel_path

# ─── Static assets (JS + CSS) ─────────────────────────────────────────────
static_dir = resource_path("static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# ─── HTML components directory ────────────────────────────────────────────
# HTML lives in gui/components/ (the only thing that is actually GUI). Anchor it at the
# repo root via resource_path so it resolves the same in dev and a PyInstaller bundle —
# independent of this file's own location (it moved from gui/ to api/).
HTML_DIR = resource_path("gui/components")

def html(file: str) -> FileResponse:
    return FileResponse(HTML_DIR / file, media_type="text/html")

# ─── Orchestration launcher (new) ─────────────────────────────────────────
# Register modules you want available system-wide.
registry.register(launcher_module)
registry.register(deep_search_module)
registry.register(account_roles_module)
registry.register(archive_module)
registry.register(runlog_workbench_module)
registry.register(investigation_module)
registry.register(inspect_module)
registry.register(audit_module)
registry.register(noun_configure_module)
registry.register(adjective_editor_module)
registry.register(verb_editor_module)
registry.register(adverb_editor_module)
registry.register(conjunction_module)
registry.register(camera_module)
registry.register(prepositional_phrase_module)
registry.register(custom_upload_module)
registry.register(noun_workbench_module)
registry.register(verb_workbench_module)
registry.register(template_module)
registry.register(backup_module)
registry.register(nodes_compliance_module)
registry.register(tutorial_module)

# Mount all module routers (launcher + every module's nodes)
registry.mount_all(app)

# Choose what the root (/) should do:
@app.get("/", include_in_schema=False)
def root():
    # Option A (recommended): land on the new launcher node
    return RedirectResponse(url="/launcher")
    # Option B: keep your legacy static launcher page
    # return html("launcher.html")

# ─── Legacy GUI page routes RETIRED (front-end refactor Tier 0) ────────────
# The parallel `/gui/*` table served the SAME HTML files raw with NO asset injection
# (no login/orchestrate/state-dock) — a second, divergent serving path for every page.
# Node routes (/noun_configure, …) are now the single serving path. (test_page — the
# disposable Glide-grid dev scaffolding — was retired; test_parser keeps its node route.)
# (Note: `/gui/grid/save/...` is a real grid-save API in api.routers.runlog_workbench —
#  unrelated to this page table — and is untouched.)

# ─── API/GUI routers (one list, not 24 hand-written include_router calls) ───
from tools import s3_viewer
_API_ROUTERS = [
    noun_gui_router, investigation_gui_router, adjective_gui_router, verb_gui_router,
    conjunction_gui_router, adverb_gui_router, word_router_router, deep_search_gui_router,
    camera_gui_router, runlog_workbench_gui_router, project_api_router,
    custom_upload_gui_router, run_customs_gui_router,
    noun_workbench_gui_router, verb_workbench_gui_router, audit_gui_router,
    archive_workbench_gui_router, template_gui_router, account_roles_gui_router,
    backup_gui_router, compliance_gui_router, s3_viewer.router,
]
for _r in _API_ROUTERS:
    app.include_router(_r)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.app:app", host="127.0.0.1", port=8000, reload=False)