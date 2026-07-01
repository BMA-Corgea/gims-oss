# modules/noun_configure_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.noun_configure import noun_configure_node
from nodes.compliance.noun_configure import compliance_noun_configure_node

noun_configure_module = make_standard_module(
    name='Noun Configure',
    route='/noun_configure',
    page_node=noun_configure_node,
    compliance_node=compliance_noun_configure_node,
    description='UI node that serves gui/components/noun_configure.html',
)
