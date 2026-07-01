# modules/adjective_editor_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.adjective_editor import adjective_editor_node
from nodes.compliance.adjective_editor import compliance_adjective_editor_node

adjective_editor_module = make_standard_module(
    name='Adjective Editor',
    route='/adjective_editor',
    page_node=adjective_editor_node,
    compliance_node=compliance_adjective_editor_node,
    description='UI node that serves gui/components/adjective_editor.html',
)
