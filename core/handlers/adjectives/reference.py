# core/adjectives/reference.py

from core.handlers.adjectives.base import BaseAdjective

class ReferenceAdjective(BaseAdjective):
    def set_field(self, key: str, value):
        if key in ("reference_noun", "filters"):
            raise ValueError(f"`{key}` must be modified through set_reference_noun() or set_filters()")
        # accept string or list but always normalize to a single string
        if isinstance(value, list):
            if not value:
                return
            self.data[key] = value[0]
        else:
            self.data[key] = str(value)

    def get_reference_noun(self) -> str:
        ref = self.data.get("reference_noun")
        if isinstance(ref, list):  # legacy safeguard
            return ref[0] if ref else self.noun_type
        return ref or self.noun_type

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
            "filters": self.get_filters()
        }

    # ⬇️ single‑noun version of use_logic ⬇️
    def use_logic(self, noun_items: list[dict], **kwargs) -> dict:
        """
        noun_items: list of item dicts for the single reference noun,
        provided by the API layer
        """
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
                options.append({
                    "value": str(entry[id_key]),
                    "noun_type": ref_noun
                })

        return {
            "reference_options": options,
            "filters": filters,
            "reference_noun": ref_noun
        }

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        """Validate that values in this field appear in the allowed options."""
        errors = []
        field = self.data.get("adjective")
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
        self.data["reference_noun"] = self.noun_type
        self.data["filters"] = {}

    def interactive_configure(self, prompt_func=None, noun_defs: dict | None = None):
        """
        Let the caller drive both noun‐selection and filter‐selection.
        Non-interactive callers pass prompt_func=None.

        Args:
            prompt_func: function to prompt the user, or None to skip
            noun_defs: preloaded noun schema dict (from i_o.load_schema(project_path, "noun"))
        """
        if not prompt_func or noun_defs is None:
            return

        noun_types = list(noun_defs.keys())

        # 1) pick exactly one reference noun
        idx = prompt_func(noun_types, "Select noun type to reference")
        if idx is None:
            return
        noun = noun_types[idx]
        self.set_reference_noun(noun)

        # 2) pick filters on that noun
        filters: dict[str, any] = {}
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
