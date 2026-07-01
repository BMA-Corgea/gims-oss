"""Phase 3.2 wiring: the resolver, the JSONL/SQL IdProviders, and the editor adapter that route
all instance validation through the one engine — with adjective-field parity preserved."""
import json

from core.words.id_provider import JsonlIdProvider, SqlIdProvider
from core.words.resolve import resolve_noun_wordtype


# ── resolver: adjective fields fold into engine-validatable rules ───────────────
def test_resolve_reference_adjective_becomes_reference_rule():
    schema = {"fields": {"sub": {"type": "adjective", "adjective_class": "Reference"}}}
    adj = {"adjective_class": "Reference", "reference_noun": "Submission", "filters": {}}
    wt = resolve_noun_wordtype("Sample", schema, lambda f: adj if f == "sub" else None)
    fr = wt.fields["sub"]
    assert fr.type == "reference" and fr.reference_noun == "Submission"


def test_resolve_action_requirement_becomes_allowed_values():
    schema = {"fields": {"request": {"type": "adjective", "adjective_class": "ActionRequirement"}}}
    adj = {"adjective_class": "ActionRequirement", "request_options": {"Micro": ["X"], "Potency": ["Y"]}}
    wt = resolve_noun_wordtype("Submission", schema, lambda f: adj)
    assert set(wt.fields["request"].allowed_values) == {"Micro", "Potency"}


def test_resolve_tag_adjective_stays_unconstrained():
    schema = {"fields": {"status": {"type": "adjective", "adjective_class": "Tag"}}}
    adj = {"adjective_class": "Tag", "valid_options": [{"value": "ok"}]}
    wt = resolve_noun_wordtype("Submission", schema, lambda f: adj)
    fr = wt.fields["status"]
    assert fr.type == "adjective" and not fr.allowed_values  # Tag values are not engine-validated


# ── IdProviders ────────────────────────────────────────────────────────────────
def _make_project(tmp_path):
    (tmp_path / "noun_types.json").write_text(json.dumps({
        "Submission": {"primary_id_field": "submission_id", "fields": {"submission_id": {"type": "string"}}},
    }))
    d = tmp_path / "nouns" / "Submission"
    d.mkdir(parents=True)
    (d / "items.jsonl").write_text(
        '{"submission_id": "SUB-1", "kind": "a"}\n{"submission_id": "SUB-2", "kind": "b"}\n')
    return tmp_path


def test_jsonl_id_provider_reads_primary_ids(tmp_path):
    _make_project(tmp_path)
    idp = JsonlIdProvider(tmp_path)
    assert idp.valid_ids("Submission") == {"SUB-1", "SUB-2"}


def test_jsonl_id_provider_applies_filters(tmp_path):
    _make_project(tmp_path)
    idp = JsonlIdProvider(tmp_path)
    assert idp.valid_ids("Submission", {"kind": "a"}) == {"SUB-1"}


def test_jsonl_id_provider_missing_noun_is_empty_not_none(tmp_path):
    _make_project(tmp_path)
    assert JsonlIdProvider(tmp_path).valid_ids("Ghost") == set()


def test_sql_id_provider_uses_injected_lister():
    idp = SqlIdProvider(lambda noun: ["A", 2] if noun == "N" else [])
    assert idp.valid_ids("N") == {"A", "2"}
    assert idp.valid_ids("Other") == set()


# ── editor adapter end-to-end (parity for adjective references + action requirement) ──
def _noun_type(tmp_path, name, schema):
    from utils.handlers.noun import NounType
    nt = NounType(name, schema, tmp_path / "noun_types.json")
    nt.project_path = tmp_path
    return nt


def test_editor_adapter_reference_ok_and_unknown(tmp_path):
    from utils import semantics
    _make_project(tmp_path)
    (tmp_path / "adjective_types.json").write_text(json.dumps([
        {"adjective": "submission", "adjective_class": "Reference",
         "applies_to": ["Sample"], "reference_noun": "Submission", "filters": {}},
    ]))
    schema = {"fields": {"submission": {"type": "adjective", "adjective_class": "Reference"}}}
    nt = _noun_type(tmp_path, "Sample", schema)
    assert semantics.validate_item_against_schema({"submission": "SUB-1"}, nt) == []
    errs = semantics.validate_item_against_schema({"submission": "NOPE"}, nt)
    assert errs and "unknown ID" in errs[0]


def test_editor_adapter_action_requirement_option(tmp_path):
    from utils import semantics
    _make_project(tmp_path)
    (tmp_path / "adjective_types.json").write_text(json.dumps([
        {"adjective": "request", "adjective_class": "ActionRequirement",
         "applies_to": ["Submission"], "request_options": {"Micro": ["X"]}},
    ]))
    schema = {"fields": {"request": {"type": "adjective", "adjective_class": "ActionRequirement"}}}
    nt = _noun_type(tmp_path, "Submission", schema)
    assert semantics.validate_item_against_schema({"request": "Micro"}, nt) == []
    errs = semantics.validate_item_against_schema({"request": "Bogus"}, nt)
    assert errs and "invalid option" in errs[0]


def test_editor_adapter_date_accepts_declared_format_and_iso(tmp_path):
    from utils import semantics
    schema = {"fields": {"received_date": {"type": "date", "format": "mmddyy"}}}
    nt = _noun_type(tmp_path, "Submission", schema)
    assert semantics.validate_item_against_schema({"received_date": "060824"}, nt) == []
    assert semantics.validate_item_against_schema({"received_date": "2024-06-08"}, nt) == []
    errs = semantics.validate_item_against_schema({"received_date": "garbage"}, nt)
    assert errs and "invalid date" in errs[0]
