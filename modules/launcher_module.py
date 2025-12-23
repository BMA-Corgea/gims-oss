from __future__ import annotations

from core.orchestration.module import Module
from nodes.pages.launcher_node import launcher_node
from nodes.login_fastapi_users_node import login_node
from nodes.state_dock_node import state_dock_node
from nodes.star_spirits_state_node import star_state_node
from nodes.star_spirits_ui_node import star_ui_node
from nodes.star_spirits_trigger_node import star_trigger_node
from nodes.dual_dataentry_node import dual_dataentry_node
from nodes.orchestrated_fetch_node import orchestrated_fetch_node
from nodes.auto_backup_node import auto_backup_node
from nodes.auth_guard_node import auth_guard_node

launcher_module = Module(
    name="Launcher",
    nodes=[
        launcher_node,
        login_node,
        state_dock_node,
#        star_state_node,
#        star_ui_node,
#        star_trigger_node,
        dual_dataentry_node,
        orchestrated_fetch_node,
        auto_backup_node,
        auth_guard_node,
    ],
    inject={
        "/": {
            "stylesheets": [],
        },
        # cover your real launcher path too:
        "/launcher": {
            "scripts": [
                "/state-dock/participate.js",
                "orchestrate/inject.js", 
            ],
            "stylesheets": [],
        },
    },
)
