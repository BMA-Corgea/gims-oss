# api/HTML_API.py

import sys
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

# Add the project root to sys.path so gui/, core/, utils/ can be imported
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import json
from core.investigation import get_lineage, render_lineage
from utils import disambiguation as dis
from gui.noun_gui import router as noun_gui_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ROOT = Path(__file__).parent.parent
app.mount("/static", StaticFiles(directory=PROJECT_ROOT), name="static")

# ─── Include Routers ──────────────────────────────────────────────────────────
app.include_router(noun_gui_router)

# ─── Homepage Launcher ────────────────────────────────────────────────────────
@app.get("/")
def root():
    return FileResponse(PROJECT_ROOT / "gui/components/launcher.html")

# ─── Dynamic GUI HTML Loader ──────────────────────────────────────────────────
@app.get("/gui/{page}")
def serve_gui_page(page: str):
    html_file = PROJECT_ROOT / f"gui/components/{page}.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail=f"{page}.html not found")
    return FileResponse(html_file)

# ─── Records API (Dynamic Project/Noun Context) ───────────────────────────────
@app.get("/records")
def get_records(project: str, noun_type: str):
    project_path = Path("projects") / project
    path = dis.get_noun_items(project_path, noun_type)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Items file not found")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

# ─── Lineage API (Dynamic Project/Noun Context) ───────────────────────────────
@app.get("/lineage/{submission_id}")
def get_lineage_view(submission_id: str, project: str, noun_type: str):
    project_path = Path("projects") / project
    path = dis.get_noun_items(project_path, noun_type)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Items file not found")

    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    schema = dis.get_noun_schema(project_path, noun_type)
    pk_field = schema.get("primary_id_field", f"{noun_type.lower()}_id")

    match = next((r for r in records if r.get(pk_field) == submission_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Record not found")

    lineage = get_lineage(project_path, noun_type, match)
    output = render_lineage(lineage, project_path)
    return {"lineage": output}
