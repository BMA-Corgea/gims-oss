# runlog_workbench_module.py
from __future__ import annotations

from core.orchestration.module import Module
from nodes.pages.runlog_workbench_node import runlog_workbench_node
from nodes.login_fastapi_users_node import login_node
from nodes.orchestrated_fetch_node import orchestrated_fetch_node
from nodes.login_rules_node import login_rules_node
from nodes.state_dock_node import state_dock_node

runlog_workbench_module = Module(
    name="Runlog Workbench",
    nodes=[
        runlog_workbench_node,
        login_node,
        orchestrated_fetch_node,
        login_rules_node,
        state_dock_node,
    ],
    version="0.1.0",
    description="Inspect and edit run entries: statuses, overrides, attachments, and linked instances.",
    roles=set(),  # empty => public; RBAC still enforced by endpoints where used
    inject={
        "/runlog-workbench": {
            "scripts": [
                "orchestrate/inject.js",
            ],
            "stylesheets": []
        }
    },
)
