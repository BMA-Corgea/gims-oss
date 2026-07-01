"""The ONE validation engine, driven by the WordType FieldRule.

Replaces the five divergent validators (utils/semantics, noun_workbench, verb_gui linear-status,
core_audit, utils/handlers/noun). Two layers, both reading the same FieldRule:

* :func:`validate_wordtype`  — definition self-consistency (types, primary id, references, verb
  linear-status anchors).
* :func:`validate_instance` — an instance against its type, with ONE type/date/number/reference
  contract and an injected :class:`IdProvider` so editor + workbench + audit resolve references
  identically.

Findings carry ``{severity, code, field, message}`` so the editor/workbench surface them as a
400 and the audit aggregates them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from core.words.id_provider import IdProvider, NullIdProvider
from core.words.wordtype import CANONICAL_FIELD_TYPES, WordType


@dataclass
class Finding:
    severity: str            # "error" | "warning"
    code: str
    field: Optional[str]
    message: str
    value: Any = None        # the offending value, when one applies (for faithful rendering)


_DEFAULT_DATE_PATTERNS = ["%Y-%m-%d", "%m/%d/%Y", "%m%d%Y", "%Y%m%d", "%m-%d-%Y"]


def _fmt_to_strptime(fmt: str) -> str:
    # Same token order as utils.id_generator.generate_date_segment (yyyy before yy).
    return (fmt.replace("yyyy", "%Y").replace("yy", "%y").replace("mm", "%m").replace("dd", "%d"))


def is_valid_date(value: Any, fmt: Optional[str]) -> bool:
    s = str(value).strip()
    if not s:
        return False
    # When a format is declared, accept that format OR ISO-8601 (yyyy-mm-dd). ISO is the
    # universal interchange form and is always an unambiguous valid date; accepting it as a
    # fallback is the *safe convergence* of the two legacy validators (the editor honored the
    # declared format; the workbench only ever accepted ISO). This way neither store's existing
    # data is newly rejected when both route through this one engine.
    patterns = [_fmt_to_strptime(fmt), "%Y-%m-%d"] if fmt else _DEFAULT_DATE_PATTERNS
    for p in patterns:
        try:
            datetime.strptime(s, p)
            return True
        except ValueError:
            continue
    return False


# ISO-8601 instant shapes accepted for a ``datetime`` field (with/without fractional
# seconds and a trailing ``Z``; space- or ``T``-separated). The compliance clock emits
# the first shape (``now_iso_ms()`` → ``2026-06-26T12:00:00.123Z``).
_ISO_DATETIME_PATTERNS = [
    "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
]


def is_valid_datetime(value: Any, fmt: Optional[str]) -> bool:
    """A ``datetime`` field accepts an ISO-8601 instant (time-of-day optional, fractional
    seconds optional, trailing ``Z`` optional). A bare date — the declared ``format`` or
    ISO ``yyyy-mm-dd`` — is also accepted (interpreted at midnight), so a datetime field
    never *newly rejects* a value a date field would have allowed."""
    s = str(value).strip()
    if not s:
        return False
    for p in _ISO_DATETIME_PATTERNS:
        try:
            datetime.strptime(s, p)
            return True
        except ValueError:
            continue
    return is_valid_date(value, fmt)


def is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value))
        return True
    except (ValueError, TypeError):
        return False


def validate_wordtype(wt: WordType, *, known_nouns: Optional[Set[str]] = None) -> List[Finding]:
    """Check a type definition for self-consistency."""
    findings: List[Finding] = []
    for fn, fr in wt.fields.items():
        if fr.type not in CANONICAL_FIELD_TYPES:
            findings.append(Finding("warning", "UNKNOWN_FIELD_TYPE", fn,
                                    f"Field '{fn}' has unknown type '{fr.type}'"))
        if fr.type in ("reference", "reference_list") and not fr.reference_noun:
            findings.append(Finding("error", "REFERENCE_MISSING_NOUN", fn,
                                    f"Reference field '{fn}' declares no reference_noun"))
        if fr.reference_noun and known_nouns is not None and fr.reference_noun not in known_nouns:
            findings.append(Finding("error", "REFERENCE_DANGLING", fn,
                                    f"Field '{fn}' references unknown noun '{fr.reference_noun}'"))
    if wt.id_policy and wt.id_policy.get("primary_id_field"):
        pid = wt.id_policy["primary_id_field"]
        if wt.fields and pid not in wt.fields:
            findings.append(Finding("error", "PRIMARY_ID_NOT_A_FIELD", pid,
                                    f"primary_id_field '{pid}' is not a declared field"))
    ls = wt.lifecycle.get("linear_status")
    if isinstance(ls, dict) and ls.get("enabled"):
        step_ids = [s.get("id") for s in (ls.get("steps") or []) if isinstance(s, dict)]
        real = [s for s in step_ids if s]
        if len(real) != len(set(real)):
            findings.append(Finding("error", "LINEAR_STATUS_DUP_STEP", None,
                                    "linear_status has duplicate or blank step ids"))
    return findings


def validate_instance(item: Dict[str, Any], wt: WordType,
                      id_provider: Optional[IdProvider] = None) -> List[Finding]:
    """Check one instance dict against its type. ``id_provider`` resolves reference existence."""
    id_provider = id_provider or NullIdProvider()
    findings: List[Finding] = []
    for fn, fr in wt.fields.items():
        present = fn in item and item.get(fn) not in (None, "")
        if fr.required and not present:
            findings.append(Finding("error", "REQUIRED_FIELD_MISSING", fn,
                                    f"Required field '{fn}' is missing"))
            continue
        if not present:
            continue
        val = item[fn]
        t = fr.type
        if t in ("number", "int"):
            if not is_number(val):
                findings.append(Finding("error", "TYPE_NOT_NUMBER", fn,
                                        f"Field '{fn}' expects a number, got {val!r}", value=val))
        elif t == "date":
            if not is_valid_date(val, fr.format):
                findings.append(Finding("error", "TYPE_NOT_DATE", fn,
                                        f"Field '{fn}' is not a valid date ({fr.format or 'any'}): {val!r}", value=val))
        elif t == "datetime":
            if not is_valid_datetime(val, fr.format):
                findings.append(Finding("error", "TYPE_NOT_DATETIME", fn,
                                        f"Field '{fn}' is not a valid date-time: {val!r}", value=val))
        elif t == "bool":
            if not isinstance(val, bool) and str(val).lower() not in ("true", "false", "1", "0", "yes", "no"):
                findings.append(Finding("error", "TYPE_NOT_BOOL", fn, f"Field '{fn}' expects a boolean", value=val))
        elif t in ("string", "text"):
            if not isinstance(val, str):
                findings.append(Finding("error", "TYPE_NOT_STRING", fn,
                                        f"Field '{fn}' expects a string, got {val!r}", value=val))
        elif t in ("reference", "reference_list") and fr.reference_noun:
            valid = id_provider.valid_ids(fr.reference_noun, fr.filters, fr.reference_key)
            if valid is not None:
                vals = val if (t == "reference_list" and isinstance(val, list)) else _split_reference_list(val, t)
                for v in vals:
                    if str(v) not in valid:
                        findings.append(Finding("error", "REFERENCE_NOT_FOUND", fn,
                                                f"Field '{fn}' references '{v}' not found in '{fr.reference_noun}'",
                                                value=v))
        # tag/picture/adjective: no primitive constraint — fall through to allowed_values.

        # allowed_values membership (e.g. ActionRequirement request_options keys). A reference
        # field never carries allowed_values, so this cannot double-fire with the ref check.
        if fr.allowed_values and t not in ("reference", "reference_list"):
            allowed = {str(a) for a in fr.allowed_values}
            if str(val) not in allowed:
                findings.append(Finding("error", "ALLOWED_VALUE_INVALID", fn,
                                        f"Field '{fn}' value {val!r} not in allowed values", value=val))
    return findings


def _split_reference_list(val: Any, t: str) -> List[Any]:
    """A reference_list value may arrive as a real list or a comma-joined string."""
    if t == "reference_list" and isinstance(val, str):
        return [p.strip() for p in val.split(",") if p.strip()]
    return [val]


def errors_only(findings: List[Finding]) -> List[Finding]:
    return [f for f in findings if f.severity == "error"]


def render_legacy_errors(findings: List[Finding], wt: WordType) -> List[str]:
    """Render engine findings as the legacy error strings the editor/workbench have always
    returned, so callers and the UI keep their exact wording. Unknown codes fall back to the
    finding's own message."""
    out: List[str] = []
    for f in errors_only(findings):
        fr = wt.fields.get(f.field) if f.field else None
        if f.code == "REQUIRED_FIELD_MISSING":
            out.append(f"'{f.field}' is required but missing.")
        elif f.code == "TYPE_NOT_DATE":
            fmt = (fr.format if fr and fr.format else "yyyy-mm-dd")
            out.append(f"'{f.field}' has invalid date '{f.value}' (expected {fmt}).")
        elif f.code == "TYPE_NOT_NUMBER":
            out.append(f"'{f.field}' should be a number, got '{f.value}'.")
        elif f.code == "TYPE_NOT_STRING":
            out.append(f"'{f.field}' should be a string.")
        elif f.code == "REFERENCE_NOT_FOUND":
            if fr and fr.type == "reference_list":
                out.append(f"'{f.field}' contains unknown ID '{f.value}'.")
            else:
                out.append(f"'{f.field}' references unknown ID '{f.value}'.")
        elif f.code == "ALLOWED_VALUE_INVALID":
            opts = list(fr.allowed_values) if fr and fr.allowed_values else []
            out.append(f"'{f.field}' has invalid option '{f.value}' (must be one of {opts}).")
        else:
            out.append(f.message)
    return out
