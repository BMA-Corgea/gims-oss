# nodes/pages/launcher_node.py
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node

launcher_node = make_page_node(
    name="Launcher",
    route="/launcher",
    html_file="launcher.html",
    kind=NodeKind.LAUNCHER,
    icon="🚀",
    label="Launcher",
    in_schema=True,
)
