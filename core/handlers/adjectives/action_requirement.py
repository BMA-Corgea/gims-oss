# core/adjectives/action_requirement.py

from core.handlers.adjectives.base import BaseAdjective

class ActionRequirementAdjective(BaseAdjective):

    def get_request_options(self) -> dict[str, list[str]]:
        return self.data.get("request_options", {}) or {}

    def set_request_options(self, new_options: dict[str, list[str]]):
        """Directly set the request options dictionary."""
        for label, verbs in new_options.items():
            if not isinstance(label, str) or not isinstance(verbs, list):
                raise TypeError("Invalid request_options format")
            for verb in verbs:
                if verb not in self.verb_names:
                    raise ValueError(f"Invalid verb: '{verb}' not found in known verbs")
        self.data["request_options"] = new_options

    def get_configurable_options(self) -> dict:
        return {
            "request_options": self.get_request_options()
        }

    def get_valid_request_labels(self) -> list[str]:
        return list(self.get_request_options().keys())

    def get_verbs_for_request(self, label: str) -> list[str]:
        return self.get_request_options().get(label, [])

    def validate_entries(self, entries: list[dict], context: dict | None = None) -> list[str]:
        """Ensure all entries have valid request label values."""
        valid = self.get_valid_request_labels()
        key = self.data.get("adjective")
        return [
            f"Invalid request label '{entry.get(key)}'" for entry in entries
            if entry.get(key) not in valid
        ]

    def use_logic(self, noun_schema: dict, verb_defs: dict, instance: dict, **kwargs) -> dict:
        return {
            "status": "follow-up",
            "requests": self.show_request_status(
                noun_schema=noun_schema,
                verb_defs=verb_defs,
                instance=instance,
                project_path=kwargs.get("project_path"),
                noun_type=kwargs.get("noun_type")
            )
        }

    def configure_defaults(self):
        self.data["request_options"] = {}

    def interactive_configure(self):
        """No-op for core. CLI should override this manually in the tool layer."""
        pass
