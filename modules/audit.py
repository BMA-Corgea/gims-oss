# modules/audit_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.audit import audit_node

audit_module = make_standard_module(
    name='Audit',
    route='/audit',
    page_node=audit_node,
    description='UI node that serves gui/components/audit.html',
)
