# modules/deep_search_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.deep_search import deep_search_node

deep_search_module = make_standard_module(
    name='Deep Search',
    route='/deep_search',
    page_node=deep_search_node,
    description='UI node that serves gui/components/deep_search.html',
)
