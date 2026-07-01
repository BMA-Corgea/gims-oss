# nodes/pages/noun_configure_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

noun_configure_node = make_page_node(
    name="Noun Configure",
    route="/noun_configure",
    html_file="noun_configure.html",
    kind=NodeKind.UI,
    icon="📦",
    label="Noun Configure",
    in_schema=True,
    shell=True,
    title="Noun Configure",
    kicker="Schemas",
    nav_key="noun_configure",
    page_css="noun_configure.css",
    # Phase 6 (editors E4): a React page — noun_configure.jsx (built to static/lib/noun_configure.js)
    # fills #noun-configure-root. Loads vendor.js (React → window.React) then the page bundle, deferred.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/noun_configure.js"></script>'
    ),
)
