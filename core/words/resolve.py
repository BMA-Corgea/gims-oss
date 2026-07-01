"""Resolve a noun's ``type: "adjective"`` fields into engine-validatable FieldRules.

A noun field only says ``{"type": "adjective", "adjective_class": "Reference"}``; the data the
validator needs (``reference_noun`` / ``filters`` / ``request_options``) lives in the *separate*
``adjective_types.json`` entry. This module performs that join once, so the ONE validation engine
(:mod:`core.words.validation`) can validate adjective fields without re-implementing the
adjective handler tree. Both the editor (JSONL) and the workbench (SQL) call this with their own
adjective-lookup, then pass the resolved :class:`WordType` to :func:`validate_instance` with the
appropriate :class:`IdProvider`.

Behavior preserved from the two legacy validators:
  * Reference / ReferenceList → become ``reference`` / ``reference_list`` rules carrying
    ``reference_noun`` / ``reference_key`` / ``filters`` (existence resolved by the IdProvider).
  * ActionRequirement        → ``allowed_values`` = the ``request_options`` keys.
  * Tag / StateControl / Picture / unknown → left as ``adjective`` (no value constraint), exactly
    as the legacy validators left them.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

from core.words.wordtype import WordType

# adjective_class -> how to enrich the FieldRule
_REF_CLASSES = {"Reference": "reference", "ReferenceList": "reference_list"}

GetAdjective = Callable[[str], Optional[Dict]]


def resolve_noun_wordtype(noun_name: str, schema: Optional[dict], get_adj: GetAdjective) -> WordType:
    """Build a noun :class:`WordType` with adjective fields enriched for the engine.

    ``get_adj(field_name)`` returns the adjective entry dict for that field on this noun
    (or ``None`` if there is no adjective definition — tolerated, matching legacy ``continue``).
    """
    wt = WordType.from_dict("noun", noun_name, schema or {})
    for fname, fr in wt.fields.items():
        if fr.type != "adjective":
            continue
        adj = get_adj(fname)
        if not adj:
            continue
        adj_class = (adj.get("adjective_class") or adj.get("class") or "").strip()
        if adj_class in _REF_CLASSES:
            fr.type = _REF_CLASSES[adj_class]
            fr.reference_noun = adj.get("reference_noun")
            fr.reference_key = adj.get("reference_key")
            flt = adj.get("filters")
            fr.filters = flt if isinstance(flt, dict) else {}
        elif adj_class == "ActionRequirement":
            fr.allowed_values = list((adj.get("request_options") or {}).keys())
        # Tag / StateControl / Picture / unknown: no value constraint (legacy parity).
    return wt
