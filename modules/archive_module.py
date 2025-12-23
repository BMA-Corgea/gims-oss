# archive_module.py
from __future__ import annotations

from core.orchestration.module import Module
from nodes.pages.archive_node import archive_node
from nodes.login_fastapi_users_node import login_node
from nodes.orchestrated_fetch_node import orchestrated_fetch_node
from nodes.login_rules_node import login_rules_node
from nodes.state_dock_node import state_dock_node


archive_module = Module(
    name="Archive",
    nodes=[
        archive_node,
        login_node,
        orchestrated_fetch_node,
        login_rules_node,
        state_dock_node,
    ],
    version="0.1.0",
    description="Archive Workbench with login, fetch, RBAC, state dock, and compliance logging",
    roles=set(),  # empty => public; RBAC still enforced by endpoints where used
    inject={
        "/archive-workbench": {
            "scripts": [
                "orchestrate/inject.js",
            ],
            "stylesheets": []
        }
    },
)