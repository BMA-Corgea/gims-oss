# OPEN-CORE STUB — auto-written by tools/oss_export. The GIMS public build ships without
# the login/auth, roles, and 21 CFR Part 11 compliance layers; this file preserves the
# import surface the rest of the app needs, implemented as an OPEN single-user no-op.
# Do NOT deploy the open-core build as a shared/multi-user service.
from __future__ import annotations

from fastapi import APIRouter

from core.orchestration.node import Node, NodeKind

# Inert compliance trigger — the open build records no audit/compliance trail.
compliance_runlog_workbench_node = Node(
    name="Compliance: runlog_workbench (disabled)",
    kind=NodeKind.INFRASTRUCTURE,
    router=APIRouter(),
    meta={"open_core": True},
)
