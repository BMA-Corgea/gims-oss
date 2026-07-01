"""Phase 3: the one validation engine — instance + definition checks."""
from core.words.id_provider import NullIdProvider, StaticIdProvider
from core.words.validation import (
    Finding,
    errors_only,
    is_valid_date,
    validate_instance,
    validate_wordtype,
)
from core.words.wordtype import WordType


def _wt(fields, **kw):
    return WordType.from_dict("noun", "Sample", {"fields": fields, **kw})


def test_required_field_missing():
    wt = _wt({"id": {"type": "string", "required": True}})
    assert any(f.code == "REQUIRED_FIELD_MISSING" for f in validate_instance({}, wt))
    assert validate_instance({"id": "S-1"}, wt) == []


def test_number_accepts_numeric_strings_and_native():
    wt = _wt({"qty": {"type": "number"}})
    assert validate_instance({"qty": 3}, wt) == []
    assert validate_instance({"qty": "3.5"}, wt) == []
    assert any(f.code == "TYPE_NOT_NUMBER" for f in validate_instance({"qty": "abc"}, wt))
    # bool is NOT a number
    assert any(f.code == "TYPE_NOT_NUMBER" for f in validate_instance({"qty": True}, wt))


def test_date_honors_declared_format():
    wt = _wt({"d": {"type": "date", "format": "mmddyyyy"}})
    assert validate_instance({"d": "06242026"}, wt) == []        # declared format accepted
    # ISO is accepted as a universal fallback alongside the declared format (safe convergence:
    # the workbench only ever stored ISO, the editor stored the declared format — accept both so
    # neither store's existing data is newly rejected). See is_valid_date().
    assert validate_instance({"d": "2026-06-24"}, wt) == []
    # A value matching neither the declared format nor ISO is still rejected.
    assert any(f.code == "TYPE_NOT_DATE" for f in validate_instance({"d": "not-a-date"}, wt))
    assert is_valid_date("2026-06-24", None)  # default patterns accept ISO


def test_reference_resolution_via_id_provider():
    wt = _wt({"ref": {"type": "reference", "reference_noun": "Submission"}})
    idp = StaticIdProvider({"Submission": {"SUB-1", "SUB-2"}})
    assert validate_instance({"ref": "SUB-1"}, wt, idp) == []
    assert any(f.code == "REFERENCE_NOT_FOUND" for f in validate_instance({"ref": "SUB-9"}, wt, idp))
    # NullIdProvider skips the existence check (degrade gracefully)
    assert validate_instance({"ref": "SUB-9"}, wt, NullIdProvider()) == []


def test_definition_checks():
    wt = _wt(
        {"id": {"type": "string"}, "bad": {"type": "wat"}, "r": {"type": "reference"}},
        primary_id_field="missing",
        autogenerate_id=True,
    )
    codes = {f.code for f in validate_wordtype(wt, known_nouns={"Sample"})}
    assert "UNKNOWN_FIELD_TYPE" in codes
    assert "REFERENCE_MISSING_NOUN" in codes
    assert "PRIMARY_ID_NOT_A_FIELD" in codes


def test_dangling_reference_noun():
    wt = _wt({"r": {"type": "reference", "reference_noun": "Ghost"}})
    assert any(f.code == "REFERENCE_DANGLING" for f in validate_wordtype(wt, known_nouns={"Sample"}))


def test_linear_status_duplicate_steps():
    wt = WordType.from_dict("verb", "Test", {
        "linear_status": {"enabled": True, "steps": [{"id": "a"}, {"id": "a"}, {"id": ""}]},
    })
    assert any(f.code == "LINEAR_STATUS_DUP_STEP" for f in validate_wordtype(wt))
