"""The six descriptor behaviors, merged from the adjective/adverb twins.

Each behavior is the union of a ``*Adjective`` and its ``*Adverb`` counterpart:

  TagBehavior              <- TagAdjective              + TagAdverb
  ReferenceBehavior        <- ReferenceAdjective        + ReferenceAdverb
  ReferenceListBehavior    <- ReferenceListAdjective    + ReferenceListAdverb
  PictureBehavior          <- PictureAdjective          + PictureAdverb
  ActionRequirementBehavior<- ActionRequirementAdjective  (noun-only)
  AttributeBehavior        <- AttributeAdverb              (verb-only)

The twins differ only by (a) which entry key names the descriptor
("adjective" vs "adverb"), and (b) the display-flag name (display_in_id vs
display_in_label). Both are resolved via :class:`WordHandler` context, so the
behavior body is written once.

``Descriptor`` is the single concrete handler. It picks a behavior by class name
and is *keyed by context*: the same behavior runs for a noun- or a verb-attached
descriptor, only the target differs.
"""
from __future__ import annotations

from datetime import datetime

from core.words.handlers.base import WordHandler


# ──────────────────────────────────────────────────────────────────────────────
# Behaviors
# ──────────────────────────────────────────────────────────────────────────────
class _Behavior:
    """Mixin marker for behavior strategies. Methods assume a ``WordHandler`` host
    (``self.data``, ``self.identity_field``, ``self.display_flag``, etc.)."""


class TagBehavior(_Behavior):
    """Discrete set of allowed tag values, each with value/explanation/display flag.

    Merge of TagAdjective + TagAdverb. The display flag name differs per context
    (``display_in_id`` for nouns, ``display_in_label`` for verbs); both are read
    via ``self.display_flag``, and BOTH are written on ``set_valid_options`` so an
    entry stays readable by either legacy tree.
    """

    def get_definition(self) -> str:
        return self.data.get("definition", "")

    def set_definition(self, definition: str):
        if not isinstance(definition, str):
            raise TypeError("definition must be a string")
        self.data["definition"] = definition

    def get_valid_options(self) -> list[dict]:
        return self.data.get("valid_options", []) or []

    def set_valid_options(self, options: list[dict]):
        if not isinstance(options, list):
            raise TypeError("valid_options must be a list of dicts")

        flag = self.display_flag
        validated = []
        seen = set()
        for opt in options:
            if not isinstance(opt, dict):
                raise TypeError("Each option must be a dict")
            value = opt.get("value")
            if not isinstance(value, str) or not value:
                raise ValueError("Each option must have a non-empty string 'value'")
            if value in seen:
                raise ValueError(f"Duplicate option value: {value}")
            seen.add(value)
            # Accept either drifted flag on input; write both so the option is
            # readable by both legacy trees.
            show = bool(opt.get("display_in_id", opt.get("display_in_label", False)))
            validated.append({
                "value": value,
                "explanation": str(opt.get("explanation", "")),
                "display_in_id": show,
                "display_in_label": show,
                flag: show,
            })

        self.data["valid_options"] = validated

    def get_configurable_options(self) -> dict:
        return {
            "definition": self.get_definition(),
            "valid_options": self.get_valid_options(),
        }

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        errors: list[str] = []
        field = self.get_self_name()
        if not field:
            return errors

        allowed = {opt["value"] for opt in self.get_valid_options()}
        for entry in entries:
            val = entry.get(field)
            if val is None:
                continue
            if isinstance(val, list):
                for bad in (v for v in val if v not in allowed):
                    errors.append(f"Invalid tag '{bad}' for field '{field}'")
            else:
                if val not in allowed:
                    errors.append(f"Invalid tag '{val}' for field '{field}'")

        return errors

    def get_display_suffix(self, value: str) -> str:
        """Return ' (VALUE)' if this option is flagged to show in the id/label."""
        flag = self.display_flag
        for opt in self.get_valid_options():
            if opt.get("value") == value and (
                opt.get(flag) or opt.get("display_in_id") or opt.get("display_in_label")
            ):
                return f" ({value})"
        return ""

    def configure_defaults(self):
        self.data["definition"] = ""
        self.data["valid_options"] = []

    def interactive_configure(self, *args, **kwargs):
        """No-op for core. CLI/GUI wrappers collect definition + options."""
        return


class ReferenceBehavior(_Behavior):
    """Single-noun reference. Merge of ReferenceAdjective + ReferenceAdverb."""

    def set_field(self, key: str, value):
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

    def get_reference_noun(self) -> str:
        ref = self.data.get("reference_noun")
        if isinstance(ref, list):  # legacy safeguard
            return ref[0] if ref else self.target_name
        return ref or self.target_name

    def set_reference_noun(self, noun_type: str):
        if not isinstance(noun_type, str):
            raise TypeError("reference_noun must be a single string")
        self.data["reference_noun"] = noun_type

    def get_filters(self) -> dict:
        return self.data.get("filters", {}) or {}

    def set_filters(self, filters: dict):
        self.data["filters"] = filters

    def get_configurable_options(self) -> dict:
        return {
            "reference_noun": self.get_reference_noun(),
            "filters": self.get_filters(),
        }

    def use_logic(self, noun_items: list[dict] | None = None, **kwargs) -> dict:
        ref_noun = self.get_reference_noun()
        filters = self.get_filters()
        entries = noun_items or []

        entries = self.apply_filters_to_items(entries, filters)
        id_key = next((k for k in entries[0].keys() if k.endswith("_id")), None) if entries else None
        if not id_key and entries:
            id_key = next(iter(entries[0].keys()))

        options = []
        for entry in entries:
            if id_key and id_key in entry:
                options.append({"value": str(entry[id_key]), "noun_type": ref_noun})

        return {
            "reference_options": options,
            "filters": filters,
            "reference_noun": ref_noun,
        }

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        errors: list[str] = []
        field = self.get_self_name()
        if not field or not context:
            return []

        options = self.use_logic(context["noun_items"]).get("reference_options", [])
        valid_values = {opt["value"] for opt in options}

        for entry in entries:
            val = entry.get(field)
            if val and val not in valid_values:
                errors.append(f"'{val}' not in valid references for field '{field}'")

        return errors

    def configure_defaults(self):
        self.data["reference_noun"] = self.target_name
        self.data["filters"] = {}

    def interactive_configure(self, prompt_func=None, noun_defs: dict | None = None):
        """Caller-driven noun + filter selection (adjective-tree version)."""
        if not prompt_func or noun_defs is None:
            return

        noun_types = list(noun_defs.keys())

        idx = prompt_func(noun_types, "Select noun type to reference")
        if idx is None:
            return
        noun = noun_types[idx]
        self.set_reference_noun(noun)

        filters: dict = {}
        field_keys = list(noun_defs[noun]["fields"].keys())
        while True:
            idx = prompt_func(field_keys, "Select noun field to filter (blank to finish)")
            if idx is None:
                break
            key = field_keys[idx]
            val = prompt_func([], f"Enter filter value for '{key}'")
            if isinstance(val, str) and "," in val:
                filters[key] = [v.strip() for v in val.split(",") if v.strip()]
            else:
                filters[key] = val

        if filters:
            self.set_filters(filters)


class ReferenceListBehavior(_Behavior):
    """Multi-noun reference list. Merge of ReferenceListAdjective + ReferenceListAdverb.

    The twins drifted on the storage key: the adjective stored ``reference_noun``
    (a list) while the adverb preferred ``reference_nouns`` and fell back to
    ``reference_noun``. We absorb that drift ONCE in :meth:`normalize_reference_nouns`,
    called on construction, so a single ``reference_noun`` string is reconciled to a
    canonical list under the canonical key and the legacy key is kept in sync.
    """

    # Canonical storage key (the adjective-tree key) + the adverb-tree alias.
    _CANON_KEY = "reference_noun"
    _ALIAS_KEY = "reference_nouns"

    def normalize_reference_nouns(self) -> None:
        """Coerce str-or-list under either key into a list under BOTH keys.

        Idempotent. Run once on construction so callers of either tree see a list.
        """
        ref = self.data.get(self._CANON_KEY)
        if ref is None:
            ref = self.data.get(self._ALIAS_KEY)
        if ref is None:
            return  # nothing to normalize yet (defaults applied later)

        if isinstance(ref, str):
            values = [ref]
        elif isinstance(ref, list):
            values = list(ref)
        else:
            values = [ref]

        self.data[self._CANON_KEY] = values
        self.data[self._ALIAS_KEY] = values

    def set_field(self, key: str, value):
        if key in ("reference_noun", "filters"):
            raise ValueError(
                f"`{key}` must be modified through set_reference_noun() or set_filters()"
            )
        if isinstance(value, str):
            values = [v.strip() for v in value.split(",") if v.strip()]
        elif isinstance(value, list):
            values = value
        else:
            values = [value]
        self.data[key] = values

    def get_reference_noun(self) -> list[str]:
        ref = self.data.get(self._CANON_KEY)
        if ref is None:
            ref = self.data.get(self._ALIAS_KEY)
        if isinstance(ref, str):
            return [ref]
        return ref or []

    def set_reference_noun(self, noun_types: str | list[str]):
        if isinstance(noun_types, str):
            values = [noun_types]
        elif isinstance(noun_types, list):
            values = noun_types
        else:
            raise TypeError("reference_noun must be str or list[str]")
        # Keep both keys in sync so either legacy tree reads it.
        self.data[self._CANON_KEY] = values
        self.data[self._ALIAS_KEY] = values

    def get_filters(self) -> dict:
        return self.data.get("filters", {}) or {}

    def set_filters(self, filters: dict):
        self.data["filters"] = filters

    def get_configurable_options(self) -> dict:
        return {
            "reference_noun": self.get_reference_noun(),
            "filters": self.get_filters(),
        }

    def use_logic(self, noun_items_map: dict[str, list[dict]] | None = None, **kwargs) -> dict:
        noun_items_map = noun_items_map or {}
        ref_nouns = self.get_reference_noun()
        filters = self.get_filters()
        combined = []

        for noun in ref_nouns:
            entries = noun_items_map.get(noun, [])
            if not entries:
                continue

            entries = self.apply_filters_to_items(entries, filters)
            id_key = next((k for k in entries[0].keys() if k.endswith("_id")), None) if entries else None
            if not id_key and entries:
                id_key = next(iter(entries[0].keys()))

            for entry in entries:
                if id_key and id_key in entry:
                    combined.append({"value": str(entry[id_key]), "noun_type": noun})

        return {
            "reference_list_options": combined,
            "filters": filters,
            "reference_nouns": ref_nouns,
        }

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        errors: list[str] = []
        field = self.get_self_name()
        if not field or not context:
            return []

        options = self.use_logic(context["noun_items_map"]).get("reference_list_options", [])
        valid_values = {opt["value"] for opt in options}

        for entry in entries:
            for val in entry.get(field, []):
                if val not in valid_values:
                    errors.append(f"'{val}' not in valid references for field '{field}'")

        return errors

    def configure_defaults(self):
        self.set_reference_noun([self.target_name])
        self.data["filters"] = {}

    def interactive_configure(self, prompt_func=None, noun_defs: dict | None = None):
        """Caller-driven multi-noun + filter selection (adjective-tree version)."""
        if not prompt_func or noun_defs is None:
            return

        noun_types = list(noun_defs.keys())

        selected_nouns: list[str] = []
        while True:
            idx = prompt_func(noun_types, "Select noun type to reference (blank to finish)")
            if idx is None:
                break
            noun = noun_types[idx]
            if noun not in selected_nouns:
                selected_nouns.append(noun)

        if selected_nouns:
            self.set_reference_noun(selected_nouns)

            filters: dict = {}
            field_keys = list(noun_defs[selected_nouns[0]]["fields"].keys())
            while True:
                idx = prompt_func(field_keys, "Select noun field to filter (blank to finish)")
                if idx is None:
                    break
                key = field_keys[idx]
                val = prompt_func([], f"Enter filter value for '{key}'")
                if isinstance(val, str) and "," in val:
                    filters[key] = [v.strip() for v in val.split(",") if v.strip()]
                else:
                    filters[key] = val

            if filters:
                self.set_filters(filters)


class PictureBehavior(_Behavior):
    """Image path / capture result. Merge of PictureAdjective + PictureAdverb."""

    def get_configurable_options(self) -> dict:
        return {}

    def configure_defaults(self):
        return

    def interactive_configure(self, *args, **kwargs):
        return

    def set_field(self, key: str, value):
        # No normalization — store the raw image path/capture result verbatim.
        self.data[key] = value

    def use_logic(self, **kwargs) -> dict:
        name = self.get_self_name()
        return {"image_path": self.data.get(name)}

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        return []


class ActionRequirementBehavior(_Behavior):
    """Follow-up request options keyed by label → verbs. Noun-attached only.

    From ActionRequirementAdjective (no adverb counterpart).
    """

    def get_request_options(self) -> dict[str, list[str]]:
        return self.data.get("request_options", {}) or {}

    def set_request_options(self, new_options: dict[str, list[str]]):
        for label, verbs in new_options.items():
            if not isinstance(label, str) or not isinstance(verbs, list):
                raise TypeError("Invalid request_options format")
            for verb in verbs:
                if verb not in self.verb_names:
                    raise ValueError(f"Invalid verb: '{verb}' not found in known verbs")
        self.data["request_options"] = new_options

    def get_configurable_options(self) -> dict:
        return {"request_options": self.get_request_options()}

    def get_valid_request_labels(self) -> list[str]:
        return list(self.get_request_options().keys())

    def get_verbs_for_request(self, label: str) -> list[str]:
        return self.get_request_options().get(label, [])

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        valid = self.get_valid_request_labels()
        key = self.get_self_name()
        return [
            f"Invalid request label '{entry.get(key)}'"
            for entry in entries
            if entry.get(key) not in valid
        ]

    def use_logic(self, noun_schema: dict, verb_defs: dict, instance: dict, **kwargs) -> dict:
        return {
            "status": "follow-up",
            "requests": self.show_request_status(
                noun_schema=noun_schema,
                verb_defs=verb_defs,
                instance=instance,
                project_path=kwargs.get("project_path"),
                noun_type=kwargs.get("noun_type"),
            ),
        }

    def configure_defaults(self):
        self.data["request_options"] = {}

    def interactive_configure(self, *args, **kwargs):
        """No-op for core. CLI overrides this in the tool layer."""
        return


class AttributeBehavior(_Behavior):
    """Single freeform value: string / number / date. Verb-attached only.

    From AttributeAdverb (no adjective counterpart). Also the target of the
    ``StateContext`` alias in the adverb dispatch map.
    """

    def get_field_type(self) -> str:
        return self.data.get("field_type", "string")

    def set_field_type(self, ftype: str):
        if ftype not in ("string", "number", "date"):
            raise ValueError("field_type must be 'string', 'number', or 'date'")
        self.data["field_type"] = ftype

    def get_format(self) -> str | None:
        return self.data.get("format")

    def set_format(self, fmt: str):
        if self.get_field_type() != "date":
            raise ValueError("format can only be set when field_type='date'")
        if fmt not in ("mmddyy", "mmddyyyy", "yyyy-mm-dd"):
            raise ValueError("Invalid date format")
        self.data["format"] = fmt

    def is_required(self) -> bool:
        return bool(self.data.get("required", False))

    def set_required(self, required: bool):
        self.data["required"] = bool(required)

    def get_configurable_options(self) -> dict:
        return {
            "field_type": self.get_field_type(),
            "format": self.get_format(),
            "required": self.is_required(),
        }

    def configure_defaults(self):
        self.data["field_type"] = "string"
        self.data["required"] = False

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        errors: list[str] = []
        field = self.get_self_name()
        if not field:
            return errors

        ftype = self.get_field_type()
        fmt = self.get_format()

        for entry in entries:
            val = entry.get(field)
            if val is None:
                if self.is_required():
                    errors.append(f"Field '{field}' is required but missing")
                continue

            sval = str(val).strip()

            if ftype == "number":
                try:
                    float(sval)
                except ValueError:
                    errors.append(f"Field '{field}' must be a number (got '{sval}')")
            elif ftype == "date":
                if not self._is_valid_date(sval, fmt or "yyyy-mm-dd"):
                    errors.append(
                        f"Field '{field}' must match date format '{fmt}' (got '{sval}')"
                    )

        return errors

    def _is_valid_date(self, value: str, fmt: str) -> bool:
        patterns = {
            "mmddyy": "%m%d%y",
            "mmddyyyy": "%m%d%Y",
            "yyyy-mm-dd": "%Y-%m-%d",
        }
        try:
            datetime.strptime(value, patterns[fmt])
            return True
        except Exception:
            return False

    def use_logic(self, **kwargs) -> dict:
        field = self.get_self_name()
        return {field: self.data.get(field)} if field else {}


class DurationBehavior(_Behavior):
    """Time-aware descriptor: binds TWO date/datetime fields of the SAME noun record and
    renders a live interval between them and the clock. Noun-attached only.

    This is the first descriptor that links two *sibling* fields of its own record
    (``start_field`` + ``end_field``) and the first whose field is **virtual** — it stores
    no entered value. ``mode`` selects which reading is shown; the two readings the user
    cares about ("how long has it been here?" / "how long until it's due?") are the same
    subtraction, differing only by operand order / sign:

        elapsed   = now − start_field        ("in for …")
        remaining = end_field − now          ("… left", negative ⇒ overdue)
        both      = show elapsed · remaining

    The authoritative clock is the server's ``core.compliance.now_iso_ms()`` (the same UTC
    clock that stamps the audit trail); the per-second tick is interpolated client-side off
    a server-anchored monotonic clock, so the browser clock never decides the value. Raising
    an alert on overdue is NOT this descriptor's job — that is composed from other adjectives
    (e.g. a Tag) reading the interval; here the interval is display-only.
    """

    _VALID_MODES = ("elapsed", "remaining", "both")
    _VALID_UNITS = ("auto", "days", "hours", "minutes", "seconds")

    def get_start_field(self) -> str | None:
        return self.data.get("start_field") or None

    def set_start_field(self, name: str):
        self.data["start_field"] = str(name)

    def get_end_field(self) -> str | None:
        return self.data.get("end_field") or None

    def set_end_field(self, name: str):
        self.data["end_field"] = str(name)

    def get_mode(self) -> str:
        mode = self.data.get("mode", "elapsed")
        return mode if mode in self._VALID_MODES else "elapsed"

    def set_mode(self, mode: str):
        if mode not in self._VALID_MODES:
            raise ValueError(f"mode must be one of {self._VALID_MODES}")
        self.data["mode"] = mode

    def get_unit(self) -> str:
        unit = self.data.get("unit", "auto")
        return unit if unit in self._VALID_UNITS else "auto"

    def get_configurable_options(self) -> dict:
        return {
            "start_field": self.get_start_field(),
            "end_field": self.get_end_field(),
            "mode": self.get_mode(),
            "unit": self.get_unit(),
            "overdue_style": self.data.get("overdue_style", "negative"),
        }

    def configure_defaults(self):
        self.data["mode"] = "elapsed"
        self.data["unit"] = "auto"

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        """A Duration field is virtual/derived — it must NOT carry a stored value. (An empty
        string / None is fine; the grid seeds blanks.) The binding's structural validity
        against the noun schema is checked at config time by the workbench, not here."""
        field = self.get_self_name()
        if not field:
            return []
        errors: list[str] = []
        for entry in entries:
            val = entry.get(field)
            if val not in (None, ""):
                errors.append(
                    f"Field '{field}' is a computed duration and cannot hold a value (got {val!r})"
                )
        return errors

    def use_logic(self, **kwargs) -> dict:
        return {
            "start_field": self.get_start_field(),
            "end_field": self.get_end_field(),
            "mode": self.get_mode(),
            "unit": self.get_unit(),
        }


# ──────────────────────────────────────────────────────────────────────────────
# Descriptor — the one concrete handler
# ──────────────────────────────────────────────────────────────────────────────
# Behavior strategy by canonical class name (aliases are mapped in __init__.py).
_BEHAVIORS: dict[str, type[_Behavior]] = {
    "Tag": TagBehavior,
    "Reference": ReferenceBehavior,
    "ReferenceList": ReferenceListBehavior,
    "Picture": PictureBehavior,
    "ActionRequirement": ActionRequirementBehavior,
    "Attribute": AttributeBehavior,
    "Duration": DurationBehavior,
}


class Descriptor(WordHandler):
    """The single unified descriptor handler.

    Selects a behavior strategy by ``behavior_name`` (a canonical class name) and
    applies it over a :class:`WordHandler` context. "Adjective on a noun" and
    "adverb on a verb" are the SAME handler with ``attaches_to`` differing.

    Behavior methods are mixed in dynamically so this stays a plain handler while
    delegating class-specific logic to the chosen strategy.
    """

    # Behaviors that need a one-time normalization pass on construction.
    _NORMALIZE_ON_INIT = {"ReferenceList"}

    def __init__(self, data: dict, *, behavior_name: str, **kwargs):
        super().__init__(data, **kwargs)
        behavior_cls = _BEHAVIORS.get(behavior_name)
        if behavior_cls is None:
            raise KeyError(f"Unknown descriptor behavior: {behavior_name!r}")
        self.behavior_name = behavior_name
        self._behavior = behavior_cls()
        # Bind behavior methods onto this instance (the behavior reads self.data, etc.).
        self._bind_behavior(behavior_cls)
        if behavior_name in self._NORMALIZE_ON_INIT:
            # str-vs-list reference coercion, absorbed once at construction.
            self.normalize_reference_nouns()

    def _bind_behavior(self, behavior_cls: type[_Behavior]) -> None:
        """Copy the behavior's public members onto this instance.

        Methods become bound methods on the instance (overriding WordHandler
        defaults); non-callable class attributes (e.g. ReferenceList's storage-key
        constants) are copied as plain instance attributes so the bound methods can
        read them.
        """
        for attr in dir(behavior_cls):
            if attr.startswith("__"):
                continue
            member = getattr(behavior_cls, attr)
            # Never shadow a WordHandler property with a behavior member.
            if isinstance(getattr(type(self), attr, None), property):
                continue
            if callable(member):
                # bind the plain function to THIS Descriptor instance
                setattr(self, attr, member.__get__(self, type(self)))
            else:
                # class-level data attribute (constant) → copy as instance attr
                setattr(self, attr, member)


__all__ = [
    "Descriptor",
    "TagBehavior",
    "ReferenceBehavior",
    "ReferenceListBehavior",
    "PictureBehavior",
    "ActionRequirementBehavior",
    "AttributeBehavior",
    "DurationBehavior",
]
