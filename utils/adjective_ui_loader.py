# utils/adjective_ui_loader.py

import json
from pathlib import Path

def load_adjective_field_config(project_path: Path, noun_type: str) -> dict:
    """
    Load adjective configuration for a given noun_type.

    Returns a mapping of field_name -> {
        "adjective_class": str,
        "valid_options": list,
        "reference_noun": str (if Reference or ReferenceList),
        "unique_per_run": bool
    }
    """
    noun_types_file      = project_path / "noun_types.json"
    adjective_types_file = project_path / "adjective_types.json"
    noun_data_root       = project_path / "nouns"

    if not noun_types_file.exists() or not adjective_types_file.exists():
        return {}

    with open(noun_types_file) as f:
        noun_defs = json.load(f)

    if noun_type not in noun_defs:
        return {}

    # Read through the back-compat reader so this tolerates EITHER on-disk shape — the
    # legacy list OR the migrated name-keyed dict (Phase 3). A raw json.load would iterate
    # a migrated dict's keys (strings) and crash on ``a["adjective"]``.
    from core.words.reader import load_descriptor_list
    adjective_defs = load_descriptor_list(project_path, "adjective")  # list of entries

    adjective_map = {a["adjective"]: a for a in adjective_defs if "adjective" in a}
    result = {}

    for field_name, field_info in noun_defs[noun_type].get("fields", {}).items():
        if field_info.get("type") != "adjective":
            continue

        adjective_class = field_info.get("adjective_class")
        if not adjective_class:
            continue

        adjective_entry = adjective_map.get(field_name)
        if not adjective_entry:
            continue

        entry = {
            "adjective_class": adjective_class,
            "valid_options": [],
            "unique_per_run": bool(adjective_entry.get("unique_per_run", False))
        }

        if adjective_class in {"Reference", "ReferenceList"}:
            ref_noun = adjective_entry.get("reference_noun")
            entry["reference_noun"] = ref_noun
            if ref_noun:
                ref_items_file = noun_data_root / ref_noun / "items.jsonl"
                if ref_items_file.exists():
                    try:
                        with open(noun_types_file) as nf:
                            ref_noun_defs = json.load(nf)
                        ref_primary = ref_noun_defs.get(ref_noun, {}).get("primary_id_field")
                        if ref_primary:
                            with open(ref_items_file) as f:
                                items = [json.loads(line) for line in f if line.strip()]
                            entry["valid_options"] = [
                                item[ref_primary] for item in items if ref_primary in item
                            ]
                    except Exception as e:
                        print(f"⚠️ Error loading reference options for '{field_name}': {e}")

        elif adjective_class == "StateControl":
            entry["valid_options"] = adjective_entry.get("allowed_values", [])

        elif adjective_class == "Tag":
            # Only return the list of tag values, not the full dicts
            entry["valid_options"] = [
                opt.get("value")
                for opt in adjective_entry.get("valid_options", [])
                if "value" in opt
            ]

        result[field_name] = entry

    return result
