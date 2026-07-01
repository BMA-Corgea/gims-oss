# OPEN-CORE STUB — auto-written by tools/oss_export. The GIMS public build ships without
# the login/auth, roles, and 21 CFR Part 11 compliance layers; this file preserves the
# import surface the rest of the app needs, implemented as an OPEN single-user no-op.
# Do NOT deploy the open-core build as a shared/multi-user service.
from __future__ import annotations

from fastapi import APIRouter

from core.orchestration.node import Node, NodeKind

# Inert: module pages are ungated in the open single-user build.
router = APIRouter()

auth_guard_node = Node(
    name="Rules: Auth Guard (Modules)",
    kind=NodeKind.RULES,
    router=router,
    meta={"enforces": [], "open_core": True},
)
