# nodes/pages/launcher_node.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse

from core.orchestration.node import Node, NodeKind
from core.orchestration.registry import registry

router = APIRouter()

def _resolve_html() -> Optional[Path]:
    for p in [
        Path("gui/components/launcher.html"),
        Path(__file__).resolve().parents[1] / "gui" / "components" / "launcher.html",
        Path(__file__).resolve().parents[2] / "gui" / "components" / "launcher.html",
    ]:
        if p.exists():
            return p
    return None

def _tags_for(target: str) -> str:
    inj = registry.gather_injections(target)
    tags = []
    for href in inj.get("stylesheets", []):
        tags.append(f'<link rel="stylesheet" href="{href}">')
    for src in inj.get("scripts", []):
        tags.append(f'<script src="{src}"></script>')
    return "\n".join(tags)

@router.get("/launcher", response_class=HTMLResponse)
async def launcher_page():
    html_path = _resolve_html()
    if not html_path:
        return PlainTextResponse(
            "launcher.html not found. Expected at gui/components/launcher.html",
            status_code=404,
        )
    html = html_path.read_text(encoding="utf-8")

    # Inject module/registry-provided assets for this page
    inject = _tags_for("/launcher")
    if inject:
        if "</body>" in html:
            html = html.replace("</body>", f"{inject}\n</body>")
        else:
            html += "\n" + inject

    return HTMLResponse(html)

launcher_node = Node(
    name="Launcher",
    kind=NodeKind.LAUNCHER,
    router=router,
    template=_resolve_html(),
    meta={"entry_path": "/launcher", "icon": "🚀", "label": "Launcher"},
)
