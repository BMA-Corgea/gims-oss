# core/adverbs/reference_list_adverb.py

from core.handlers.adverbs.base_adverb import BaseAdverb

class ReferenceListAdverb(BaseAdverb):
    def set_field(self, key: str, value):
        if key in ("reference_noun", "filters"):
            raise ValueError(f"`{key}` must be modified through set_reference_noun() or set_filters()")
        if isinstance(value, str):
            values = [v.strip() for v in value.split(",") if v.strip()]
        elif isinstance(value, list):
            values = value
        else:
            values = [value]
        self.data[key] = values

    def get_reference_noun(self) -> list[str]:
        # Accept both "reference_nouns" (preferred) and legacy "reference_noun"
        ref = self.data.get("reference_nouns")
        if ref is None:
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

    def use_logic(self, noun_items_map: dict[str, list[dict]], **kwargs) -> dict:
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
        errors = []
        field = self.data.get("adverb")
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
        self.data["reference_noun"] = [self.verb_name]
        self.data["filters"] = {}
