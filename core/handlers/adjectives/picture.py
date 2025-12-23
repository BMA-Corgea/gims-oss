# core/adjectives/picture.py

from core.handlers.adjectives.base import BaseAdjective

class PictureAdjective(BaseAdjective):
    def get_configurable_options(self) -> dict:
        # no configurable options; just return empty dict
        return {}

    def configure_defaults(self):
        # no defaults beyond being an adjective field
        pass

    def interactive_configure(self, *args, **kwargs):
        # nothing to configure interactively
        return

    def set_field(self, key: str, value: str):
        """
        Set the image path or capture result.
        In GUI, this will be handled by the upload/capture workflow.
        """
        self.data[key] = value

    def use_logic(self, **kwargs) -> dict:
        # No special runtime logic, just echo current value
        return {
            "image_path": self.data.get(self.data.get("adjective"))
        }

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        # No validation required beyond being present
        return []