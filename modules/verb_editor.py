# modules/verb_editor_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.verb_editor import verb_editor_node
from nodes.compliance.verb_editor import compliance_verb_editor_node

verb_editor_module = make_standard_module(
    name='Verb Editor',
    route='/verb_editor',
    page_node=verb_editor_node,
    compliance_node=compliance_verb_editor_node,
    description='UI node that serves gui/components/verb_editor.html',
)
