# core/audit/findings.py
# Public datatypes, type aliases (shape contracts), and finding summarization.
# Split VERBATIM from core_audit.py (the R19 integrity auditor).

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Set
import collections

from utils.logger import get_logger
log = get_logger(__name__)

# -------------------------------
# Public datatypes
# -------------------------------

Severity = str  # "error" | "warn" | "info"

@dataclass
class Finding:
    code: str                 # e.g., "N_REQUIRED_MISSING"
    severity: Severity        # "error" | "warn" | "info"
    where: Dict[str, Any]     # narrow, machine-friendly locator
    message: str              # human readable
    details: Dict[str, Any]   # extra structured context

# -------------------------------
# Type aliases (shape contracts)
# -------------------------------

# You can keep these loose (Dict[str, Any]) to avoid over-fitting to evolving schemas.
NounTypes      = Dict[str, Dict[str, Any]]     # noun_type_name -> schema dict
VerbTypes      = Dict[str, Dict[str, Any]]     # verb_name -> verb schema (incl. adverb_schema)
AdverbTypes    = Dict[str, Dict[str, Any]]     # adverb_name -> adverb type definition (with .verb, etc.)
AdjectiveTypes = Dict[str, Dict[str, Any]]     # adjective_name -> definition (class, etc.)

# Instances:
# noun_instances_by_type: { noun_type: [ {field: value, "_runID": "...", "<primary_id_field>": "..."} ] }
NounInstancesByType = Dict[str, List[Dict[str, Any]]]

# run_entries_by_group: { verb_group: [ { "_runID": str, "verb": str, "adverbs": {...}, "data_entry": {...}, ... } ] }
RunEntriesByGroup = Dict[str, List[Dict[str, Any]]]

# override_index: runID -> set of other runIDs that explicitly alias/override it
OverrideIndex = Dict[str, Set[str]]

# For noun reference resolution, we’ll use a simple index:
# noun_index: { noun_type: set(primary_ids) }
NounIndex = Dict[str, Set[str]]

# data_entry_schemas: { verb_name: { "required_headers": [...], "types": { header: "string|number|bool|date" } } }
# (If you store this inside VerbTypes["data_entry_schema"], pass it through as-is and we’ll read it there.)


# -------------------------------
# Helpers: summarization
# -------------------------------

def summarize_findings(findings: Iterable[Finding]) -> Dict[str, Any]:
    log.debug("[summarize_findings] Start")
    counts = collections.Counter()
    by_code = collections.Counter()
    total = 0
    for f in findings:
        total += 1
        counts[f.severity] += 1
        by_code[f.code] += 1
    summary = {
        "errors": counts.get("error", 0),
        "warnings": counts.get("warn", 0),
        "infos": counts.get("info", 0),
        "by_code": dict(by_code),
        "total": total,
    }
    log.debug("[summarize_findings] Done ->", summary)
    return summary
