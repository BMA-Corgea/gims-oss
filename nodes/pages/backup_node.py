# nodes/pages/backup_node.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse

from core.orchestration.node import Node, NodeKind
from core.orchestration.registry import registry # <-- Import added

def _resolve_html() -> Optional[Path]:
    """
    Find gui/components/backup.html from common roots.
    Assumes you run uvicorn from repo root; falls back to relative to this file.
    """
    candidates = [
        Path("gui/components/backup.html"),
        Path(__file__).resolve().parents[1] / "gui" / "components" / "backup.html",
        Path(__file__).resolve().parents[2] / "gui" / "components" / "backup.html",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None

def _tags_for(target: str) -> str: # <-- Helper function added
    """Collect <link> and <script> tags from registry injections."""
    inj = registry.gather_injections(target)
    tags: list[str] = []
    for href in inj.get("stylesheets", []):
        tags.append(f'<link rel="stylesheet" href="{href}">')
    for src in inj.get("scripts", []):
        tags.append(f'<script src="{src}"></script>')
    return "\n".join(tags)


router = APIRouter()

@router.get("/backup", response_class=HTMLResponse)
async def backup_page(): # <-- Function modified
    html_path = _resolve_html()
    if not html_path:
        return PlainTextResponse(
            "backup.html not found. Expected at gui/components/backup.html",
            status_code=404,
        )
    html = html_path.read_text(encoding="utf-8")

    # Inject module/registry-provided assets for this page
    inject = _tags_for("/backup")
    if inject:
        if "</body>" in html:
            html = html.replace("</body>", f"{inject}\n</body>")
        else:
            html += "\n" + inject

    return HTMLResponse(html)


backup_node = Node(
    name="backup",
    kind=NodeKind.UI,
    router=router,
    # Convention: entry path is stored so the launcher can link to it.
    template=_resolve_html(),
    meta={"entry_path": "/backup", "icon": "💾", "label": "backup"},
)