# nodes/pages/verb_editor_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

verb_editor_node = make_page_node(
    name="Verb Editor",
    route="/verb_editor",
    html_file="verb_editor.html",
    kind=NodeKind.UI,
    icon="⚡",
    label="Verb Editor",
    in_schema=True,
    shell=True,
    title="Verb Editor",
    kicker="Schemas",
    nav_key="verb_editor",
    page_css="verb_editor.css",
    # Phase 6 (editors E5): a React page — verb_editor.jsx (built to static/lib/verb_editor.js)
    # fills #verb-editor-root. Loads vendor.js (React → window.React) then the page bundle, deferred.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/verb_editor.js"></script>'
    ),
)
