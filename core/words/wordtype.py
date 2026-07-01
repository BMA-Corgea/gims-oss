"""Canonical ``WordType`` envelope — one in-memory shape for every part of speech.

Every ``*_types.json`` (noun/verb = name-keyed dicts; adjective/adverb = lists today)
normalizes to a name-keyed dict of :class:`WordType`. The on-disk key is the stable
identity; identity fields are also denormalized into the value, and the full original is
preserved in ``raw``, so ``to_dict``/``from_dict`` round-trips losslessly and migration is
idempotent. Prepositional phrases are intentionally out of scope here (Docker/FS — Phase 4/6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

KIND_NOUN = "noun"
KIND_VERB = "verb"
KIND_ADJECTIVE = "adjective"
KIND_ADVERB = "adverb"
KIND_CONJUNCTION = "conjunction"
KINDS = (KIND_NOUN, KIND_VERB, KIND_ADJECTIVE, KIND_ADVERB, KIND_CONJUNCTION)

# The ONE canonical field-type vocabulary (the validation engine rejects anything else).
# ``datetime`` is ``date`` with a time-of-day component — the instant resolution a
# time-aware (Duration) descriptor needs to tick in seconds/minutes/hours, not just days.
CANONICAL_FIELD_TYPES = {
    "string", "text", "number", "int", "bool", "date", "datetime",
    "adjective", "reference", "reference_list", "tag", "picture",
}


@dataclass
class FieldRule:
    """One field's validation rule — the single source the editor/workbench/audit read."""

    name: str
    type: str = "string"
    required: bool = False
    format: Optional[str] = None            # date format token (mmddyyyy, yyyy-mm-dd, ...)
    adjective_class: Optional[str] = None   # when type == "adjective"
    reference_noun: Optional[str] = None
    reference_key: Optional[str] = None
    filters: Dict[str, Any] = field(default_factory=dict)
    allowed_values: Optional[List[Any]] = None
    raw: Dict[str, Any] = field(default_factory=dict)  # full original (lossless round-trip)

    @classmethod
    def from_dict(cls, name: str, d: Optional[Dict[str, Any]]) -> "FieldRule":
        d = d or {}
        # Landmine: filters is sometimes the empty STRING "" instead of {} — normalize on read.
        filters = d.get("filters")
        if not isinstance(filters, dict):
            filters = {}
        return cls(
            name=name,
            type=str(d.get("type", "string")),
            required=bool(d.get("required", False)),
            format=d.get("format"),
            adjective_class=d.get("adjective_class"),
            reference_noun=d.get("reference_noun"),
            reference_key=d.get("reference_key"),
            filters=filters,
            allowed_values=d.get("allowed_values") or d.get("valid_options"),
            raw=dict(d),
        )

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.raw)


@dataclass
class WordType:
    """A part-of-speech type definition in canonical, kind-agnostic form."""

    kind: str
    name: str
    description: Optional[str] = None
    fields: Dict[str, FieldRule] = field(default_factory=dict)
    id_policy: Optional[Dict[str, Any]] = None
    relations: Dict[str, List[str]] = field(default_factory=dict)  # attaches_to / acts_on / monitors
    lifecycle: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)  # full original value (lossless)

    # ---- construction from the various on-disk shapes ----
    @classmethod
    def from_dict(cls, kind: str, name: str, value: Dict[str, Any]) -> "WordType":
        """Build from a name-keyed dict value (noun/verb shape, or an already-migrated entry)."""
        value = value or {}
        # ``fields`` is a dict of rules in most projects, but a bare LIST of field names in
        # some (e.g. Sterility). Normalize both; the original is preserved in ``raw``.
        raw_fields = value.get("fields") or {}
        if isinstance(raw_fields, list):
            fields = {fn: FieldRule(name=fn) for fn in raw_fields if isinstance(fn, str)}
        elif isinstance(raw_fields, dict):
            fields = {fn: FieldRule.from_dict(fn, fd) for fn, fd in raw_fields.items()}
        else:
            fields = {}
        id_policy = None
        if "primary_id_field" in value or value.get("autogenerate_id") is not None:
            id_policy = {
                "primary_id_field": value.get("primary_id_field"),
                "autogenerate": bool(value.get("autogenerate_id", False)),
                "segments": value.get("autogenerate_segments"),
                "format": value.get("autogenerate_format"),
            }
        relations = {}
        # Prefer the canonical key; fall back to the legacy descriptor scope keys so a
        # *dict-shaped* adjective/adverb file (hand-authored, or pre-canonical) keeps its scope.
        attaches = value.get("attaches_to")
        if attaches is None and kind in (KIND_ADJECTIVE, KIND_ADVERB):
            attaches = value.get("applies_to")
            if attaches is None and value.get("verb"):
                attaches = [value["verb"]]
        if attaches:
            relations["attaches_to"] = list(attaches) if isinstance(attaches, (list, tuple)) else [attaches]
        lifecycle = {}
        if "status_values" in value:
            lifecycle["status_values"] = value.get("status_values")
        if "linear_status" in value:
            lifecycle["linear_status"] = value.get("linear_status")
        return cls(
            kind=kind, name=name, description=value.get("description"),
            fields=fields, id_policy=id_policy, relations=relations,
            lifecycle=lifecycle, raw=dict(value),
        )

    @classmethod
    def from_descriptor_entry(cls, kind: str, name: str, entry: Dict[str, Any],
                              attaches_to: List[str]) -> "WordType":
        """Build an adjective/adverb WordType from a (folded) list entry + its attach targets."""
        raw = dict(entry)
        raw["attaches_to"] = list(attaches_to)
        cls_name = entry.get("adjective_class") or entry.get("adverb_class") or entry.get("class")
        return cls(
            kind=kind, name=name, description=entry.get("description"),
            fields={}, id_policy=None,
            relations={"attaches_to": list(attaches_to)},
            lifecycle={},
            raw={**raw, "class": cls_name} if cls_name else raw,
        )

    @property
    def descriptor_class(self) -> Optional[str]:
        return self.raw.get("class") or self.raw.get("adjective_class") or self.raw.get("adverb_class")

    @property
    def attaches_to(self) -> List[str]:
        return list(self.relations.get("attaches_to", []))

    def to_dict(self) -> Dict[str, Any]:
        """The canonical (name-keyed) on-disk value. Starts from ``raw``.

        For descriptor kinds (adjective/adverb) the scope is emitted ONLY as the canonical
        ``attaches_to`` and the legacy scope keys (``applies_to``/``verb``) are dropped — otherwise
        a folded or renamed entry would carry a *stale* ``applies_to`` alongside the correct
        ``attaches_to`` (the reader prefers ``attaches_to``, but the on-disk file would lie).
        """
        out = dict(self.raw)
        if self.kind in (KIND_ADJECTIVE, KIND_ADVERB):
            out.pop("applies_to", None)
            out.pop("verb", None)
        if self.relations.get("attaches_to") is not None:
            out["attaches_to"] = list(self.relations["attaches_to"])
        return out
