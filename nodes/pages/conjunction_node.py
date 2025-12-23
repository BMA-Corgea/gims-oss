# nodes/pages/conjunction_node.py
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse

from core.orchestration.node import Node, NodeKind
from core.orchestration.registry import registry

# ─── Config block (edit this section only when cloning) ────────────────────────
NODE_NAME    = "Conjunction Editor"
ROUTE_PATH   = "/conjunction_editor"           # URL endpoint
HTML_FILE    = "conjunction.html"       # in gui/components/
ICON         = "🔗"                       # launcher icon
LABEL        = "Conjunction Editor"            # launcher label
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_html() -> Optional[Path]:
    """Find gui/components/<HTML_FILE> from common roots."""
    candidates = [
        Path("gui/components") / HTML_FILE,
        Path(__file__).resolve().parents[1] / "gui" / "components" / HTML_FILE,
        Path(__file__).resolve().parents[2] / "gui" / "components" / HTML_FILE,
    ]
    for p in candidates:
        if p.exists():
            return p
    return None

def _tags_for(target: str) -> str:
    """Collect <link> and <script> tags from registry injections."""
    inj = registry.gather_injections(target)
    tags: list[str] = []
    for href in inj.get("stylesheets", []):
        tags.append(f'<link rel="stylesheet" href="{href}">')
    for src in inj.get("scripts", []):
        tags.append(f'<script src="{src}"></script>')
    return "\n".join(tags)

router = APIRouter()

@router.get(ROUTE_PATH, response_class=HTMLResponse)
async def page():
    html_path = _resolve_html()
    if not html_path:
        return PlainTextResponse(
            f"{HTML_FILE} not found. Expected at gui/components/{HTML_FILE}",
            status_code=404,
        )
    html = html_path.read_text(encoding="utf-8")

    # Inject module/registry-provided assets for this page
    inject = _tags_for(ROUTE_PATH)
    if inject:
        if "</body>" in html:
            html = html.replace("</body>", f"{inject}\n</body>")
        else:
            html += "\n" + inject

    return HTMLResponse(html)

conjunction_node = Node(
    name=NODE_NAME,
    kind=NodeKind.UI,
    router=router,
    template=_resolve_html(),
    meta={"entry_path": ROUTE_PATH, "icon": ICON, "label": LABEL},
)
