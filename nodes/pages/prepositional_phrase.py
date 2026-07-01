# nodes/pages/prepositional_phrase_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

prepositional_phrase_node = make_page_node(
    name="Prepositional Phrase Runner",
    route="/prepositional_phrase_runner",
    html_file="prepositional_phrase_runner.html",
    kind=NodeKind.UI,
    icon="📜",
    label="Prepositional Phrase Runner",
    in_schema=True,
    shell=True,
    title="Prepositional Phrase Runner",
    kicker="Tools",
    nav_key="prepositional_phrase_runner",
    page_css="prepositional_phrase_runner.css",
    # Phase 6 (tool pages T8): a React page — prepositional_phrase_runner.jsx (built to
    # static/lib/prepositional_phrase_runner.js) fills #prep-runner-root. vendor.js (React) then the bundle.
    head_extra=(
        '<script defer src="/static/lib/vendor.js"></script>'
        '<script defer src="/static/lib/prepositional_phrase_runner.js"></script>'
    ),
)
