# modules/verb_workbench_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.verb_workbench import verb_workbench_node
from nodes.compliance.verb_workbench import compliance_verb_workbench_node

verb_workbench_module = make_standard_module(
    name='Verb Workbench',
    route='/verb_workbench',
    page_node=verb_workbench_node,
    compliance_node=compliance_verb_workbench_node,
    description='UI node that serves gui/components/verb_workbench.html',
)
