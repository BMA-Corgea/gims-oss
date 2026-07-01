# nodes/pages/conjunction_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

conjunction_node = make_page_node(
    name="Conjunction Editor",
    route="/conjunction_editor",
    html_file="conjunction.html",
    kind=NodeKind.UI,
    icon="🔗",
    label="Conjunction Editor",
    in_schema=True,
    shell=True,
    title="Conjunction Editor",
    kicker="Schemas",
    nav_key="conjunction_editor",
    page_css="conjunction.css",
    # Phase 6 (editors E6): a React page — conjunction.jsx (built to static/lib/conjunction.js)
    # fills #conjunction-root. Loads vendor.js (React → window.React) then the page bundle, deferred.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/conjunction.js"></script>'
    ),
)
