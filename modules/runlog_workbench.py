# modules/runlog_workbench_module.py
from __future__ import annotations

from core.orchestration.standard_module import make_standard_module
from nodes.pages.runlog_workbench import runlog_workbench_node
from nodes.compliance.runlog_workbench import compliance_runlog_workbench_node

runlog_workbench_module = make_standard_module(
    name='Runlog Workbench',
    # Must match the node's served route (/runlog_workbench) — the inject map is keyed by
    # route, and the old '/runlog-workbench' (hyphen) never matched, so this page silently
    # got NO login/orchestrate/state-dock injection. Route-neutral fix (inject map is internal).
    route='/runlog_workbench',
    page_node=runlog_workbench_node,
    compliance_node=compliance_runlog_workbench_node,
    reason_sign=True,
    description='Inspect and edit run entries: statuses, overrides, attachments, and linked instances.',
    inject_scripts=[
                "/orchestrate/inject.js",
            ],
)
