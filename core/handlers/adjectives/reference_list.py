# core/adjectives/reference_list.py

from core.handlers.adjectives.base import BaseAdjective

class ReferenceListAdjective(BaseAdjective):
    def set_field(self, key: str, value):
        if key in ("reference_noun", "filters"):
            raise ValueError(f"`{key}` must be modified through set_reference_noun() or set_filters()")
        # accept either a list or a comma-separated string
        if isinstance(value, str):
            values = [v.strip() for v in value.split(",") if v.strip()]
        elif isinstance(value, list):
            values = value
        else:
            values = [value]
        self.data[key] = values

    def get_reference_noun(self) -> list[str]:
        ref = self.data.get("reference_noun")
        if isinstance(ref, str):
            return [ref]
        return ref or []

    def set_reference_noun(self, noun_types: str | list[str]):
        if isinstance(noun_types, str):
            self.data["reference_noun"] = [noun_types]
        elif isinstance(noun_types, list):
            self.data["reference_noun"] = noun_types
        else:
            raise TypeError("reference_noun must be str or list[str]")

    def get_filters(self) -> dict:
        return self.data.get("filters", {}) or {}

    def set_filters(self, filters: dict):
        self.data["filters"] = filters

    def get_configurable_options(self) -> dict:
        return {
            "reference_noun": self.get_reference_noun(),
            "filters": self.get_filters()
        }

    # ⬇️ refactored: no file I/O, expects noun_items dicts to be passed in ⬇️
    def use_logic(self, noun_items_map: dict[str, list[dict]], **kwargs) -> dict:
        """
        noun_items_map: {noun_type: [list of item dicts]} provided by API layer
        """
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
                    combined.append({
                        "value": str(entry[id_key]),
                        "noun_type": noun
                    })

        return {
            "reference_list_options": combined,
            "filters": filters,
            "reference_nouns": ref_nouns
        }

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        """Validate that values in this field appear in the allowed options."""
        errors = []
        field = self.data.get("adjective")
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
        self.data["reference_noun"] = [self.noun_type]
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

        # 1) pick reference noun(s)
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

            # 2) pick filters on that noun
            filters: dict[str, any] = {}
            field_keys = list(noun_defs[selected_nouns[0]]["fields"].keys())
            while True:
                idx = prompt_func(field_keys, "Select noun field to filter (blank to finish)")
                if idx is None:
                    break
                key = field_keys[idx]
                val = prompt_func([], f"Enter filter value for '{key}'")
                # no infer_type here — keep raw input or split comma string if you want
                if isinstance(val, str) and "," in val:
                    filters[key] = [v.strip() for v in val.split(",") if v.strip()]
                else:
                    filters[key] = val

            if filters:
                self.set_filters(filters)