import json
from pathlib import Path
from typing import Optional

# -----------------------------
# Load full schemas from file
# -----------------------------

def load_schema(project_path: Path, word_type: str) -> dict:
    """
    Load the full *_types.json schema file.

    word_type: one of 'noun', 'verb', 'adjective', 'adverb'
    """
    schema_path = project_path / f"{word_type}_types.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"{schema_path} not found.")
    # adjective/adverb are list-shaped today but migrate to a name-keyed dict; return the legacy
    # list from either shape so list-consumers (tools/view, tools/launch_adjective) keep working
    # across the migration. noun/verb are dicts — read them as-is.
    if word_type in ("adjective", "adverb"):
        from core.words.reader import load_descriptor_list
        return load_descriptor_list(project_path, word_type)
    return json.loads(schema_path.read_text())


def load_override(project_path: Path) -> list[dict]:
    """
    Load the override.json file (list of override instructions).
    """
    override_path = project_path / "override.json"
    if not override_path.exists():
        return []
    return json.loads(override_path.read_text())


# -----------------------------
# Schema lookup by top-level key
# -----------------------------

def get_noun_schema(project_path: Path, noun_name: str) -> Optional[dict]:
    """
    Return the schema dict for a given noun name (must match a top-level key).
    """
    schema = load_schema(project_path, "noun")
    return schema.get(noun_name)


def get_verb_schema(project_path: Path, verb_name: str) -> Optional[dict]:
    """
    Return the schema dict for a given verb name (must match a top-level key).
    """
    schema = load_schema(project_path, "verb")
    return schema.get(verb_name)


def get_adjective_schema(
    project_path: Path,
    adjective_name: str,
    applies_to: Optional[str] = None
) -> Optional[dict]:
    """
    Return the adjective schema where the identifying field 'adjective' matches exactly.

    Optionally filter by applies_to (e.g., "Sample").

    🚨 Only matches on the 'adjective' field, not any nested fields.
    """
    schema_list = load_schema(project_path, "adjective")
    candidates = [
        entry for entry in schema_list
        if entry.get("adjective") == adjective_name
    ]

    if applies_to:
        for entry in candidates:
            if applies_to in entry.get("applies_to", []):
                return entry
        return None
    else:
        return candidates[0] if candidates else None


def get_adverb_schema(
    project_path: Path,
    adverb_name: str,
    applies_to: Optional[str] = None
) -> Optional[dict]:
    """
    Return the adverb schema where the identifying field 'adverb' matches exactly.

    Optionally filter by applies_to (e.g., 'Sample') if that field is present.

    🚨 Only matches on the 'adverb' field, not nested values.
    """
    schema_list = load_schema(project_path, "adverb")

    candidates = [
        entry for entry in schema_list
        if entry.get("adverb") == adverb_name
    ]

    if applies_to:
        for entry in candidates:
            if applies_to in entry.get("applies_to", []):
                return entry
        return None
    else:
        return candidates[0] if candidates else None


def get_override_schema(project_path: Path, run_id: str) -> list[dict]:
    """
    Return override entries in override.json where 'run' == run_id.

    🚨 This ONLY matches on the top-level 'run' field.
    """
    overrides = load_override(project_path)
    return [entry for entry in overrides if entry.get("run") == run_id]

def find_non_id_field_value(project_path: Path, search_value: str) -> list[dict]:
    """
    Search all *_types.json files for entries where a non-identifying field
    has a value that matches `search_value`.

    Ignores the key in noun/verb types, and the 'adjective'/'adverb' field in adjective/adverb types.

    Returns a list of dicts:
        {
            "word_type": "adjective",
            "schema_name": "Analyte Type",
            "match_path": "reference_noun",
            "matched_value": "Analyte Types",
            "schema": <full schema dict>
        }
    """

    def walk(obj, skip_keys: set, path=""):
        matches = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in skip_keys:
                    continue
                new_path = f"{path}.{k}" if path else k
                matches += walk(v, skip_keys, new_path)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                new_path = f"{path}[{i}]"
                matches += walk(v, skip_keys, new_path)
        elif obj == search_value:
            matches.append(path)
        return matches

    results = []

    for word_type in ["noun", "verb", "adjective", "adverb"]:
        try:
            schema = load_schema(project_path, word_type)
        except Exception:
            continue

        if isinstance(schema, dict):  # noun_types or verb_types
            for schema_name, entry in schema.items():
                matches = walk(entry, skip_keys=set())
                for path in matches:
                    results.append({
                        "word_type": word_type,
                        "schema_name": schema_name,
                        "match_path": path,
                        "matched_value": search_value,
                        "schema": entry
                    })

        elif isinstance(schema, list):  # adjective_types or adverb_types
            for entry in schema:
                id_field = "adjective" if word_type == "adjective" else "adverb"
                schema_name = entry.get(id_field, "(unknown)")
                matches = walk(entry, skip_keys={id_field})
                for path in matches:
                    results.append({
                        "word_type": word_type,
                        "schema_name": schema_name,
                        "match_path": path,
                        "matched_value": search_value,
                        "schema": entry
                    })

    return results

def find_in_override_by_non_id_field_value(project_path: Path, search_value: str) -> list[dict]:
    """
    Search override.json for any entries where a non-identifying field (not 'run') has a value matching `search_value`.

    Returns a list of:
        {
            "match_path": "field" or "field[index]" (for list matches),
            "matched_value": ...,
            "entry": full override row
        }
    """
    import json

    path = project_path / "override.json"
    if not path.exists():
        return []

    try:
        entries = json.loads(path.read_text())
    except Exception:
        return []

    results = []

    for entry in entries:
        for key, val in entry.items():
            if key == "run":
                continue

            if isinstance(val, str) and val == search_value:
                results.append({
                    "match_path": key,
                    "matched_value": val,
                    "entry": entry
                })
            elif isinstance(val, list) and search_value in val:
                idx = val.index(search_value)
                results.append({
                    "match_path": f"{key}[{idx}]",
                    "matched_value": search_value,
                    "entry": entry
                })

    return results

# -----------------------------
# Noun + Verb file references
# -----------------------------

def get_noun_items(project_path: Path, noun_type: str) -> Path:
    """
    Return the path to nouns/(noun_type)/items.jsonl
    """
    path = project_path / "nouns" / noun_type / "items.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"items.jsonl not found for noun: {noun_type}")
    return path


def get_verb_data_entry(project_path: Path, verb_group: str, run_id: str) -> Path:
    """
    Return the path to verbs/(verb_group)/data_dumps/(run_id)/DataEntry.json
    """
    path = project_path / "verbs" / verb_group / "data_dumps" / run_id / "DataEntry.json"
    if not path.exists():
        raise FileNotFoundError(f"DataEntry.json not found for run: {run_id} in {verb_group}/data_dumps")
    return path

def resolve_run_id_to_test_type(project_path: Path, run_id: str) -> Optional[str]:
    """
    Given a run_ID, search through all *_log.jsonl files in verbs/(group)/
    to find the test_type associated with it.
    """
    verbs_dir = project_path / "verbs"
    if not verbs_dir.exists():
        raise FileNotFoundError(f"verbs directory not found in {project_path}")

    for group_path in verbs_dir.iterdir():
        if group_path.is_dir():
            log_path = group_path / f"{group_path.name}_log.jsonl"
            if log_path.exists():
                with log_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            if entry.get("run_ID") == run_id:
                                return entry.get("test_type")
                        except json.JSONDecodeError:
                            continue  # ignore malformed lines
    return None

def resolve_verb_group_from_test_type(project_path: Path, test_type: str) -> str:
    """
    Given a test_type (e.g., 'Micro_Test'), resolve which verb group it belongs to
    by loading its schema and returning the 'verb_group' field.

    Defaults to 'Tests' if the field is missing.
    """
    schema = get_verb_schema(project_path, test_type)
    if not schema:
        raise ValueError(f"Verb schema for {test_type} not found.")
    return schema.get("verb_group", "Tests")

def resolve_noun_type_from_override(project_path: Path, override_entry: dict) -> str | None:
    verb_name = override_entry.get("verb")
    if not verb_name:
        return None
    verb_schema = get_verb_schema(project_path, verb_name)
    return verb_schema.get("data_entry_schema", {}).get("set_up_inputs", {}).get("noun_type_ref")