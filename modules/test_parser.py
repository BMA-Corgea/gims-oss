# modules/test_parser.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.test_parser import test_parser_node

test_parser_module = make_standard_module(
    name='Test Parser',
    route='/test_parser',
    page_node=test_parser_node,
    description='Custom-parser / prepositional-phrase test harness.',
)
