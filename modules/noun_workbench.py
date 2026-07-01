# modules/noun_workbench_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.noun_workbench import noun_workbench_node
from nodes.compliance.noun_workbench import compliance_noun_workbench_node

noun_workbench_module = make_standard_module(
    name='Noun Workbench',
    route='/noun_workbench',
    page_node=noun_workbench_node,
    compliance_node=compliance_noun_workbench_node,
    description='UI node that serves gui/components/noun_workbench.html',
)
