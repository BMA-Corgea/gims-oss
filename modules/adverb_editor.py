# modules/adverb_editor_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.adverb_editor import adverb_editor_node
from nodes.compliance.adverb_editor import compliance_adverb_editor_node

adverb_editor_module = make_standard_module(
    name='Adverb Editor',
    route='/adverb_editor',
    page_node=adverb_editor_node,
    compliance_node=compliance_adverb_editor_node,
    description='UI node that serves gui/components/adverb.html',
)
