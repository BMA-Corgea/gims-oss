"""Unified word-handler base — the merge of ``BaseAdjective`` and ``BaseAdverb``.

The two legacy trees (``core/handlers/adjectives/base.py`` and
``core/handlers/adverbs/base_adverb.py``) are ~92% identical. They differ only in:

* the **context noun** the descriptor attaches to (``noun_type`` vs ``verb_name``),
* the **identity field key** of the entry (``"adjective"`` vs ``"adverb"``),
* one option flag name (``display_in_id`` vs ``display_in_label``), and
* their **constructor conventions** (see :meth:`WordHandler.from_legacy`).

This base reconciles all of that behind a single ``attaches_to`` context
("noun" | "verb") so that "an adjective on a noun" and "an adverb on a verb"
become the SAME handler pointed at a different target.

Purely additive: importing this module has no side effects and changes no
running behavior. Later steps re-export the 8 dispatch-map copies through here.
"""
from __future__ import annotations

from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

# Context kinds. An adjective attaches to a noun; an adverb attaches to a verb.
ATTACH_NOUN = "noun"
ATTACH_VERB = "verb"

# Per-context entry identity field. Adjective entries are keyed by "adjective";
# adverb entries by "adverb". We keep BOTH resolvable so a handler reads its own
# field name regardless of which legacy shape produced the entry.
_IDENTITY_FIELD = {
    ATTACH_NOUN: "adjective",
    ATTACH_VERB: "adverb",
}

# Per-context "show in label/id" option flag. The two trees drifted on this name;
# we normalize reads so a single behavior handles both verbatim.
_DISPLAY_FLAG = {
    ATTACH_NOUN: "display_in_id",
    ATTACH_VERB: "display_in_label",
}


class WordHandler:
    """One handler for both descriptor trees, selected by ``attaches_to`` context.

    ``attaches_to`` is "noun" (adjective-on-noun) or "verb" (adverb-on-verb). The
    target name is the noun type or verb name respectively, exposed under BOTH
    ``noun_type`` and ``verb_name`` plus the neutral ``target_name`` so any caller
    of either legacy tree keeps working.
    """

    def __init__(
        self,
        data: dict,
        *,
        attaches_to: str = ATTACH_NOUN,
        target_name: Optional[str] = None,
        verb_types: Optional[dict] = None,
        project_name: Optional[str] = None,
    ):
        if attaches_to not in (ATTACH_NOUN, ATTACH_VERB):
            raise ValueError(
                f"attaches_to must be {ATTACH_NOUN!r} or {ATTACH_VERB!r}, got {attaches_to!r}"
            )
        self.data = data if data is not None else {}
        self.attaches_to = attaches_to
        self.target_name = target_name
        self.project_name = project_name
        # BaseAdjective tracked verb_names (list of verb keys) for ActionRequirement.
        self.verb_names = list(verb_types.keys()) if verb_types else []

    # ---- context-aware aliases (keep both legacy attribute names alive) ----
    @property
    def noun_type(self) -> Optional[str]:
        """Legacy adjective alias for the attached target."""
        return self.target_name

    @property
    def verb_name(self) -> Optional[str]:
        """Legacy adverb alias for the attached target."""
        return self.target_name

    @property
    def identity_field(self) -> str:
        """The entry key naming this descriptor ('adjective' or 'adverb')."""
        return _IDENTITY_FIELD[self.attaches_to]

    @property
    def display_flag(self) -> str:
        """The 'show in label/id' option key for this context."""
        return _DISPLAY_FLAG[self.attaches_to]

    def get_self_name(self) -> Optional[str]:
        """Read this descriptor's own name from the entry, tolerating either key."""
        return self.data.get("adjective") or self.data.get("adverb")

    # ---- legacy-constructor reconciliation ----
    @classmethod
    def from_legacy(cls, *args, **kwargs) -> "WordHandler":
        """Build a handler from ANY of the legacy constructor conventions.

        Supported call shapes seen across the twin trees + tool layer:

        * adjective:  ``(data, noun_type=..., verb_types=..., project_name=...)``
        * adverb:     ``(data, verb_name=..., project_name=...)``
        * adverb alt: ``(data, project_name, name)`` / positional dict-first
        * tool layer: ``(project_name, name, config)`` — project/name/config
          positional (``utils.handlers.adverb.BaseAdverb`` style), where the
          *config* dict is the entry ``data``.

        The result is always a context-explicit :class:`WordHandler`. ``attaches_to``
        may be passed as a kwarg; otherwise it is inferred (verb_name → "verb",
        noun_type → "noun", else default "noun").
        """
        attaches_to = kwargs.pop("attaches_to", None)
        project_name = kwargs.pop("project_name", None)
        verb_types = kwargs.pop("verb_types", None)
        noun_type = kwargs.pop("noun_type", None)
        verb_name = kwargs.pop("verb_name", None)
        target_name = kwargs.pop("target_name", None)

        data: Optional[dict] = None
        name: Optional[str] = None

        if args:
            if isinstance(args[0], dict):
                # data-first conventions: (data, ...) used by both core trees.
                data = args[0]
                # any trailing positional that is a str is a target name fallback
                for extra in args[1:]:
                    if isinstance(extra, str) and name is None:
                        name = extra
            else:
                # (project_name, name, config) tool-layer convention.
                pos = list(args)
                if project_name is None and pos:
                    project_name = pos.pop(0)
                if pos:
                    name = pos.pop(0) if isinstance(pos[0], str) else None
                    if name is None:
                        # first positional after project wasn't a name; treat as config
                        data = pos.pop(0) if isinstance(pos[0], dict) else None
                if data is None and pos and isinstance(pos[0], dict):
                    data = pos.pop(0)

        if data is None:
            data = {}

        # Resolve target name from the most specific source available.
        resolved_target = (
            target_name
            or noun_type
            or verb_name
            or name
            or data.get("adjective")
            or data.get("adverb")
        )

        # Infer context if not given. Adverb signals (verb attachment) take
        # priority over the default-noun fallback.
        if attaches_to is None:
            adverb_signal = (
                verb_name is not None
                or "adverb" in data
                or "adverb_class" in data
                or "verb" in data
            )
            noun_signal = (
                noun_type is not None
                or "adjective" in data
                or "adjective_class" in data
                or "applies_to" in data
            )
            if adverb_signal and not noun_signal:
                attaches_to = ATTACH_VERB
            else:
                attaches_to = ATTACH_NOUN

        return cls(
            data,
            attaches_to=attaches_to,
            target_name=resolved_target,
            verb_types=verb_types,
            project_name=project_name,
        )

    # ---- field access (merged verbatim from both bases) ----
    def set_field(self, key: str, value):
        """Set a field, normalizing list → single string.

        Reference/filter keys are protected (must go through their setters);
        the adjective base enforced this and the adverb base did not, but the
        reference behaviors in both trees did — so we keep the stricter rule,
        which is a no-op for plain fields.
        """
        if key in ("reference_noun", "filters"):
            raise ValueError(
                f"`{key}` must be modified through set_reference_noun() or set_filters()"
            )
        if isinstance(value, list):
            if not value:
                return
            self.data[key] = value[0]
        else:
            self.data[key] = str(value)

    def get_editable_fields(self) -> list[str]:
        """Return all non-protected keys in this descriptor."""
        protected = {
            # adjective-side protected keys
            "adjective", "adjective_class", "applies_to",
            "noun_type", "project_name", "verb_names",
            # adverb-side protected keys
            "adverb", "adverb_class", "verb_name", "verb",
        }
        return [k for k in self.data if k not in protected]

    def apply_filters_to_items(self, items: list[dict], filters: dict) -> list[dict]:
        """Filter items by case-insensitive string equality on key:value pairs."""
        if not filters:
            return items
        return [
            item for item in items
            if all(
                str(item.get(k, "")).lower() == str(v).lower()
                for k, v in filters.items()
            )
        ]

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        """Default: no validation errors. Behaviors override."""
        return []

    def get_field_value(self, key: str):
        return self.data.get(key)

    def update_field_value(self, key: str, value: str):
        self.set_field(key, value)

    # ---- hooks for behaviors ----
    def get_configurable_options(self) -> dict:
        return {}

    def requires_interaction(self) -> bool:
        return False

    def use_logic(self, **kwargs) -> dict | None:
        return None

    def configure_defaults(self):
        """Override in behaviors to apply class-specific config."""
        pass

    def interactive_edit(
        self,
        prompt_fn,
        save_fn=None,
    ) -> bool:
        """Core-safe field editor (merged from both bases).

        Args:
            prompt_fn: (editable_fields, context) -> (field_name, new_value) | None
            save_fn:   optional, called with updated ``self.data`` to persist.

        Returns True if edited, False if canceled/failed.
        """
        editable_fields = self.get_editable_fields()
        if not editable_fields:
            return False

        # Context carries both legacy key names so either GUI wrapper sees what it expects.
        context = {
            "adjective": self.data.get("adjective", "<unknown>"),
            "adverb": self.data.get("adverb", "<unknown>"),
            "noun_type": self.noun_type,
            "verb_name": self.verb_name,
            "current_values": {k: self.data.get(k) for k in editable_fields},
        }

        result = prompt_fn(editable_fields, context)
        if result is None:
            return False

        field_key, new_value = result
        self.set_field(field_key, new_value)

        if save_fn:
            try:
                save_fn(self.data)
            except Exception:
                log.warning("interactive_edit: save_fn failed", exc_info=True)
                return False

        return True
