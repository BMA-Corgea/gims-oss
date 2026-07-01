# nodes/pages/verb_workbench_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

verb_workbench_node = make_page_node(
    name="Verb Workbench",
    route="/verb_workbench",
    html_file="verb_workbench.html",
    kind=NodeKind.UI,
    icon="✨",
    label="Verb Workbench",
    in_schema=True,
    shell=True,
    title="Verb Workbench",
    kicker="Operations",
    nav_key="verb_workbench",
    page_css="noun_workbench.css",   # shared form-workbench stylesheet
    # Phase 6 (tool pages T3): a React page — verb_workbench.jsx (built to static/lib/verb_workbench.js)
    # fills #verb-workbench-root. vendor.js (React) then the page bundle, deferred.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/verb_workbench.js"></script>'
    ),
)
