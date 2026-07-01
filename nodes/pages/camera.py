# nodes/pages/camera_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

camera_node = make_page_node(
    name="Image Capture",
    route="/camera",
    html_file="camera.html",
    kind=NodeKind.UI,
    icon="📸",
    label="Image Capture",
    in_schema=True,
    shell=True,
    title="Image Capture",
    kicker="Tools",
    nav_key="camera",
    page_css="camera.css",
    # Phase 6 (tool pages T5): a React page — camera.jsx (built to static/lib/camera.js)
    # fills #camera-root. vendor.js (React) then the page bundle, deferred.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/camera.js"></script>'
    ),
)
