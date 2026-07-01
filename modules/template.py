# modules/template_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.template import template_node

template_module = make_standard_module(
    name='Template',
    route='/template',
    page_node=template_node,
    description='UI node that serves gui/components/template.html',
)
