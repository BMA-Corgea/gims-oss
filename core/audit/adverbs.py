# core/audit/adverbs.py
# Adverb <-> Verb schema congruency auditing + adverb-field normalization.
# Split VERBATIM from core_audit.py (the R19 integrity auditor).

from __future__ import annotations
from typing import Any, Dict, List

from utils.logger import get_logger
log = get_logger(__name__)

from core.audit.findings import Finding, AdverbTypes, VerbTypes, NounTypes
from core.audit.checks import _dedupe


# -------------------------------
# Adverb ↔ Verb schema congruency
# -------------------------------

def audit_adverb_type_alignment(
    adverb_types: AdverbTypes,
    verb_types: VerbTypes,
    noun_types: NounTypes
) -> List[Finding]:
    log.debug("[audit_adverb_type_alignment] Start")
    findings: List[Finding] = []

    # Fast lookup for noun type existence
    known_nouns = set(noun_types.keys())
    log.debug("[audit_adverb_type_alignment] known_nouns:", known_nouns)

    for adv_name, adv_def in adverb_types.items():
        log.debug(f"[audit_adverb_type_alignment] Checking adverb='{adv_name}'")
        verb_name = adv_def.get("verb")
        if not verb_name or verb_name not in verb_types:
            log.debug(f"[audit_adverb_type_alignment] Unknown verb='{verb_name}' for adverb='{adv_name}'")
            findings.append(Finding(
                code="ADV_VERB_UNKNOWN",
                severity="error",
                where={"scope": "adverb_type", "adverb": adv_name},
                message=f"Adverb '{adv_name}' references unknown verb '{verb_name}'",
                details={"verb": verb_name}
            ))
            continue

        v = verb_types[verb_name]
        v_adv_schema = (v.get("adverb_schema") or {})
        if adv_name not in v_adv_schema:
            log.debug(f"[audit_adverb_type_alignment] Adverb '{adv_name}' missing from verb '{verb_name}' adverb_schema")
            findings.append(Finding(
                code="ADV_NOT_DECLARED_ON_VERB",
                severity="error",
                where={"scope": "adverb_type", "adverb": adv_name, "verb": verb_name},
                message=f"Adverb '{adv_name}' not declared under verb '{verb_name}' adverb_schema",
                details={}
            ))

        # Cross-check fields that matter
        left = _normalize_adverb_fields(adv_def)
        right = _normalize_adverb_fields(v_adv_schema.get(adv_name, {}))
        log.debug(f"[audit_adverb_type_alignment] Norm left={left}")
        log.debug(f"[audit_adverb_type_alignment] Norm right={right}")

        # Duplicate options inside each definition
        _, dups_l = _dedupe(left.get("valid_options_values", []))
        _, dups_r = _dedupe(right.get("valid_options_values", []))
        if dups_l:
            log.debug(f"[audit_adverb_type_alignment] Duplicate options (left): {sorted(set(dups_l))}")
            findings.append(Finding(
                code="ADV_OPTION_DUPLICATE",
                severity="warn",
                where={"scope": "adverb_type", "adverb": adv_name, "verb": verb_name, "side": "adverb_types"},
                message=f"Duplicate option values detected: {sorted(set(dups_l))}",
                details={"duplicates": sorted(set(dups_l))}
            ))
        if dups_r:
            log.debug(f"[audit_adverb_type_alignment] Duplicate options (right): {sorted(set(dups_r))}")
            findings.append(Finding(
                code="ADV_OPTION_DUPLICATE",
                severity="warn",
                where={"scope": "adverb_type", "adverb": adv_name, "verb": verb_name, "side": "verb_types"},
                message=f"Duplicate option values detected: {sorted(set(dups_r))}",
                details={"duplicates": sorted(set(dups_r))}
            ))

        # Field conflicts
        conflicts = {}
        for k in ("field_type", "required", "format"):
            lv, rv = left.get(k), right.get(k)
            if lv is not None and rv is not None and lv != rv:
                conflicts[k] = (lv, rv)
        if conflicts:
            log.debug(f"[audit_adverb_type_alignment] Conflicts: {conflicts}")
            findings.append(Finding(
                code="ADV_SCHEMA_CONFLICT",
                severity="error",
                where={"scope": "adverb_type", "adverb": adv_name, "verb": verb_name},
                message=f"Conflicting fields between adverb_types and verb_types: {conflicts}",
                details={"conflicts": conflicts}
            ))

        # Reference nouns must exist
        for rn in set(left.get("reference_nouns", []) + right.get("reference_nouns", [])):
            if rn not in known_nouns:
                log.debug(f"[audit_adverb_type_alignment] Unknown reference_noun target: {rn}")
                findings.append(Finding(
                    code="ADV_REF_TARGET_UNKNOWN",
                    severity="error",
                    where={"scope": "adverb_type", "adverb": adv_name, "verb": verb_name},
                    message=f"Reference target noun type '{rn}' does not exist",
                    details={"reference_noun": rn}
                ))

    log.debug("[audit_adverb_type_alignment] Done -> findings:", len(findings))
    return findings


def _normalize_adverb_fields(d: Dict[str, Any]) -> Dict[str, Any]:
    log.debug("[_normalize_adverb_fields] In:", d)
    # Gather possible forms
    valid_options = d.get("valid_options") or []
    values = []
    for item in valid_options:
        if isinstance(item, dict) and "value" in item:
            values.append(item["value"])
        elif isinstance(item, str):
            values.append(item)
    ref_nouns = []
    if "reference_nouns" in d and isinstance(d["reference_nouns"], list):
        ref_nouns.extend([str(x) for x in d["reference_nouns"]])
    if "reference_noun" in d and isinstance(d["reference_noun"], str):
        ref_nouns.append(d["reference_noun"])
    out = {
        "field_type": d.get("field_type"),
        "required": d.get("required"),
        "format": d.get("format"),
        "valid_options_values": values,
        "reference_nouns": ref_nouns,
    }
    log.debug("[_normalize_adverb_fields] Out:", out)
    return out
