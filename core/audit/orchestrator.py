# core/audit/orchestrator.py
# Orchestration entrypoint (audit_all) + minimal self-test smoke block.
# Split VERBATIM from core_audit.py (the R19 integrity auditor).

from __future__ import annotations
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from utils.logger import get_logger
log = get_logger(__name__)

from core.audit.findings import (
    Finding,
    summarize_findings,
    NounTypes,
    VerbTypes,
    AdverbTypes,
    AdjectiveTypes,
    NounInstancesByType,
    RunEntriesByGroup,
    NounIndex,
    OverrideIndex,
)
from core.audit.nouns import audit_noun_instances
from core.audit.adverbs import audit_adverb_type_alignment
from core.audit.adjectives import audit_adjective_alignment
from core.audit.runs import audit_run_entries


# -------------------------------
# Orchestration entrypoints
# -------------------------------

def audit_all(
    *,
    noun_types: NounTypes,
    verb_types: VerbTypes,
    adverb_types: AdverbTypes,
    adjective_types: AdjectiveTypes,
    noun_instances_by_type: NounInstancesByType,
    run_entries_by_group: RunEntriesByGroup,
    noun_index: Optional[NounIndex] = None,
    override_index: Optional[OverrideIndex] = None,
    engine_validation: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Perform all instance-level audits (no filesystem, no interpretation).
    Returns a dict with `findings` (list of Finding as dict) and `summary`.

    ``engine_validation`` routes noun-instance checks through the one validation engine
    (default: the ``GIMS_AUDIT_ENGINE`` config flag). See ``utils.config.audit_engine``.
    """
    log.debug("[audit_all] Start")
    # Build noun_index if not provided
    if noun_index is None:
        log.debug("[audit_all] Building noun_index from noun_instances_by_type")
        noun_index = {
            k: {
                inst.get(noun_types.get(k, {}).get("primary_id_field"))
                for inst in v
                if inst.get(noun_types.get(k, {}).get("primary_id_field"))
            }
            for k, v in noun_instances_by_type.items()
        }
    log.debug("[audit_all] noun_index keys:", {k: len(v) for k, v in noun_index.items()})
    override_index = override_index or {}
    log.debug("[audit_all] override_index keys:", list(override_index.keys()))

    findings: List[Finding] = []

    # Nouns
    n_findings = audit_noun_instances(
        noun_types, noun_instances_by_type, noun_index,
        adjective_types=adjective_types, engine_validation=engine_validation,
    )
    findings += n_findings
    log.debug("[audit_all] Noun findings:", len(n_findings))

    # Adverbs vs Verbs
    adv_type_findings = audit_adverb_type_alignment(adverb_types, verb_types, noun_types)
    findings += adv_type_findings
    log.debug("[audit_all] Adverb-type findings:", len(adv_type_findings))

    # Adjectives vs Nouns
    adj_findings = audit_adjective_alignment(noun_types, adjective_types)
    findings += adj_findings
    log.debug("[audit_all] Adjective findings:", len(adj_findings))

    # Runs
    run_findings = audit_run_entries(run_entries_by_group, verb_types, noun_types, adverb_types, noun_index, override_index)
    findings += run_findings
    log.debug("[audit_all] Run findings:", len(run_findings))

    # Convert to serializable output
    as_dicts = [asdict(f) for f in findings]
    summary = summarize_findings(findings)
    log.debug("[audit_all] Done. Total findings:", len(as_dicts))
    return {
        "findings": as_dicts,
        "summary": summary,
    }


# -------------------------------
# Minimal self-test (optional)
# -------------------------------

if __name__ == "__main__":
    log.debug("[__main__] Running smoke test")
    noun_types = {
        "Sample": {
            "primary_id_field": "SampleID",
            "required_fields": ["SampleID", "Matrix"],
            "field_types": {"SampleID": "string", "Matrix": "string"},
            "adjective_fields": {
                "Parent": {"adjective_class": "Reference", "reference_nouns": ["Sample"]}
            },
            "autogenerate_segments": [
                {"type": "date", "format": "YYYYMMDD"},
                {"type": "static", "value": "-"},
                {"type": "number", "width": 3}
            ],
        }
    }
    noun_instances_by_type = {
        "Sample": [
            {"SampleID": "20250101-001", "Matrix": "Plant", "_runID": "R1"},
            {"SampleID": "bad-format", "Matrix": "Plant", "_runID": "R2", "Parent": "20250101-001"},
        ]
    }
    noun_index = {"Sample": {"20250101-001"}}

    verb_types = {
        "Test": {
            "verb_name": "Test",
            "verb_group": "General",
            "data_entry_schema": {"required_headers": ["Instrument"], "types": {"Instrument": "string"}},
            "adverb_schema": {
                "tomato": {"valid_options": [{"value": "q", "display_in_label": True}],
                           "required": True,
                           "field_type": "string"},
            }
        }
    }
    adverb_types = {
        "tomato": {"adverb": "tomato", "verb": "Test",
                   "valid_options": [{"value": "q", "display_in_label": True}, {"value": "q"}],
                   "adverb_class": "Tag"}
    }
    adjective_types = {
        "Reference": {"adjective_class": "reference"}  # simplistic placeholder
    }
    run_entries_by_group = {
        "General": [
            {"_runID": "R1", "verb": "Test",
             "adverbs": {"tomato": "q"},
             "data_entry": {"Instrument": "HPLC-03"}},
            {"_runID": "R1", "verb": "Test",  # duplicate without override
             "adverbs": {"tomato": "x"},
             "data_entry": {"Instrument": "HPLC-03"}}
        ]
    }
    override_index = {}  # no aliases, so duplicate should error

    result = audit_all(
        noun_types=noun_types,
        verb_types=verb_types,
        adverb_types=adverb_types,
        adjective_types=adjective_types,
        noun_instances_by_type=noun_instances_by_type,
        run_entries_by_group=run_entries_by_group,
        noun_index=noun_index,
        override_index=override_index,
    )
    from pprint import pprint
    pprint(result["summary"])
    for f in result["findings"]:
        pprint(f)
