# nodes/pages/noun_workbench_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

noun_workbench_node = make_page_node(
    name="Noun Workbench",
    route="/noun_workbench",
    html_file="noun_workbench.html",
    kind=NodeKind.UI,
    icon="📇",
    label="Noun Workbench",
    in_schema=True,
    shell=True,
    title="Noun Workbench",
    kicker="Operations",
    nav_key="noun_workbench",
    page_css="noun_workbench.css",
    # Phase 6 (tool pages T7): a React page — noun_workbench.jsx (built to static/lib/noun_workbench.js)
    # fills #noun-workbench-root. vendor.js (React) then the page bundle, deferred.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/noun_workbench.js"></script>'
    ),
)
