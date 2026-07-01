# core/audit/nouns.py
# Noun auditing (instance-level + engine-routed definition checks).
# Split VERBATIM from core_audit.py (the R19 integrity auditor).

from __future__ import annotations
from typing import Any, Dict, List, Optional, Set
import re

from utils.logger import get_logger
log = get_logger(__name__)

from core.audit.findings import (
    Finding,
    NounTypes,
    NounInstancesByType,
    NounIndex,
    AdjectiveTypes,
)
from core.audit.checks import _is_type, compile_autogen_regex


# -------------------------------
# Noun auditing
# -------------------------------

def _audit_adj_lookup(adjective_types: Optional["AdjectiveTypes"], noun_name: str):
    """Build ``get_adj(field) -> adjective entry | None`` for one noun, from the audit's
    name-keyed adjective_types (only entries whose scope includes this noun)."""
    adjective_types = adjective_types or {}

    def _scope(entry: dict) -> list:
        sc = entry.get("attaches_to") or entry.get("applies_to") or []
        return sc if isinstance(sc, list) else [sc]

    def get_adj(field: str):
        entry = adjective_types.get(field)
        if isinstance(entry, dict) and (not _scope(entry) or noun_name in _scope(entry)):
            return entry
        return None

    return get_adj


def _engine_finding(f, nt_name: str, *, scope: str, pid=None, rid=None, field=None) -> Finding:
    """Map a core.words.validation Finding onto an audit Finding."""
    return Finding(
        code=f"N_{f.code}",
        severity=f.severity,
        where={"scope": scope, "noun_type": nt_name, "field": f.field or field,
               "primary": pid, "_runID": rid},
        message=f.message,
        details={"value": f.value} if getattr(f, "value", None) is not None else {},
    )


def audit_noun_instances(
    noun_types: NounTypes,
    noun_instances_by_type: NounInstancesByType,
    noun_index: Optional[NounIndex] = None,
    *,
    adjective_types: Optional["AdjectiveTypes"] = None,
    engine_validation: Optional[bool] = None,
) -> List[Finding]:
    log.debug("[audit_noun_instances] Start")
    findings: List[Finding] = []
    noun_index = noun_index or {k: set() for k in noun_instances_by_type.keys()}

    if engine_validation is None:
        try:
            from utils.config import audit_engine
            engine_validation = audit_engine()
        except Exception:
            engine_validation = False
    if engine_validation:
        from core.words.resolve import resolve_noun_wordtype
        from core.words.id_provider import StaticIdProvider
        from core.words.validation import validate_instance, validate_wordtype, errors_only
        known_nouns = set(noun_types)
        idp = StaticIdProvider({k: set(v) for k, v in noun_index.items()})

    # Pre-compile ID regex per noun type if autogeneration configured
    id_regex_by_type: Dict[str, Optional[re.Pattern]] = {}
    primary_field_by_type: Dict[str, Optional[str]] = {}

    for nt_name, nt_schema in noun_types.items():
        primary_field = nt_schema.get("primary_id_field")
        primary_field_by_type[nt_name] = primary_field
        autogen = nt_schema.get("autogenerate_segments")
        id_regex_by_type[nt_name] = compile_autogen_regex(autogen) if autogen else None
        log.debug(f"[audit_noun_instances] NounType={nt_name} primary_field={primary_field} id_rx={id_regex_by_type[nt_name]}")

    for nt_name, instances in noun_instances_by_type.items():
        log.debug(f"[audit_noun_instances] Checking noun_type={nt_name} with {len(instances)} instances")
        nt_schema = noun_types.get(nt_name, {})
        # Legacy phantom-key reads (used only when the engine path is disabled). These keys do
        # not exist on real schemas, so the legacy noun-instance checks silently no-op.
        required_fields: Set[str] = set(nt_schema.get("required_fields", []))
        field_types: Dict[str, str] = nt_schema.get("field_types", {}) or {}
        adjective_fields: Dict[str, Dict[str, Any]] = nt_schema.get("adjective_fields", {}) or {}
        primary_field = primary_field_by_type.get(nt_name)
        id_rx = id_regex_by_type.get(nt_name)

        wt = None
        if engine_validation:
            # Resolve the noun + its adjective fields once, then validate the definition itself
            # (dangling references, unknown field types, primary-id-not-a-field, ...). Isolated so
            # one malformed noun_type can't abort the WHOLE audit (engine is on by default now).
            try:
                wt = resolve_noun_wordtype(nt_name, nt_schema, _audit_adj_lookup(adjective_types, nt_name))
                for f in errors_only(validate_wordtype(wt, known_nouns=known_nouns)):
                    findings.append(_engine_finding(f, nt_name, scope="noun_type"))
            except Exception as e:
                wt = None
                log.warning("[audit] engine validation of noun_type failed (skipping):", nt_name, repr(e))

        for inst in instances:
            pid = inst.get(primary_field) if primary_field else None
            rid = inst.get("_runID")
            log.debug(f"[audit_noun_instances] Instance primary={pid} _runID={rid}")

            # Engine path: required/type/date/number/reference via the ONE validation engine
            # (same contract as the editor and workbench). References resolve against noun_index.
            # Per-instance isolation: a bad row is logged + skipped, never aborts the audit.
            if engine_validation and wt is not None:
                try:
                    for f in errors_only(validate_instance(inst, wt, idp)):
                        findings.append(_engine_finding(f, nt_name, scope="noun", pid=pid, rid=rid))
                except Exception as e:
                    log.warning("[audit] engine validation of instance failed (skipping):", nt_name, pid, repr(e))

            # Required fields present (legacy mode only)
            missing = sorted(f for f in required_fields if f not in inst or inst.get(f) in (None, "")) if not engine_validation else []
            if missing:
                log.debug("[audit_noun_instances] Missing required fields:", missing)
                findings.append(Finding(
                    code="N_REQUIRED_MISSING",
                    severity="error",
                    where={"scope": "noun", "noun_type": nt_name, "primary": pid, "_runID": rid},
                    message=f"Missing required fields: {missing}",
                    details={"missing": missing}
                ))

            # Type checks (only for fields declared in schema)
            for field, t in field_types.items():
                if field in inst and inst[field] is not None:
                    ok = _is_type(inst[field], t)
                    if not ok:
                        log.debug(f"[audit_noun_instances] Type mismatch field={field} expected={t} value={inst[field]}")
                        findings.append(Finding(
                            code="N_TYPE_MISMATCH",
                            severity="error",
                            where={"scope": "noun", "noun_type": nt_name, "field": field, "primary": pid, "_runID": rid},
                            message=f"Field '{field}' has wrong type (expected {t})",
                            details={"expected": t, "value": inst[field]}
                        ))

            # Reference adjective checks (if configured on this noun type)
            for field, adj_cfg in adjective_fields.items():
                adj_class = (adj_cfg.get("class") or adj_cfg.get("adjective_class") or "").lower()
                if adj_class in ("reference", "ref"):
                    allowed_types = adj_cfg.get("reference_nouns") or adj_cfg.get("reference_noun")
                    if isinstance(allowed_types, str):
                        allowed_types = [allowed_types]
                    allowed_types = list(allowed_types or [])
                    value = inst.get(field)
                    log.debug(f"[audit_noun_instances] Ref check field={field} value={value} allowed_types={allowed_types}")
                    if value:
                        ok = False
                        for at in allowed_types:
                            if value in (noun_index.get(at) or set()):
                                ok = True
                                break
                        if not ok:
                            log.debug(f"[audit_noun_instances] Invalid reference value={value} allowed_types={allowed_types}")
                            findings.append(Finding(
                                code="N_REF_INVALID",
                                severity="error",
                                where={"scope": "noun", "noun_type": nt_name, "field": field, "primary": pid, "_runID": rid},
                                message=f"Reference '{value}' not found in allowed types {allowed_types}",
                                details={"reference_value": value, "allowed_types": allowed_types}
                            ))

            # Primary ID format (if autogeneration configured)
            if id_rx and primary_field:
                if isinstance(pid, str) and not id_rx.match(pid):
                    log.debug(f"[audit_noun_instances] ID format mismatch primary={pid} pattern={id_rx.pattern}")
                    findings.append(Finding(
                        code="N_ID_FORMAT",
                        severity="error",
                        where={"scope": "noun", "noun_type": nt_name, "primary": pid, "_runID": rid},
                        message=f"Primary ID '{pid}' does not match autogeneration format",
                        details={"pattern": id_rx.pattern}
                    ))

            # Primary ID field missing
            if primary_field and pid is None:
                log.debug(f"[audit_noun_instances] Primary ID missing field={primary_field}")
                findings.append(Finding(
                    code="N_PRIMARY_MISSING",
                    severity="error",
                    where={"scope": "noun", "noun_type": nt_name, "_runID": rid},
                    message=f"Primary ID field '{primary_field}' is missing",
                    details={}
                ))

    log.debug("[audit_noun_instances] Done -> findings:", len(findings))
    return findings
