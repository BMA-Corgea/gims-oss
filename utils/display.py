from pathlib import Path
import json
from utils import disambiguation as dis


def load_noun_schema(project_path: Path, noun_type: str) -> dict:
    path = project_path / "noun_types.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get(noun_type, {})
    except Exception:
        return {}

def format_display_id(entry: dict, noun_type: str, project_path: Path) -> str:
    """
    Return the primary ID for a noun instance, with any tag-style adjective suffixes appended
    in the format: ID (tag1)(tag2)... based on `display_in_id` config in adjective_types.json.
    """

    noun_schema = dis.get_noun_schema(project_path, noun_type)
    if not noun_schema:
        return "(unknown noun)"

    id_field = noun_schema.get("primary_id_field", f"{noun_type.lower()}_id")
    id_val = entry.get(id_field, "")

    suffixes: list[str] = []

    for field_name, field_def in noun_schema.get("fields", {}).items():
        if field_def.get("type") == "adjective" and field_def.get("adjective_class") == "Tag":
            value = entry.get(field_name)
            if not value:
                continue

            adj_schema = dis.get_adjective_schema(project_path, field_name, applies_to=noun_type)
            if not adj_schema:
                continue

            valid_options = adj_schema.get("valid_options", [])
            for opt in valid_options:
                if opt.get("value") == value and opt.get("display_in_id"):
                    suffixes.append(f"({value})")
                    break

    return f"{id_val}{' ' + ''.join(suffixes) if suffixes else ''}"