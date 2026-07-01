# modules/tutorial.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.tutorial import tutorial_node

tutorial_module = make_standard_module(
    name='Tutorial',
    route='/tutorial',
    page_node=tutorial_node,
    description='Onboarding / how-GIMS-works explainer page.',
)
