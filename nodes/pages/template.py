# nodes/pages/template_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

template_node = make_page_node(
    name="Template",
    route="/template",
    html_file="template.html",
    kind=NodeKind.UI,
    icon="🌱",
    label="Template",
    in_schema=True,
    shell=True,
    title="Template Manager",
    kicker="Admin",
    nav_key="template",
    page_css="template.css",
    # Phase 6 (tool pages T1): a React page — template.jsx (built to static/lib/template.js)
    # fills #template-root. vendor.js (React) then the page bundle, deferred.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/template.js"></script>'
    ),
)
