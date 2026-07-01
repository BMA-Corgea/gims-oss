"""Phase 3 handler collapse: the unified Descriptor + single dispatch map.

These tests assert the additive ``core/words/handlers`` package resolves every
class key/alias from the eight legacy dispatch-map copies, in the correct attach
context, and that the ReferenceList behavior absorbs the str-vs-list reference
coercion once on construction.
"""
import pytest

from core.words.handlers import (
    make_class_getter,
    get_descriptor,
    DESCRIPTOR_CLASSES,
    Descriptor,
)


@pytest.fixture
def get_noun():
    return make_class_getter("noun")


@pytest.fixture
def get_verb():
    return make_class_getter("verb")


# ──────────────────────────────────────────────────────────────────────────────
# Shared keys resolve in BOTH contexts
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", ["Tag", "Reference", "ReferenceList", "Picture"])
def test_shared_keys_resolve_in_both_contexts(get_noun, get_verb, key):
    assert get_noun(key) is not None, f"noun context should resolve {key}"
    assert get_verb(key) is not None, f"verb context should resolve {key}"


# ──────────────────────────────────────────────────────────────────────────────
# Context-exclusive keys
# ──────────────────────────────────────────────────────────────────────────────
def test_action_requirement_is_noun_attached(get_noun, get_verb):
    """ActionRequirement exists only in the adjective (noun) tree."""
    assert get_noun("ActionRequirement") is not None
    assert get_verb("ActionRequirement") is None


def test_attribute_is_verb_attached(get_verb):
    """Attribute is the adverb (verb) tree's freeform-value behavior."""
    assert get_verb("Attribute") is not None


# ──────────────────────────────────────────────────────────────────────────────
# Drifted aliases must remain resolvable (never silently re-pointed)
# ──────────────────────────────────────────────────────────────────────────────
def test_state_aliases_resolve(get_noun, get_verb):
    # StateControl lived in the adjective maps; StateContext in the adverb maps.
    assert get_noun("StateControl") is not None, "StateControl must resolve"
    assert get_verb("StateContext") is not None, "StateContext must resolve"
    # Both fold to the Attribute behavior in this collapse.
    assert DESCRIPTOR_CLASSES["StateControl"] == "Attribute"
    assert DESCRIPTOR_CLASSES["StateContext"] == "Attribute"


def test_every_legacy_key_is_present():
    """All keys/aliases gathered from the 8 dispatch-map copies are present."""
    expected = {
        "Tag", "Reference", "ReferenceList", "Picture",
        "ActionRequirement", "Attribute", "StateControl", "StateContext",
    }
    assert expected <= set(DESCRIPTOR_CLASSES)


def test_full_map_is_returned_without_a_name(get_noun):
    """Calling a resolver with no name returns the full context-valid map."""
    full = get_noun()
    assert isinstance(full, dict)
    assert "Tag" in full and "ActionRequirement" in full


# ──────────────────────────────────────────────────────────────────────────────
# ReferenceList normalization: single reference_noun="X" -> list form
# ──────────────────────────────────────────────────────────────────────────────
def test_reference_list_normalizes_single_string_to_list():
    desc = get_descriptor(
        {"adjective": "links", "adjective_class": "ReferenceList", "reference_noun": "Sample"},
        attaches_kind="adjective",
        target_name="Run",
    )
    assert isinstance(desc, Descriptor)
    # get_reference_noun always yields a list
    assert desc.get_reference_noun() == ["Sample"]
    # the underlying data is normalized in place, under both legacy keys
    assert desc.data["reference_noun"] == ["Sample"]
    assert desc.data["reference_nouns"] == ["Sample"]


def test_reference_list_normalizes_for_adverb_context():
    """Adverb tree preferred 'reference_nouns'; a single string still folds to list."""
    desc = get_descriptor(
        {"adverb": "links", "adverb_class": "ReferenceList", "reference_noun": "Sample"},
        attaches_kind="adverb",
        target_name="Run",
    )
    assert desc.get_reference_noun() == ["Sample"]
    assert desc.data["reference_nouns"] == ["Sample"]


def test_reference_list_already_a_list_is_untouched():
    desc = get_descriptor(
        {"adjective": "links", "adjective_class": "ReferenceList",
         "reference_noun": ["A", "B"]},
        attaches_kind="adjective",
        target_name="Run",
    )
    assert desc.get_reference_noun() == ["A", "B"]


# ──────────────────────────────────────────────────────────────────────────────
# The "same handler, different target" property
# ──────────────────────────────────────────────────────────────────────────────
def test_adjective_and_adverb_tag_are_the_same_handler_type():
    adj = get_descriptor({"adjective": "t", "adjective_class": "Tag"},
                         attaches_kind="adjective", target_name="Sample")
    adv = get_descriptor({"adverb": "t", "adverb_class": "Tag"},
                         attaches_kind="adverb", target_name="Run")
    assert type(adj) is type(adv) is Descriptor
    assert adj.behavior_name == adv.behavior_name == "Tag"
    assert adj.attaches_to == "noun"
    assert adv.attaches_to == "verb"
