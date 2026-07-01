# nodes/pages/backup_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

backup_node = make_page_node(
    name="backup",
    route="/backup",
    html_file="backup.html",
    kind=NodeKind.UI,
    icon="💾",
    label="backup",
    in_schema=True,
    shell=True,
    title="Backup Manager",
    kicker="Admin",
    nav_key="backup",
    page_css="backup.css",
    # Phase 6 (tool pages T2): a React page — backup.jsx (built to static/lib/backup.js)
    # fills #backup-root. vendor.js (React) then the page bundle, deferred.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/backup.js"></script>'
    ),
)
