"""Phase 3.3: the integrity auditor (R19) routed through the one validation engine, gated.

Default (gate off) preserves the legacy no-op on real schemas (real noun schemas nest field
rules under ``fields``, which the legacy auditor never read). Gate on, the same engine that
backs the editor + workbench surfaces real instance/definition findings.
"""
from core.audit import audit_noun_instances, audit_all


# A realistic noun schema: rules nested under "fields" (NOT the phantom top-level keys).
NOUN_TYPES = {
    "Sample": {
        "primary_id_field": "sample_id",
        "fields": {
            "sample_id": {"type": "string"},
            "qty": {"type": "number"},
            "received_date": {"type": "date", "format": "mmddyy"},
            "parent": {"type": "adjective", "adjective_class": "Reference"},
        },
    },
}
ADJ_TYPES = {
    # 'parent' references the nonexistent noun "Ghost" -> a dangling-reference definition finding.
    "parent": {"adjective_class": "Reference", "reference_noun": "Ghost",
               "applies_to": ["Sample"], "filters": {}},
}


def _codes(findings):
    return {f.code for f in findings}


def test_legacy_mode_is_noop_on_real_schema():
    # Gate off: the legacy noun-instance checks read phantom keys that don't exist -> no findings.
    instances = {"Sample": [{"sample_id": "S-1", "qty": "abc"}]}  # qty is non-numeric...
    findings = audit_noun_instances(NOUN_TYPES, instances, {"Sample": {"S-1"}},
                                    engine_validation=False)
    assert findings == []  # ...but legacy mode never checks it (no-op)


def test_engine_mode_flags_type_and_dangling_reference():
    instances = {"Sample": [{"sample_id": "S-1", "qty": "abc", "received_date": "060824"}]}
    findings = audit_noun_instances(NOUN_TYPES, instances, {"Sample": {"S-1"}},
                                    adjective_types=ADJ_TYPES, engine_validation=True)
    codes = _codes(findings)
    assert "N_TYPE_NOT_NUMBER" in codes          # qty="abc" now actually checked
    assert "N_REFERENCE_DANGLING" in codes       # parent -> Ghost (definition-level)


def test_engine_mode_required_and_valid_pass():
    instances = {"Sample": [{"sample_id": "S-1", "qty": 5, "received_date": "060824"}]}
    findings = audit_noun_instances(
        {"Sample": {"primary_id_field": "sample_id",
                    "fields": {"sample_id": {"type": "string"}, "qty": {"type": "number"}}}},
        instances, {"Sample": {"S-1"}}, engine_validation=True)
    # No adjective refs, valid number -> no findings.
    assert findings == []


def test_audit_all_threads_the_gate():
    instances = {"Sample": [{"sample_id": "S-1", "qty": "abc"}]}
    off = audit_all(noun_types=NOUN_TYPES, verb_types={}, adverb_types={}, adjective_types=ADJ_TYPES,
                    noun_instances_by_type=instances, run_entries_by_group={},
                    engine_validation=False)
    on = audit_all(noun_types=NOUN_TYPES, verb_types={}, adverb_types={}, adjective_types=ADJ_TYPES,
                   noun_instances_by_type=instances, run_entries_by_group={},
                   engine_validation=True)
    assert on["summary"]["total"] > off["summary"]["total"]
