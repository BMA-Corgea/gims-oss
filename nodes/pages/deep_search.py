# nodes/pages/deep_search_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

deep_search_node = make_page_node(
    name="Deep Search",
    route="/deep_search",
    html_file="deep_search.html",
    kind=NodeKind.UI,
    icon="🔎",
    label="Deep Search",
    in_schema=True,
    shell=True,
    title="Deep Search",
    kicker="Search",
    nav_key="deep_search",
    page_css="deep_search.css",
    # Phase 6 (tool pages T4): a React page — deep_search.jsx (built to static/lib/deep_search.js)
    # fills #deep-search-root. vendor.js (React) then the page bundle, deferred.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/deep_search.js"></script>'
    ),
)
