# core/audit/runs.py
# Per-run validation (verbs/adverbs/data entry) + duplicate-runID override coverage.
# Split VERBATIM from core_audit.py (the R19 integrity auditor).

from __future__ import annotations
from typing import Dict, Iterable, List, Optional, Set, Tuple
import collections

from utils.logger import get_logger
log = get_logger(__name__)

from core.audit.findings import (
    Finding,
    RunEntriesByGroup,
    VerbTypes,
    NounTypes,
    AdverbTypes,
    NounIndex,
    OverrideIndex,
)
from core.audit.checks import _is_type
from core.audit.adverbs import _normalize_adverb_fields


# -------------------------------
# Per-run validation (verbs/adverbs/data entry)
# -------------------------------

def audit_run_entries(
    run_entries_by_group: RunEntriesByGroup,
    verb_types: VerbTypes,
    noun_types: NounTypes,
    adverb_types: AdverbTypes,
    noun_index: NounIndex,
    override_index: Optional[OverrideIndex] = None,
) -> List[Finding]:
    log.debug("[audit_run_entries] Start")
    findings: List[Finding] = []
    override_index = override_index or {}

    # Build fast maps
    verb_by_group: Dict[str, Set[str]] = collections.defaultdict(set)
    for vname, vs in verb_types.items():
        grp = vs.get("verb_group")
        if grp:
            verb_by_group[grp].add(vname)
    log.debug("[audit_run_entries] verb_by_group:", {k: sorted(list(v)) for k, v in verb_by_group.items()})

    # Duplicate runID detection with override-awareness
    all_runs: List[Tuple[str, str]] = []  # (runID, group)
    for group, runs in run_entries_by_group.items():
        for r in runs:
            rid = r.get("_runID")
            if rid:
                all_runs.append((rid, group))
    log.debug("[audit_run_entries] total runs counted:", len(all_runs))

    dup_map = _group_duplicates([rid for rid, _ in all_runs])
    log.debug("[audit_run_entries] dup_map keys:", list(dup_map.keys()))

    for runid, positions in dup_map.items():
        log.debug(f"[audit_run_entries] Checking duplicates runid={runid} occurrences={len(positions)}")
        if len(positions) <= 1:
            continue
        covered = _duplicates_covered_by_overrides(runid, positions, override_index)
        log.debug(f"[audit_run_entries] override covered? {covered} for runid={runid}")
        if not covered:
            findings.append(Finding(
                code="V_RUNID_DUP_NO_OVERRIDE",
                severity="error",
                where={"scope": "run", "_runID": runid},
                message=f"Duplicate _runID '{runid}' without explicit override/alias",
                details={"count": len(positions)}
            ))

    # Validate each run
    for group, runs in run_entries_by_group.items():
        allowed_verbs = verb_by_group.get(group, set())
        log.debug(f"[audit_run_entries] group={group} runs={len(runs)} allowed_verbs={sorted(list(allowed_verbs))}")
        for r in runs:
            rid = r.get("_runID")
            verb_name = r.get("verb")
            log.debug(f"[audit_run_entries] run _runID={rid} verb={verb_name}")

            if not verb_name or verb_name not in verb_types:
                log.debug(f"[audit_run_entries] Unknown verb for run _runID={rid}")
                findings.append(Finding(
                    code="V_VERB_UNKNOWN",
                    severity="error",
                    where={"scope": "run", "group": group, "_runID": rid},
                    message=f"Unknown verb '{verb_name}' on run",
                    details={"verb": verb_name}
                ))
                continue

            vdef = verb_types[verb_name]
            v_group = vdef.get("verb_group")

            # Group alignment
            if v_group != group:
                log.debug(f"[audit_run_entries] Group mismatch _runID={rid} in={group} declared={v_group}")
                findings.append(Finding(
                    code="V_GROUP_MISMATCH",
                    severity="error",
                    where={"scope": "run", "group": group, "_runID": rid, "verb": verb_name},
                    message=f"Run is in group '{group}' but verb declares '{v_group}'",
                    details={"declared_group": v_group}
                ))

            # DataEntry schema shape (if verb defines one)
            de_schema = vdef.get("data_entry_schema") or {}
            required_headers = list(de_schema.get("required_headers", []))
            header_types: Dict[str, str] = de_schema.get("types", {}) or {}

            data_entry = r.get("data_entry")
            if required_headers or header_types:
                if not isinstance(data_entry, dict):
                    log.debug(f"[audit_run_entries] DataEntry missing for _runID={rid} verb={verb_name}")
                    findings.append(Finding(
                        code="V_DATAENTRY_MISSING",
                        severity="error",
                        where={"scope": "run", "_runID": rid, "verb": verb_name},
                        message=f"DataEntry is required by verb '{verb_name}' but missing or not an object",
                        details={}
                    ))
                else:
                    # Required headers
                    missing = [h for h in required_headers if h not in data_entry]
                    if missing:
                        log.debug(f"[audit_run_entries] DataEntry missing headers _runID={rid} missing={missing}")
                        findings.append(Finding(
                            code="V_DATAENTRY_SCHEMA_MISMATCH",
                            severity="error",
                            where={"scope": "run", "_runID": rid, "verb": verb_name},
                            message=f"DataEntry missing required headers: {missing}",
                            details={"missing": missing}
                        ))
                    # Type checks
                    for h, t in header_types.items():
                        if h in data_entry and data_entry[h] is not None and not _is_type(data_entry[h], t):
                            log.debug(f"[audit_run_entries] DataEntry type mismatch _runID={rid} field={h} expected={t} value={data_entry[h]}")
                            findings.append(Finding(
                                code="V_DATAENTRY_SCHEMA_MISMATCH",
                                severity="error",
                                where={"scope": "run", "_runID": rid, "verb": verb_name, "field": h},
                                message=f"DataEntry field '{h}' wrong type (expected {t})",
                                details={"expected": t, "value": data_entry[h]}
                            ))

            # Per-run adverbs validation (against verb's adverb_schema)
            r_adverbs = r.get("adverbs") or {}
            v_adv_schema = vdef.get("adverb_schema") or {}
            log.debug(f"[audit_run_entries] _runID={rid} adverbs provided={list(r_adverbs.keys())} schema keys={list(v_adv_schema.keys())}")

            # Required adverbs
            required_adv = [name for name, meta in v_adv_schema.items() if meta.get("required") is True]
            missing_adv = [a for a in required_adv if a not in r_adverbs]
            if missing_adv:
                log.debug(f"[audit_run_entries] Missing required adverbs _runID={rid} missing={missing_adv}")
                findings.append(Finding(
                    code="ADV_REQUIRED_MISSING_ON_RUN",
                    severity="error",
                    where={"scope": "run", "_runID": rid, "verb": verb_name},
                    message=f"Missing required adverbs: {missing_adv}",
                    details={"missing": missing_adv}
                ))

            # Provided adverbs must be known for this verb and pass field checks
            for adv_name, adv_value in r_adverbs.items():
                if adv_name not in v_adv_schema:
                    log.debug(f"[audit_run_entries] Unknown adverb for verb _runID={rid} adverb={adv_name} verb={verb_name}")
                    findings.append(Finding(
                        code="ADV_NOT_DECLARED_ON_VERB",
                        severity="error",
                        where={"scope": "run", "_runID": rid, "verb": verb_name, "adverb": adv_name},
                        message=f"Adverb '{adv_name}' not allowed for verb '{verb_name}'",
                        details={}
                    ))
                    continue

                adv_meta = _normalize_adverb_fields(v_adv_schema[adv_name])

                # Type
                ftype = adv_meta.get("field_type")
                if ftype:
                    if not _is_type(adv_value, ftype):
                        log.debug(f"[audit_run_entries] Adverb type mismatch _runID={rid} adverb={adv_name} expected={ftype} value={adv_value}")
                        findings.append(Finding(
                            code="ADV_TYPE_MISMATCH_ON_RUN",
                            severity="error",
                            where={"scope": "run", "_runID": rid, "verb": verb_name, "adverb": adv_name},
                            message=f"Adverb '{adv_name}' wrong type (expected {ftype})",
                            details={"expected": ftype, "value": adv_value}
                        ))

                # Options (if present)
                opts = adv_meta.get("valid_options_values", [])
                if opts:
                    if isinstance(adv_value, list):
                        # Filter out blanks when checking invalids
                        invalid = [v for v in adv_value if v not in opts and v not in (None, "")]
                    else:
                        if adv_value in (None, ""):
                            invalid = []
                        else:
                            invalid = [] if adv_value in opts else [adv_value]

                    if invalid:
                        findings.append(Finding(
                            code="ADV_OPTION_INVALID",
                            severity="error",
                            where={"scope": "run", "_runID": rid, "verb": verb_name, "adverb": adv_name},
                            message=f"Adverb value(s) not in valid options: {invalid}",
                            details={"invalid": invalid, "valid": opts}
                        ))

                # Reference resolution (if reference_noun(s))
                adv_class = (adverb_types.get(adv_name, {}).get("adverb_class") or "").lower()
                ref_nouns = adv_meta.get("reference_nouns", [])
                if ref_nouns:
                    # Normalize values based on adverb_class
                    if adv_class == "referencelist":
                        raw_values = adv_value if isinstance(adv_value, list) else [adv_value]
                    elif adv_class == "reference":
                        raw_values = [adv_value] if adv_value is not None else []
                    else:
                        raw_values = []

                    # Flatten nested lists
                    values = []
                    for rv in raw_values:
                        if isinstance(rv, (list, tuple, set)):
                            values.extend(rv)
                        else:
                            values.append(rv)

                    # Now check each scalar
                    for v in values:
                        # Skip empty / unset values
                        if v in (None, "", []):
                            continue
                        if not isinstance(v, (str, int, float)):
                            continue
                        ok = any(v in (noun_index.get(nt) or set()) for nt in ref_nouns)
                        if not ok:
                            findings.append(Finding(
                                code="ADV_REF_INVALID_ON_RUN",
                                severity="error",
                                where={"scope": "run", "_runID": rid, "verb": verb_name, "adverb": adv_name},
                                message=f"Adverb reference '{v}' not found in allowed noun types {ref_nouns}",
                                details={"value": v, "allowed_types": ref_nouns}
                            ))

    log.debug("[audit_run_entries] Done -> findings:", len(findings))
    return findings


def _group_duplicates(ids: Iterable[str]) -> Dict[str, List[int]]:
    """Return id -> list of positions (indices). Positions are only used for counting here."""
    log.debug("[_group_duplicates] Start")
    positions: Dict[str, List[int]] = {}
    for idx, rid in enumerate(ids):
        positions.setdefault(rid, []).append(idx)
    result = {k: v for k, v in positions.items() if len(v) > 1}
    log.debug("[_group_duplicates] Result keys:", list(result.keys()))
    return result


def _duplicates_covered_by_overrides(runid: str, positions: List[int], override_index: OverrideIndex) -> bool:
    """
    Decide if duplicate runIDs are explicitly allowed via overrides/aliases.
    Strategy:
    - If override_index contains runid -> aliases where size >= len(positions)-1, consider covered.
    - Or if any alias key maps to this runid similarly.
    """
    log.debug(f"[_duplicates_covered_by_overrides] runid={runid} occurrences={len(positions)}")
    needed = len(positions) - 1
    if needed <= 0:
        log.debug("[_duplicates_covered_by_overrides] needed<=0 -> True")
        return True
    aliases = override_index.get(runid, set())
    log.debug(f"[_duplicates_covered_by_overrides] aliases for {runid} -> {aliases} needed={needed}")
    if len(aliases) >= needed:
        log.debug("[_duplicates_covered_by_overrides] Covered by direct aliases")
        return True
    # Check reciprocal keys that alias to this runid
    back_refs = [k for k, vs in override_index.items() if runid in vs]
    log.debug(f"[_duplicates_covered_by_overrides] back_refs -> {back_refs}")
    for k in back_refs:
        if len(override_index.get(k, set())) >= needed:
            log.debug("[_duplicates_covered_by_overrides] Covered by reciprocal alias")
            return True
    log.debug("[_duplicates_covered_by_overrides] Not covered")
    return False
