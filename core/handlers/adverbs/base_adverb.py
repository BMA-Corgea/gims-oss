# core/adverbs/base.py

from typing import Optional

class BaseAdverb:
    def __init__(
        self,
        data: dict,
        verb_name: Optional[str] = None,
        project_name: Optional[str] = None
    ):
        self.data = data
        self.verb_name = verb_name
        self.project_name = project_name

    def set_field(self, key: str, value):
        """Set a field value safely. Subclasses may enforce constraints."""
        # normalize list → single string
        if isinstance(value, list):
            if not value:
                return
            self.data[key] = value[0]
        else:
            self.data[key] = str(value)

    def get_editable_fields(self) -> list[str]:
        """Return all non-protected keys in this adverb."""
        protected = {
            "adverb", "adverb_class",
            "verb_name", "project_name"
        }
        return [k for k in self.data if k not in protected]

    def apply_filters_to_items(self, items: list[dict], filters: dict) -> list[dict]:
        """Filter items by equality checks on key:value pairs."""
        if not filters:
            return items
        return [
            item for item in items
            if all(str(item.get(k, "")).lower() == str(v).lower()
                   for k, v in filters.items())
        ]

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        """Default: no validation errors. Subclasses can override."""
        return []

    def get_field_value(self, key: str):
        return self.data.get(key)

    def update_field_value(self, key: str, value: str):
        self.set_field(key, value)

    # Hooks for subclasses
    def get_configurable_options(self) -> dict:
        return {}

    def requires_interaction(self) -> bool:
        return False

    def use_logic(self, **kwargs) -> dict | None:
        return None

    def configure_defaults(self):
        pass

    def interactive_edit(
        self,
        prompt_fn: callable,
        save_fn: Optional[callable] = None
    ) -> bool:
        """
        Core-safe editor for adverb fields.
        - prompt_fn: (editable_fields: list[str], context: dict) → (field_name, new_value) or None
        - save_fn: optional function to persist updated self.data
        """
        editable_fields = self.get_editable_fields()
        if not editable_fields:
            return False

        context = {
            "adverb": self.data.get("adverb", "<unknown>"),
            "verb_name": self.verb_name,
            "current_values": {k: self.data.get(k) for k in editable_fields}
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
                return False

        return True
