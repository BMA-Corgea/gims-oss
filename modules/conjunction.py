# modules/conjunction_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.conjunction import conjunction_node
from nodes.compliance.conjunction import compliance_conjunction_node

conjunction_module = make_standard_module(
    name='Conjunction Editor',
    route='/conjunction_editor',
    page_node=conjunction_node,
    compliance_node=compliance_conjunction_node,
    description='UI node that serves gui/components/conjunction.html',
)
