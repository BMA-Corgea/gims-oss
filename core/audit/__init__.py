# core/audit/__init__.py
# Pure logic audit engine for GIMS instances, verbs, adverbs, adjectives.
# No file I/O. Feed it already-loaded structures from your API layer.
#
# Role-named package split (wiring-neutral) of the former core/core_audit.py:
#   findings      - Severity, Finding, type aliases, summarize_findings
#   checks        - _is_type, _dedupe, _DATE_TOKEN_MAP, compile_autogen_regex
#   nouns         - audit_noun_instances (+ _audit_adj_lookup, _engine_finding)
#   adverbs       - audit_adverb_type_alignment (+ _normalize_adverb_fields)
#   runs          - audit_run_entries (+ _group_duplicates, _duplicates_covered_by_overrides)
#   adjectives    - audit_adjective_alignment
#   orchestrator  - audit_all (+ __main__ smoke test)

from __future__ import annotations

# Preserve the original import-time side effect (was core_audit.py line 17).
from utils.logger import get_logger
log = get_logger(__name__)
log.debug("[core_audit_instances] Module loaded")

from core.audit.findings import (
    Severity,
    Finding,
    summarize_findings,
    NounTypes,
    VerbTypes,
    AdverbTypes,
    AdjectiveTypes,
    NounInstancesByType,
    RunEntriesByGroup,
    OverrideIndex,
    NounIndex,
)
from core.audit.checks import compile_autogen_regex
from core.audit.nouns import audit_noun_instances
from core.audit.adverbs import audit_adverb_type_alignment
from core.audit.runs import audit_run_entries
from core.audit.adjectives import audit_adjective_alignment
from core.audit.orchestrator import audit_all

__all__ = [
    "audit_all",
    "audit_noun_instances",
    "Finding",
    "Severity",
    "summarize_findings",
    "compile_autogen_regex",
    "audit_adverb_type_alignment",
    "audit_run_entries",
    "audit_adjective_alignment",
    "NounTypes",
    "VerbTypes",
    "AdverbTypes",
    "AdjectiveTypes",
    "NounInstancesByType",
    "RunEntriesByGroup",
    "OverrideIndex",
    "NounIndex",
]
