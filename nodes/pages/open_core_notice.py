# OPEN-CORE STUB — auto-written by tools/oss_export. The GIMS public build ships without
# the login/auth, roles, and 21 CFR Part 11 compliance layers; this file preserves the
# import surface the rest of the app needs, implemented as an OPEN single-user no-op.
# Do NOT deploy the open-core build as a shared/multi-user service.
from __future__ import annotations

from core.orchestration.node import NodeKind
from core.orchestration.page_node import make_page_node


def _notice_node(route, title, nav_key, icon):
    return make_page_node(
        name=title,
        route=route,
        html_file="open_core_notice.html",
        kind=NodeKind.UI,
        icon=icon,
        label=title,
        in_schema=True,
        shell=True,
        title=title,
        kicker="Open core",
        nav_key=nav_key,
    )


account_roles_notice_node = _notice_node("/account_roles", "Account & Roles", "account_roles", "\U0001f511")
nodes_compliance_notice_node = _notice_node("/nodes_compliance", "Nodes Compliance", "nodes_compliance", "\U0001f6e1")
