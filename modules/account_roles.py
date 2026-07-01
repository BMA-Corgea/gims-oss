# OPEN-CORE STUB — auto-written by tools/oss_export. The GIMS public build ships without
# the login/auth, roles, and 21 CFR Part 11 compliance layers; this file preserves the
# import surface the rest of the app needs, implemented as an OPEN single-user no-op.
# Do NOT deploy the open-core build as a shared/multi-user service.
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.open_core_notice import account_roles_notice_node

account_roles_module = make_standard_module(
    name="Account Roles",
    route="/account_roles",
    page_node=account_roles_notice_node,
    description="open-core: accounts & roles are not included (single-user build)",
)
