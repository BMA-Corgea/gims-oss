# nodes/pages/test_parser.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

# Parser test harness. Now a first-class node route at /test_parser, restructured onto
# the shared Watery app-shell (shell=True): the HTML is a content-only fragment wrapped
# in the shell chrome (title + profile chip + login), with the gims.js toolkit + the
# page's own parser_test.css / parser_test.js layered on top.
test_parser_node = make_page_node(
    name="Test Parser",
    route="/test_parser",
    html_file="test_parser.html",
    kind=NodeKind.UI,
    icon="🤖",
    label="Parser Test",
    in_schema=True,
    shell=True,
    title="Parser Test",
    kicker="Tests",
    nav_key="test_parser",
    page_css="parser_test.css",
    # Phase 6 (tool pages T9): a React page — parser_test.jsx (built to static/lib/parser_test.js)
    # fills #parser-test-root. vendor.js (React) then the page bundle, deferred.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/parser_test.js"></script>'
    ),
)
