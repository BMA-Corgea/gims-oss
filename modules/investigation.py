# modules/investigation_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.investigation import investigation_node

investigation_module = make_standard_module(
    name='Investigation',
    route='/investigation',
    page_node=investigation_node,
    description='UI node that serves gui/components/investigation.html',
)
