# core/adverbs/reference_adverb.py

from core.handlers.adverbs.base_adverb import BaseAdverb

class ReferenceAdverb(BaseAdverb):
    def set_field(self, key: str, value):
        if key in ("reference_noun", "filters"):
            raise ValueError(f"`{key}` must be modified through set_reference_noun() or set_filters()")
        if isinstance(value, list):
            if not value:
                return
            self.data[key] = value[0]
        else:
            self.data[key] = str(value)

    def get_reference_noun(self) -> str:
        ref = self.data.get("reference_noun")
        if isinstance(ref, list):
            return ref[0] if ref else self.verb_name
        return ref or self.verb_name

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

    def use_logic(self, noun_items: list[dict], **kwargs) -> dict:
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
        errors = []
        field = self.data.get("adverb")
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
        self.data["reference_noun"] = self.verb_name
        self.data["filters"] = {}
