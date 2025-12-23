# core/adverbs/picture_adverb.py

from core.handlers.adverbs.base_adverb import BaseAdverb

class PictureAdverb(BaseAdverb):
    def get_configurable_options(self) -> dict:
        return {}

    def configure_defaults(self):
        pass

    def set_field(self, key: str, value: str):
        self.data[key] = value

    def use_logic(self, **kwargs) -> dict:
        return {
            "image_path": self.data.get(self.data.get("adverb"))
        }

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        return []
