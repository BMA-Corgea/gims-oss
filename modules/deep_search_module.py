# modules/deep_search_module.py
from __future__ import annotations

from core.orchestration.module import Module
from nodes.login_fastapi_users_node import login_node
from nodes.orchestrated_fetch_node import orchestrated_fetch_node
from nodes.state_dock_node import state_dock_node
from nodes.login_rules_node import login_rules_node

# ─── Config block (edit this section when cloning) ─────────────────────────────
from nodes.pages.deep_search_node import deep_search_node
MODULE_NAME        = "Deep Search"
DESCRIPTION        = "UI node that serves gui/components/deep_search.html"
VERSION            = "0.1.0"
ROUTE_PATH         = "/deep_search"        # must match the node’s route
INJECT_SCRIPTS     = ["/orchestrate/inject.js"]
INJECT_STYLESHEETS = []
# ──────────────────────────────────────────────────────────────────────────────

deep_search_module = Module(
    name=MODULE_NAME,
    nodes=[
        deep_search_node,
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
