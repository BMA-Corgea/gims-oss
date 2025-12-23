# modules/verb_workbench_module.py
from __future__ import annotations

from core.orchestration.module import Module
from nodes.login_fastapi_users_node import login_node
from nodes.orchestrated_fetch_node import orchestrated_fetch_node
from nodes.state_dock_node import state_dock_node
from nodes.login_rules_node import login_rules_node


# ─── Config block (edit this section when cloning) ─────────────────────────────
from nodes.pages.verb_workbench_node import verb_workbench_node
MODULE_NAME        = "Verb Workbench"
DESCRIPTION        = "UI node that serves gui/components/verb_workbench.html"
VERSION            = "0.1.0"
ROUTE_PATH         = "/verb_workbench"
INJECT_SCRIPTS     = ["/orchestrate/inject.js"]
INJECT_STYLESHEETS = []
# ──────────────────────────────────────────────────────────────────────────────

verb_workbench_module = Module(
    name=MODULE_NAME,
    nodes=[
        verb_workbench_node,
        login_node,
        orchestrated_fetch_node,
        login_rules_node,
        state_dock_node,
    ],
    version=VERSION,
    description=DESCRIPTION,
    roles=set(),  # public; endpoint-level RBAC still applies
    inject={
        ROUTE_PATH: {
            "scripts": INJECT_SCRIPTS,
            "stylesheets": INJECT_STYLESHEETS,
        }
    },
)
