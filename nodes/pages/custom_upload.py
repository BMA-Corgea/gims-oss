# nodes/pages/custom_upload_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

custom_upload_node = make_page_node(
    name="Custom Upload",
    route="/custom_upload",
    html_file="custom_upload.html",
    kind=NodeKind.UI,
    icon="📝",
    label="Custom Upload",
    in_schema=True,
    shell=True,
    title="Custom Parsers",
    kicker="Tools",
    nav_key="custom_upload",
    page_css="custom_upload.css",
    # Phase 6 (tool pages T6): a React page — custom_upload.jsx (built to static/lib/custom_upload.js)
    # fills #custom-upload-root. vendor.js (React) then the page bundle, deferred.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/custom_upload.js"></script>'
    ),
)
