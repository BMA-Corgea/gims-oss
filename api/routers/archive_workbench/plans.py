"""Plan -> JSON serialization (split verbatim from archive_workbench.py)."""
from __future__ import annotations
from typing import Any, Dict

from utils.logger import get_logger
log = get_logger(__name__)

from core.archive_workbench import (
    Plan, PlanStep, EnsureSoftColumns, EnsureArchiveTable, SQLStep, FileOp,
)


def _serialize_plan(plan: Plan) -> Dict[str, Any]:
    def step_to_dict(s: PlanStep) -> Dict[str, Any]:
        if isinstance(s, EnsureSoftColumns):
            return {"type": "EnsureSoftColumns", "target": s.target, "table": s.table}
        if isinstance(s, EnsureArchiveTable):
            return {
                "type": "EnsureArchiveTable",
                "source_target": s.source_target,
                "source_table": s.source_table,
                "dest_target": s.dest_target,
                "dest_table": s.dest_table,
                "columns": s.columns,
                "include_meta": s.include_meta,
            }
        if isinstance(s, SQLStep):
            return {"type": "SQLStep", "target": s.target, "sql": s.sql, "params": list(s.params)}
        if isinstance(s, FileOp):
            return {"type": "FileOp", "op": s.op, "src": s.src, "dst": s.dst, "text": s.text}
        return {"type": type(s).__name__, "repr": repr(s)}
    out = {"description": plan.description, "meta": plan.meta, "steps": [step_to_dict(s) for s in plan.steps]}
    log.debug("[_serialize_plan] steps:", len(out["steps"]))
    return out
