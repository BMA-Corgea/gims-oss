# nodes/pages/adverb_editor_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

adverb_editor_node = make_page_node(
    name="Adverb Editor",
    route="/adverb_editor",
    html_file="adverb.html",
    kind=NodeKind.UI,
    icon="🎯",
    label="Adverb Editor",
    in_schema=True,
    shell=True,
    title="Adverb Editor",
    kicker="Schemas",
    nav_key="adverb_editor",
    page_css="adverb.css",
    # Phase 6 (editors E3): a React page — adverb_editor.jsx (built to static/lib/adverb_editor.js)
    # fills #adverb-root with the master/detail editor (the descriptor twin of the adjective editor).
    # Reuses the adjective editor's ae-* master/detail CSS + vendor.js, then the page bundle (deferred).
    head_extra=(
        '<link rel="stylesheet" href="/static/styles/adjective_editor.css">'
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/adverb_editor.js"></script>'
    ),
)
