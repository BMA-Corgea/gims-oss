# nodes/pages/investigation_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

investigation_node = make_page_node(
    name="Investigation",
    route="/investigation",
    html_file="investigation.html",
    kind=NodeKind.UI,
    icon="🕵️",
    label="Investigation",
    in_schema=True,
    shell=True,
    title="Lineage Investigator",
    kicker="Search",
    nav_key="investigation",
    page_css="investigation.css",
    # Phase 6 (remaining vanilla R1): a React page — investigation.jsx (built to
    # static/lib/investigation.js) fills #investigation-root. vendor.js (React) then the page bundle.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/investigation.js"></script>'
    ),
)
