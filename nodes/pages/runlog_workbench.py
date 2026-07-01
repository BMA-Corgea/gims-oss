# nodes/pages/runlog_workbench_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

runlog_workbench_node = make_page_node(
    name="Runlog Workbench",
    route="/runlog_workbench",
    html_file="runlog_workbench.html",
    kind=NodeKind.UI,
    icon="🧰",
    label="Runlog",
    in_schema=True,
    shell=True,
    title="Runlog Workbench",
    kicker="Operations",
    nav_key="runlog_workbench",
    page_css="runlog_workbench.css",
    # Phase 6 (runlog S12 cutover): a full React page. runlog_workbench.jsx (built to
    # static/lib/runlog_workbench.js) fills #runlog-root — toolbar + run log + the 7-tab data-dump
    # drawer (status/data-entry grid/gates+e-sign/raw/interpretation/overrides/adverbs). The vanilla
    # static/scripts/runlog_workbench.js is deleted. Deferred, in order: vendor.js (React 18 →
    # window.React) → glide-vendor.js (Glide → window.GlideDataGrid) → the page bundle (which
    # dynamically imports /static/lib/data_grid.js for the editable grid). One React on the page.
    head_extra=(
        '<link rel="stylesheet" href="/static/lib/glide-vendor.css">'
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/glide-vendor.js"></script>'
        '<script defer src="/static/lib/runlog_workbench.js"></script>'
    ),
)
