"""Factory for the standard UI modules (R21).

Each ``modules/*.py`` repeated an identical ``Module(...)`` wiring its page node together with the
same shared infra nodes (login + orchestrated-fetch + login-rules + state-dock), an optional
two-node compliance pack, and an optional reason/sign node. :func:`make_standard_module` captures
that one composition so a module is declared in a few lines.

(``launcher_module`` is intentionally NOT built here — it composes a different node set:
auth-guard + auto-backup + dual-dataentry.)
"""
from __future__ import annotations

from typing import Optional, Sequence

from core.orchestration.module import Module
from core.orchestration.node import Node
from nodes.login_fastapi_users_node import login_node
from nodes.orchestrated_fetch_node import orchestrated_fetch_node
from nodes.login_rules_node import login_rules_node
from nodes.state_dock_node import state_dock_node
from nodes.compliance.compliance_node import compliance_node as _base_compliance_node
from nodes.compliance.reason_sign_node import reason_sign_node


def make_standard_module(
    *,
    name: str,
    route: str,
    page_node: Node,
    compliance_node: Optional[Node] = None,   # the module-specific compliance trigger node
    reason_sign: bool = False,
    login_rules: bool = True,
    inject_scripts: Sequence[str] = ("/orchestrate/inject.js",),
    inject_stylesheets: Sequence[str] = (),
    description: str = "",
    version: str = "0.1.0",
    roles: Optional[set] = None,
) -> Module:
    """Compose a standard UI module: the page node + shared infra (+ optional compliance / reason-sign)."""
    nodes: list[Node] = [page_node, login_node, orchestrated_fetch_node, state_dock_node]
    if login_rules:
        nodes.append(login_rules_node)
    if compliance_node is not None:
        nodes += [_base_compliance_node, compliance_node]
    if reason_sign:
        nodes.append(reason_sign_node)
    return Module(
        name=name,
        nodes=nodes,
        version=version,
        description=description,
        roles=roles if roles is not None else set(),
        inject={route: {"scripts": list(inject_scripts), "stylesheets": list(inject_stylesheets)}},
    )
