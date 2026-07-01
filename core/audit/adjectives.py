# core/audit/adjectives.py
# Adjective <-> Noun type alignment (schema level).
# Split VERBATIM from core_audit.py (the R19 integrity auditor).

from __future__ import annotations
from typing import Any, Dict, List

from utils.logger import get_logger
log = get_logger(__name__)

from core.audit.findings import Finding, NounTypes, AdjectiveTypes


# -------------------------------
# Adjective ↔ Noun type alignment (schema level)
# -------------------------------

def audit_adjective_alignment(
    noun_types: NounTypes,
    adjective_types: AdjectiveTypes
) -> List[Finding]:
    log.debug("[audit_adjective_alignment] Start")
    findings: List[Finding] = []

    # Build a lookup of adjective name -> class
    adj_classes = {name: (d.get("adjective_class") or d.get("class") or "").lower()
                   for name, d in adjective_types.items()}
    log.debug("[audit_adjective_alignment] adj_classes:", adj_classes)

    for nt_name, nt_schema in noun_types.items():
        adj_fields: Dict[str, Dict[str, Any]] = nt_schema.get("adjective_fields", {}) or {}
        log.debug(f"[audit_adjective_alignment] noun_type={nt_name} fields={list(adj_fields.keys())}")
        for field, cfg in adj_fields.items():
            declared_class = (cfg.get("adjective_class") or cfg.get("class") or "").lower()
            adj_name = cfg.get("adjective") or field  # if you store by name, keep this flexible
            if adj_name in adjective_types:
                real_class = adj_classes.get(adj_name, "")
                if declared_class and real_class and declared_class != real_class:
                    log.debug(f"[audit_adjective_alignment] Class mismatch noun_type={nt_name} field={field} declared={declared_class} actual={real_class}")
                    findings.append(Finding(
                        code="ADJ_CLASS_MISMATCH",
                        severity="error",
                        where={"scope": "noun_type", "noun_type": nt_name, "field": field, "adjective": adj_name},
                        message=f"Adjective class mismatch: {declared_class} vs {real_class}",
                        details={"declared": declared_class, "actual": real_class}
                    ))
            else:
                log.debug(f"[audit_adjective_alignment] Unknown adjective type noun_type={nt_name} field={field} adj={adj_name}")
                findings.append(Finding(
                    code="ADJ_TYPE_UNKNOWN",
                    severity="error",
                    where={"scope": "noun_type", "noun_type": nt_name, "field": field, "adjective": adj_name},
                    message=f"Unknown adjective type '{adj_name}' referenced by noun type '{nt_name}'",
                    details={}
                ))

    log.debug("[audit_adjective_alignment] Done -> findings:", len(findings))
    return findings
