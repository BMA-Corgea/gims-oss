# nodes/pages/adjective_editor_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

adjective_editor_node = make_page_node(
    name="Adjective Editor",
    route="/adjective_editor",
    html_file="adjective_editor.html",
    kind=NodeKind.UI,
    icon="🏷️",
    label="Adjective Editor",
    in_schema=True,
    shell=True,
    title="Adjective Editor",
    kicker="Schemas",
    nav_key="adjective_editor",
    page_css="adjective_editor.css",
    # Phase 6 (editors E2): a React page — adjective_editor.jsx (built to static/lib/adjective_editor.js)
    # fills #adjective-root. Loads vendor.js (React → window.React) then the page bundle, deferred in order.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/adjective_editor.js"></script>'
    ),
)
