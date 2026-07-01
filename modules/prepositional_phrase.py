# modules/prepositional_phrase_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.prepositional_phrase import prepositional_phrase_node
from nodes.compliance.prepositional_phrase import compliance_prepositional_phrase_node

prepositional_phrase_module = make_standard_module(
    name='Prepositional Phrase Runner',
    route='/prepositional_phrase_runner',
    page_node=prepositional_phrase_node,
    compliance_node=compliance_prepositional_phrase_node,
    reason_sign=True,
    description='UI node that serves gui/components/prepositional_phrase_runner.html',
)
