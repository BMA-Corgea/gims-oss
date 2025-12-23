# core/adverbs/attribute_adverb.py

from core.handlers.adverbs.base_adverb import BaseAdverb
from datetime import datetime

class AttributeAdverb(BaseAdverb):
    """
    Adverb that holds a single freeform value: string, number, or date.

    Config keys:
      - field_type: "string" | "number" | "date"
      - format: (only if date) one of ["mmddyy","mmddyyyy","yyyy-mm-dd"]
      - required: bool
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
            "required": self.is_required()
        }

    def configure_defaults(self):
        self.data["field_type"] = "string"
        self.data["required"] = False

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        """Validate all values in the field against its configured type."""
        errors = []
        field = self.data.get("adverb")
        if not field:
            return errors

        ftype = self.get_field_type()
        fmt   = self.get_format()

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
                    errors.append(f"Field '{field}' must match date format '{fmt}' (got '{sval}')")

        return errors

    def _is_valid_date(self, value: str, fmt: str) -> bool:
        patterns = {
            "mmddyy": "%m%d%y",
            "mmddyyyy": "%m%d%Y",
            "yyyy-mm-dd": "%Y-%m-%d"
        }
        try:
            datetime.strptime(value, patterns[fmt])
            return True
        except Exception:
            return False

    def use_logic(self, **kwargs) -> dict:
        """At runtime, just surface the field value as-is."""
        field = self.data.get("adverb")
        return {field: self.data.get(field)} if field else {}
