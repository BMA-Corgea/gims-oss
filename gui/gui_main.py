# gui/gui_main.py

from __future__ import annotations

import sys
from api import json_proxy
sys.modules["json"] = json_proxy
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse

# ─── Orchestration (nodes/modules/launcher) ───────────────────────────────
# These keep core clean and simply wire existing routers/nodes as "modules".
from core.orchestration.registry import registry
from modules.launcher_module import launcher_module
from modules.deep_search_module import deep_search_module
from modules.archive_module import archive_module
from modules.account_roles_module import account_roles_module
from modules.runlog_workbench_module import runlog_workbench_module
from modules.investigation_module import investigation_module
from modules.audit_module import audit_module
from modules.noun_configure_module import noun_configure_module
from modules.adjective_editor_module import adjective_editor_module
from modules.verb_editor_module import verb_editor_module
from modules.adverb_editor_module import adverb_editor_module
from modules.conjunction_module import conjunction_module
from modules.camera_module import camera_module
from modules.prepositional_phrase_module import prepositional_phrase_module
from modules.custom_upload_module import custom_upload_module
from modules.noun_workbench_module import noun_workbench_module
from modules.verb_workbench_module import verb_workbench_module
from modules.template_module import template_module
from modules.backup_module import backup_module
from modules.nodes_compliance_module import nodes_compliance_module

# ─── Routers (your existing GUI/API backends) ─────────────────────────────
from gui.noun_gui                import router as noun_gui_router
from gui.investigation_gui       import router as investigation_gui_router
from gui.adjective_gui           import router as adjective_gui_router
from gui.verb_gui                import router as verb_gui_router
from gui.conjunction_gui         import router as conjunction_gui_router
from gui.adverb_gui              import router as adverb_gui_router
from gui.deep_search_gui         import router as deep_search_gui_router
from gui.camera_gui              import router as camera_gui_router
from gui.runlog_workbench_gui    import router as runlog_workbench_gui_router
from api.project_api             import router as project_api_router
from api.grid_adapter            import router as grid_router
from gui.test_page_gui           import router as test_page_gui_router
from gui.custom_upload_gui       import router as custom_upload_gui_router
from gui.run_customs_gui         import router as run_customs_gui_router
from gui.noun_workbench_gui      import router as noun_workbench_gui_router
from gui.verb_workbench_gui      import router as verb_workbench_gui_router
from gui.audit_gui               import router as audit_gui_router
from gui.archive_workbench_gui   import router as archive_workbench_gui_router
from gui.template_gui            import router as template_gui_router
from gui.account_roles_gui       import router as account_roles_gui_router
from gui.backup_gui          import router as backup_gui_router
from gui.nodes_compliance_gui import router as compliance_gui_router

app = FastAPI(title="GIMS GUI + Orchestration")

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
HTML_DIR = Path(__file__).parent / "components"

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

# Mount all module routers (launcher + every module's nodes)
registry.mount_all(app)

# Choose what the root (/) should do:
@app.get("/", include_in_schema=False)
def root():
    # Option A (recommended): land on the new launcher node
    return RedirectResponse(url="/launcher")
    # Option B: keep your legacy static launcher page
    # return html("launcher.html")

# ─── Legacy GUI pages (kept intact) ───────────────────────────────────────
@app.get("/gui/noun_configure")
def noun_configure():
    return html("noun_configure.html")

@app.get("/gui/investigation")
def investigation_gui():
    return html("investigation.html")

@app.get("/gui/adjective_editor")
def adjective_editor():
    return html("adjective_editor.html")

@app.get("/gui/verb_editor")
def verb_editor():
    return html("verb_editor.html")

@app.get("/gui/conjunction_manager")
def conjunction_manager():
    return html("conjunction.html")

@app.get("/gui/adverb_editor")
def adverb_editor():
    return html("adverb.html")

@app.get("/gui/deep_search")
def deep_search():
    return html("deep_search.html")

@app.get("/gui/camera")
def camera():
    return html("camera.html")

@app.get("/gui/runlog_workbench")
def runlog_workbench():
    return html("runlog_workbench.html")

@app.get("/gui/noun_workbench")
def noun_workbench():
    return html("noun_workbench.html")

@app.get("/gui/verb_workbench")
def verb_workbench():
    return html("verb_workbench.html")

@app.get("/gui/test_page")
def test_page():
    return html("test_page.html")

@app.get("/gui/custom_upload")
def custom_upload():
    return html("custom_upload.html")

@app.get("/gui/test_parser")
def test_parser():
    return html("test_parser.html")

@app.get("/gui/audit")
def audit():
    return html("audit.html")

@app.get("/gui/archive_workbench")
def archive_workbench():
    return html("archive_workbench.html")

@app.get("/gui/prepositional_phrase_runner")
def prepositional_phrase_runner():
    return html("prepositional_phrase_runner.html")

@app.get("/gui/template")
def template():
    return html("template.html")

@app.get("/gui/backup")
def backup():
    return html("backup.html")

@app.get("/gui/nodes_compliance")
def compliance():
    return html("nodes_compliance.html")

# ─── Attach existing API routers (unchanged) ──────────────────────────────
app.include_router(noun_gui_router)
app.include_router(investigation_gui_router)
app.include_router(adjective_gui_router)
app.include_router(verb_gui_router)
app.include_router(conjunction_gui_router)
app.include_router(adverb_gui_router)
app.include_router(deep_search_gui_router)
app.include_router(camera_gui_router)
app.include_router(runlog_workbench_gui_router)
app.include_router(project_api_router)
app.include_router(grid_router)
app.include_router(test_page_gui_router)
app.include_router(custom_upload_gui_router)
app.include_router(run_customs_gui_router)
app.include_router(noun_workbench_gui_router)
app.include_router(verb_workbench_gui_router)
app.include_router(audit_gui_router)
app.include_router(archive_workbench_gui_router)
app.include_router(template_gui_router)
app.include_router(account_roles_gui_router)
app.include_router(backup_gui_router)
app.include_router(compliance_gui_router)

from tools import s3_viewer
app.include_router(s3_viewer.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("gui.gui_main:app", host="127.0.0.1", port=8000, reload=False)