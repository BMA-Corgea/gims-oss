# core/adverbs/tag_adverb.py

from core.handlers.adverbs.base_adverb import BaseAdverb

class TagAdverb(BaseAdverb):
    """
    Tag adverbs define a discrete set of allowed tag values, each with:
      - value (string)
      - explanation (tooltip / description)
      - display_in_label (bool, whether to show in the entity label/ID)
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
        """
        Expected format:
        [
          {"value": str, "explanation": str, "display_in_label": bool},
          ...
        ]
        """
        if not isinstance(options, list):
            raise TypeError("valid_options must be a list of dicts")

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
            validated.append({
                "value": value,
                "explanation": str(opt.get("explanation", "")),
                "display_in_label": bool(opt.get("display_in_label", False)),
            })

        self.data["valid_options"] = validated

    def get_configurable_options(self) -> dict:
        return {
            "definition": self.get_definition(),
            "valid_options": self.get_valid_options()
        }

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        errors = []
        field = self.data.get("adverb")
        if not field:
            return errors

        allowed = {opt["value"] for opt in self.get_valid_options()}
        for entry in entries:
            val = entry.get(field)
            if val is None:
                continue
            if isinstance(val, list):
                for v in val:
                    if v not in allowed:
                        errors.append(f"Invalid tag '{v}' for field '{field}'")
            else:
                if val not in allowed:
                    errors.append(f"Invalid tag '{val}' for field '{field}'")

        return errors

    def get_display_suffix(self, value: str) -> str:
        for opt in self.get_valid_options():
            if opt.get("value") == value and opt.get("display_in_label"):
                return f" ({value})"
        return ""

    def configure_defaults(self):
        self.data["definition"] = ""
        self.data["valid_options"] = []
