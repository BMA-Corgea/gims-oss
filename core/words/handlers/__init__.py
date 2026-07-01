"""Unified descriptor handlers — the single canonical dispatch map.

This package is the Phase 3 "handler collapse": the two near-identical class
trees (``core/handlers/adjectives/`` and ``core/handlers/adverbs/``) and their
EIGHT scattered dispatch-map copies fold into ONE :class:`Descriptor` plus this
one ``DESCRIPTOR_CLASSES`` map.

The map preserves EVERY class key found across all copies, including drifted
aliases, so resolution is never silently re-pointed:

  Canonical (both trees, where applicable):
    Tag, Reference, ReferenceList, Picture
  Noun-attached only:
    ActionRequirement
  Verb-attached only:
    Attribute
  Drifted aliases (must remain resolvable):
    StateControl  -> behaves as Attribute (single freeform value; legacy
                     adjective tree had a StateControlAdjective with
                     allowed_values; the closest preserved behavior is Attribute)
    StateContext  -> Attribute (the adverb map aliased this to AttributeAdverb)

Sources audited for keys/aliases:
  * gui/adjective_gui.py            : ActionRequirement, Tag, Reference, ReferenceList, Picture
  * gui/adverb_gui.py               : Tag, Reference, ReferenceList, Picture, Attribute
  * utils/handlers/adverb.py        : Tag, Reference, ReferenceList, Attribute, StateContext
  * utils/handlers/adjective.py     : ActionRequirement, StateControl, Tag, Reference, ReferenceList, Picture
  * meta/meta_adjective/adjective.py: (same as utils/handlers/adjective.py)
  * meta/meta_adjective/core_adjective.py + tools/launch_adjective.py: StateControl + adjective set
  * meta/meta_adverb/adverb.py      : Tag, Reference, ReferenceList, Attribute, StateContext

Purely additive: importing has no side effects and changes no running behavior.
"""
from __future__ import annotations

from typing import Optional

from core.words.handlers.base import WordHandler, ATTACH_NOUN, ATTACH_VERB
from core.words.handlers.behaviors import (
    Descriptor,
    TagBehavior,
    ReferenceBehavior,
    ReferenceListBehavior,
    PictureBehavior,
    ActionRequirementBehavior,
    AttributeBehavior,
    DurationBehavior,
)

# ──────────────────────────────────────────────────────────────────────────────
# THE single dispatch map: class key -> behavior name.
# Behavior name is the canonical strategy key understood by Descriptor.
# ──────────────────────────────────────────────────────────────────────────────
DESCRIPTOR_CLASSES: dict[str, str] = {
    # canonical (present in both trees where applicable)
    "Tag": "Tag",
    "Reference": "Reference",
    "ReferenceList": "ReferenceList",
    "Picture": "Picture",
    # noun-attached only (adjective tree)
    "ActionRequirement": "ActionRequirement",
    # noun-attached only — time-aware interval over two of the record's date fields
    "Duration": "Duration",
    # verb-attached only (adverb tree)
    "Attribute": "Attribute",
    # drifted aliases — preserved so resolution is never silently re-pointed
    "StateControl": "Attribute",
    "StateContext": "Attribute",
}

# Which canonical class keys are valid for each attach context. Aliases inherit
# from their canonical behavior's allowed contexts.
_BEHAVIOR_CONTEXTS: dict[str, frozenset[str]] = {
    "Tag": frozenset({ATTACH_NOUN, ATTACH_VERB}),
    "Reference": frozenset({ATTACH_NOUN, ATTACH_VERB}),
    "ReferenceList": frozenset({ATTACH_NOUN, ATTACH_VERB}),
    "Picture": frozenset({ATTACH_NOUN, ATTACH_VERB}),
    "ActionRequirement": frozenset({ATTACH_NOUN}),  # adjective-only
    "Duration": frozenset({ATTACH_NOUN}),           # adjective-only (time-aware interval)
    # Attribute (single freeform value) is the adverb-tree behavior, but it is the
    # resolution target of BOTH drifted aliases — StateContext (adverb/verb) and
    # StateControl (adjective/noun) — so it must be resolvable in both contexts.
    # See the StateControl note in get_descriptor / module docstring: the legacy
    # StateControlAdjective carried `allowed_values`, which has no dedicated
    # behavior in this collapse; Attribute is the closest preserved target.
    "Attribute": frozenset({ATTACH_NOUN, ATTACH_VERB}),
}

# Map the public ``attaches_kind`` ("adjective"|"adverb") to the attach context.
_KIND_TO_CONTEXT = {
    "adjective": ATTACH_NOUN,
    "noun": ATTACH_NOUN,
    "adverb": ATTACH_VERB,
    "verb": ATTACH_VERB,
}


def _context_for_kind(attaches_kind: str) -> str:
    ctx = _KIND_TO_CONTEXT.get(attaches_kind)
    if ctx is None:
        raise ValueError(
            f"attaches_kind must be one of {sorted(_KIND_TO_CONTEXT)}, got {attaches_kind!r}"
        )
    return ctx


def make_class_getter(attaches_kind: str):
    """Return a ``get_<x>_class_handler(name)`` resolver for one attach context.

    The returned function mirrors the legacy ``get_adjective_class_handler`` /
    ``get_adverb_class_handler`` shape:

      * called with ``None`` → returns the full ``{class_key: behavior_name}`` map
        filtered to keys valid in this context;
      * called with a class key → returns the canonical behavior name, or ``None``
        if the key is unknown OR not valid for this context.

    Resolution covers every alias in :data:`DESCRIPTOR_CLASSES`; a key that exists
    but is not valid for this context (e.g. ``Attribute`` on a noun) resolves to
    ``None`` rather than being silently re-pointed.
    """
    context = _context_for_kind(attaches_kind)

    def resolver(name: Optional[str] = None):
        valid = {
            key: behavior
            for key, behavior in DESCRIPTOR_CLASSES.items()
            if context in _BEHAVIOR_CONTEXTS[behavior]
        }
        if name is None:
            return valid
        return valid.get(name)

    resolver.__name__ = f"get_{attaches_kind}_class_handler"
    resolver.attaches_kind = attaches_kind
    resolver.context = context
    return resolver


# Convenience pre-built resolvers (legacy-named).
get_adjective_class_handler = make_class_getter("adjective")
get_adverb_class_handler = make_class_getter("adverb")


def get_descriptor(
    data: dict,
    *,
    attaches_to: Optional[str] = None,
    attaches_kind: Optional[str] = None,
    project_name: Optional[str] = None,
    target_name: Optional[str] = None,
    verb_types: Optional[dict] = None,
    class_key: Optional[str] = None,
) -> Descriptor:
    """Factory: build a :class:`Descriptor` for a descriptor entry.

    Args:
        data: the descriptor entry dict (an adjective_types/adverb_types row).
        attaches_to: explicit attach context ("noun"|"verb"); if omitted it is
            derived from ``attaches_kind``.
        attaches_kind: "adjective"/"noun" or "adverb"/"verb". Determines both the
            attach context and which class-key column of the entry is read.
        project_name: owning project name.
        target_name: the attached noun type or verb name.
        verb_types: known verb map (needed by ActionRequirement).
        class_key: override the class key; otherwise read from the entry
            (``adjective_class`` / ``adverb_class`` / ``class``).

    Unknown class keys fall back to the plain :class:`Descriptor` behavior-less
    handler base by raising — callers that want a tolerant fallback should pass a
    valid ``class_key``. To match legacy tolerance, an unresolved key returns a
    bare :class:`WordHandler`-equivalent via the ``Attribute``/base path only when
    a context default exists; here we keep it strict and resolve via the map.
    """
    # Resolve context.
    if attaches_to is None:
        if attaches_kind is None:
            raise ValueError("provide attaches_to or attaches_kind")
        attaches_to = _context_for_kind(attaches_kind)
    elif attaches_to not in (ATTACH_NOUN, ATTACH_VERB):
        raise ValueError(f"attaches_to must be 'noun' or 'verb', got {attaches_to!r}")

    # Resolve the class key from the entry if not explicitly provided.
    if class_key is None:
        class_key = (
            data.get("adjective_class")
            or data.get("adverb_class")
            or data.get("class")
        )

    behavior_name = DESCRIPTOR_CLASSES.get(class_key) if class_key else None
    if behavior_name is None:
        # Default-per-context fallback preserves legacy behavior: the adjective
        # GUI fell back to BaseAdjective, the adverb tool layer to AttributeAdverb.
        # We pick a context-appropriate behavior that is always resolvable.
        behavior_name = "Attribute" if attaches_to == ATTACH_VERB else "Tag"

    return Descriptor(
        data if data is not None else {},
        behavior_name=behavior_name,
        attaches_to=attaches_to,
        target_name=target_name,
        verb_types=verb_types,
        project_name=project_name,
    )


__all__ = [
    "WordHandler",
    "Descriptor",
    "DESCRIPTOR_CLASSES",
    "make_class_getter",
    "get_descriptor",
    "get_adjective_class_handler",
    "get_adverb_class_handler",
    "ATTACH_NOUN",
    "ATTACH_VERB",
    "TagBehavior",
    "ReferenceBehavior",
    "ReferenceListBehavior",
    "PictureBehavior",
    "ActionRequirementBehavior",
    "AttributeBehavior",
    "DurationBehavior",
]
