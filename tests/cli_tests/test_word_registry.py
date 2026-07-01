"""WordRegistry as reader-backed lifecycle owner (Phase 3): real dependents + real rename.

Rewritten from the legacy fixtures (which seeded phantom prepositional_phrases/adverb_layers
files and asserted string deps) to the new correct behavior.
"""
import json

import pytest

from core.errors import AppError
from utils.word_registry import WordRegistry


def setup_project(tmp_path):
    proj = tmp_path / "P"
    proj.mkdir(parents=True)
    (proj / "noun_types.json").write_text(json.dumps({"N": {"fields": {"sid": {"type": "string"}}}}))
    (proj / "verb_types.json").write_text(json.dumps({"V": {"acts_on": ["N"]}}))
    # LIST-shaped adjective (the shape that used to be invisible / crash the registry)
    (proj / "adjective_types.json").write_text(json.dumps(
        [{"adjective": "A", "adjective_class": "Tag", "applies_to": ["N"]}]))
    (proj / "adverb_types.json").write_text(json.dumps([]))
    return proj


def _reg(tmp_path):
    return WordRegistry("P", project_path=setup_project(tmp_path))


def test_get_all_words_noun(tmp_path):
    assert _reg(tmp_path).get_all_words("noun") == {"N"}


def test_list_shaped_adjectives_are_visible(tmp_path):
    # The old registry returned an EMPTY set for list-shaped adjective files.
    assert _reg(tmp_path).get_all_words("adjective") == {"A"}


def test_get_all_words_empty(tmp_path):
    proj = tmp_path / "P"
    proj.mkdir()
    (proj / "noun_types.json").write_text("{}")
    assert WordRegistry("P", project_path=proj).get_all_words("noun") == set()


def test_get_dependents_noun_is_structured(tmp_path):
    labels = [d["label"] for d in _reg(tmp_path).get_dependents("noun", "N")]
    assert "verb_types: V" in labels          # verb acts_on N
    assert "adjective_types: A" in labels      # adjective attaches_to N


def test_is_monitored(tmp_path):
    r = _reg(tmp_path)
    assert r.is_monitored("noun", "N")
    assert not r.is_monitored("noun", "ghost")


def test_get_dependents_verb_none(tmp_path):
    assert _reg(tmp_path).get_dependents("verb", "V") == []


def test_enforce_disentanglement_raises_apperror(tmp_path):
    with pytest.raises(AppError) as ei:
        _reg(tmp_path).enforce_disentanglement("noun", "N")
    assert ei.value.code == "WORD_IN_USE"
    assert ei.value.status == 409


def test_enforce_disentanglement_no_deps_ok(tmp_path):
    proj = setup_project(tmp_path)
    (proj / "verb_types.json").write_text("{}")
    (proj / "adjective_types.json").write_text("[]")
    WordRegistry("P", project_path=proj).enforce_disentanglement("noun", "N")  # must not raise


def test_rename_references_rewrites_and_renames(tmp_path):
    proj = setup_project(tmp_path)
    deps = WordRegistry("P", project_path=proj).rename_references("noun", "N", "M")
    assert deps  # references were rewritten, not orphaned

    fresh = WordRegistry("P", project_path=proj)
    assert fresh.get_all_words("noun") == {"M"}
    assert json.loads((proj / "verb_types.json").read_text())["V"]["acts_on"] == ["M"]
    # Phase 6/R17: rename now persists the canonical name-keyed dict (was a legacy list), with the
    # scope under the canonical `attaches_to` (legacy `applies_to` dropped to avoid stale drift).
    assert json.loads((proj / "adjective_types.json").read_text())["A"]["attaches_to"] == ["M"]
