from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.inspect import inspect_node

inspect_module = make_standard_module(
    name='Inspect',
    route='/inspect',
    page_node=inspect_node,
    description='UI node that serves gui/components/inspect.html (read-only instance viewer)',
)
