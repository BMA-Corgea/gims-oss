# OPEN-CORE STUB — auto-written by tools/oss_export. The GIMS public build ships without
# the login/auth, roles, and 21 CFR Part 11 compliance layers; this file preserves the
# import surface the rest of the app needs, implemented as an OPEN single-user no-op.
# Do NOT deploy the open-core build as a shared/multi-user service.
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.open_core_notice import nodes_compliance_notice_node

nodes_compliance_module = make_standard_module(
    name="Nodes Compliance",
    route="/nodes_compliance",
    page_node=nodes_compliance_notice_node,
    description="open-core: 21 CFR Part 11 compliance is not included",
)
