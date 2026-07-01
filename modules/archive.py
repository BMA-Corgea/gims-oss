# modules/archive_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.archive import archive_node
from nodes.compliance.archive import compliance_archive_node

archive_module = make_standard_module(
    name='Archive',
    route='/archive-workbench',
    page_node=archive_node,
    compliance_node=compliance_archive_node,
    reason_sign=True,
    description='Archive Workbench with login, fetch, RBAC, state dock, and compliance logging',
    inject_scripts=[
                "orchestrate/inject.js",
            ],
)
