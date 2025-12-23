# core/adjectives/base.py

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from typing import Optional
import json

class BaseAdjective:
    def __init__(
        self,
        data: dict,
        noun_type: Optional[str] = None,
        verb_types: Optional[dict] = None,
        project_name: Optional[str] = None
    ):
        self.data = data
        self.noun_type = noun_type
        self.verb_names = list(verb_types.keys()) if verb_types else []
        self.project_name = project_name

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

    def get_editable_fields(self) -> list[str]:
        """Return all non-protected keys in this adjective."""
        protected = {
            "adjective", "adjective_class", "applies_to",
            "noun_type", "project_name", "verb_names"
        }
        return [k for k in self.data if k not in protected]

    def apply_filters_to_items(self, items: list[dict], filters: dict) -> list[dict]:
        """Filter a list of items based on key:value filter matches (string equality)."""
        if not filters:
            return items
        return [
            item for item in items
            if all(str(item.get(k, "")).lower() == str(v).lower() for k, v in filters.items())
        ]

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        """Default: no validation errors. Subclasses can override."""
        return []

    def get_field_value(self, key: str):
        """Utility to read a field from adjective data."""
        return self.data.get(key)

    def update_field_value(self, key: str, value: str):
        """Update a field in the adjective data (same as set_field unless overridden)."""
        self.set_field(key, value)

    # Hooks for subclasses
    def get_configurable_options(self) -> dict:
        """Return a dict describing what this adjective can be configured with."""
        return {}

    def requires_interaction(self) -> bool:
        """Return True if this adjective needs prompting/config."""
        return False

    def use_logic(self, **kwargs) -> dict | None:
        return None

    def configure_defaults(self):
        """Override in subclasses to apply class-specific config."""
        pass

    def interactive_edit(
        self,
        prompt_fn: callable,
        save_fn: Optional[callable] = None
    ) -> bool:
        """
        Core-safe editor for adjective fields.

        Args:
            prompt_fn: (editable_fields: list[str], context: dict) → (field_name, new_value) or None
            save_fn: Optional. If provided, called with updated self.data to persist externally.

        Returns:
            True if edited successfully, False if canceled or failed.
        """
        editable_fields = self.get_editable_fields()
        if not editable_fields:
            return False

        context = {
            "adjective": self.data.get("adjective", "<unknown>"),
            "noun_type": self.noun_type,
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