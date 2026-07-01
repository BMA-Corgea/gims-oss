# nodes/pages/audit_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

audit_node = make_page_node(
    name="Audit",
    route="/audit",
    html_file="audit.html",
    kind=NodeKind.UI,
    icon="🛡️",
    label="Audit",
    in_schema=True,
    shell=True,
    title="Audit Workbench",
    kicker="Data Quality",
    nav_key="audit",
    page_css="audit.css",
    # Phase 6: React. Load the shared React vendor chunk THEN the page bundle (both deferred,
    # in order → window.GIMS + #audit-root ready before audit.js mounts). The page bundle
    # externalizes React to vendor.js (shared by every React page). Build: `node build.mjs`.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/audit.js"></script>'
    ),
)
