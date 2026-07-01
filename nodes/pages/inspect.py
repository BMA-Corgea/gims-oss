from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

# Instance Inspector — a read-only viewer for a single noun or run instance, opened via
# deep-links from anywhere (Investigation chips, deep_search, runlog, …):
#   /inspect?project=P&kind=noun&type=Sample&id=SMP-0005
#   /inspect?project=P&kind=run&group=Chemistry&run_id=R-1001&verb=Chemistry%20Panel
# It reads only existing endpoints; no new data routes. (Replaces the old broken deep-links
# that dumped users on a blank Noun Workbench.)
inspect_node = make_page_node(
    name="Instance Inspector",
    route="/inspect",
    html_file="inspect.html",
    kind=NodeKind.UI,
    icon="🔎",
    label="Inspector",
    in_schema=True,
    shell=True,
    title="Instance Inspector",
    kicker="Searches",
    nav_key="inspect",
    page_css="inspect.css",
    # Phase 6: React. Load the shared vendor chunk (React/ReactDOM) THEN the page bundle, both
    # deferred + in order (deferred scripts run in document order, after parse → window.GIMS +
    # #inspect-root ready, window.React set before the page reads it). The page bundle (~8KB)
    # externalizes React to vendor.js (~139KB, shared by every React page).
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/inspect.js"></script>'
    ),
)
