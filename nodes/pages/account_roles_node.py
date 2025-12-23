# nodes/pages/account_roles_node.py
from __future__ import annotations

"""
Accounts & Roles — UI page node

This node ONLY serves the HTML page for the admin UI and lets the registry
inject any extra CSS/JS (for example, the login module’s injectors).

Your actual data APIs (role CRUD, approvals, password reset, CSRF, etc.)
must live in a GUI backend (e.g. gui/account_roles_gui.py) or in a dedicated
nodes/account_roles_api_node.py. Keeping the page route separate from data APIs
keeps things tidy and predictable.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse

from core.orchestration.node import Node, NodeKind
from core.orchestration.registry import registry


def _resolve_html() -> Optional[Path]:
    """
    Locate gui/components/account_roles.html from common roots (repo layout tolerant).
    """
    candidates = [
        Path("gui/components/account_roles.html"),
        Path(__file__).resolve().parents[1] / "gui" / "components" / "account_roles.html",
        Path(__file__).resolve().parents[2] / "gui" / "components" / "account_roles.html",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _tags_for(target: str) -> str:
    """
    Ask the registry for assets registered for this target path and turn them
    into <link> and <script> tags to inject into the HTML.
    """
    inj = registry.gather_injections(target)
    tags: list[str] = []
    for href in inj.get("stylesheets", []):
        tags.append(f'<link rel="stylesheet" href="{href}">')
    for src in inj.get("scripts", []):
        tags.append(f'<script src="{src}"></script>')
    return "\n".join(tags)


router = APIRouter()


@router.get("/account_roles", response_class=HTMLResponse, include_in_schema=False)
async def account_roles_page():
    """
    Serve the Accounts & Roles admin HTML.

    Notes:
    - This endpoint does NOT expose APIs. It only serves the UI page.
    - We append any module/registry-provided assets right before </body>.
      For example: the login module registers its injectors so this page
      gets the auth shim, profile tab, and CSRF bootstrap automatically.
    """
    html_path = _resolve_html()
    if not html_path:
        return PlainTextResponse(
            "account_roles.html not found. Expected at gui/components/account_roles.html",
            status_code=404,
        )
    html = html_path.read_text(encoding="utf-8")

    # Inject module/registry-provided assets (e.g., login injectors, shims)
    inject = _tags_for("/account_roles")
    if inject:
        if "</body>" in html:
            html = html.replace("</body>", f"{inject}\n</body>")
        else:
            html += "\n" + inject

    return HTMLResponse(html)


account_roles_node = Node(
    name="Accounts & Roles",
    kind=NodeKind.UI,  # front-end page (not an API, not a login provider)
    router=router,
    template=_resolve_html(),
    meta={
        "entry_path": "/account_roles",
        "icon": "🛂",
        "label": "Accounts & Roles",
    },
)
