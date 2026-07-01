# nodes/pages/archive_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

archive_node = make_page_node(
    name="Archive",
    route="/archive-workbench",
    html_file="archive_workbench.html",
    kind=NodeKind.UI,
    icon="🗄️",
    label="Archive",
    in_schema=True,
    shell=True,
    title="Archive",
    kicker="Searches",
    nav_key="archive_workbench",
    page_css="archive_workbench.css",
    # Phase 6 (admin track A3): a React page — archive_workbench.jsx (built to
    # static/lib/archive_workbench.js) fills #archive-workbench-root. vendor.js (React) then the bundle.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/archive_workbench.js"></script>'
    ),
)
