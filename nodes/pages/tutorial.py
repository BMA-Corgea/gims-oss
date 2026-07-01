# nodes/pages/tutorial.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

# Onboarding / "how GIMS works" page (Phase 3). Static explainer of the parts-of-speech
# model + the configure→enter→operate→investigate flow; rendered in the Watery shell.
tutorial_node = make_page_node(
    name="Tutorial",
    route="/tutorial",
    html_file="tutorial.html",
    kind=NodeKind.UI,
    icon="📖",
    label="Tutorial",
    in_schema=True,
    shell=True,
    title="GIMS Tutorial",
    kicker="Help",
    nav_key="tutorial",
    page_css="tutorial.css",
    page_script="tutorial.js",
)
