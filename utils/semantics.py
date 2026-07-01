import json
import os
from pathlib import Path
from utils.handlers.noun import NounType
from datetime import datetime

def get_display_name(key, part_of_speech, lang_config):
    """
    Retrieve human-readable display names from alias mappings.

    key: e.g., "Sample", "Test"
    part_of_speech: one of "nouns", "verbs", "adjectives", "adverbs"
    """
    alias_map = lang_config["aliases"].get(part_of_speech, {})
    if isinstance(alias_map, dict):
        return alias_map.get(key, key)
    return key

def load_json(path):
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def get_input(prompt: str, default: str = None) -> str:
    if default:
        response = input(f"{prompt} [{default}]: ").strip()
        return response if response else default
    return input(f"{prompt}: ").strip()


def confirm_list(prompt: str) -> list:
    response = input(f"{prompt} (comma-separated, leave blank if none): ").strip()
    return [x.strip() for x in response.split(",") if x.strip()]

def generate_project_selector(project_name: str | None) -> str:
    if project_name:
        return project_name

    project_folders = [f.name for f in Path("projects").iterdir() if f.is_dir()]
    if not project_folders:
        raise ValueError("❌ No project folders found in 'projects/'.")

    print("\n🗂 Available projects:")
    for i, name in enumerate(project_folders):
        print(f"[{i}] {name}")
    print("Select a project by number:")

    while True:
        choice = input("Project index (or 'q' to cancel): ").strip()
        if choice.lower() == 'q':
            print("❎ Cancelled.")
            exit()
        if choice.isdigit():
            index = int(choice)
            if 0 <= index < len(project_folders):
                return project_folders[index]
        print("❌ Invalid selection. Try again.")


def check_if_word_exists(project_name: str, word_type: str, word_key: str) -> bool:
    """
    Check if a noun, verb, or adjective key already exists in the project config.

    Parameters:
    - project_name: Folder name inside 'projects'
    - word_type: One of 'noun', 'verb', 'adjective'
    - word_key: The key you're trying to register

    Returns:
    - True if it already exists, False otherwise
    """
    path_map = {
        "noun": "noun_types.json",
        "verb": "verb_types.json",
        "adjective": "adjective_types.json"
    }

    if word_type not in path_map:
        raise ValueError(f"Unsupported word type: {word_type}")

    path = Path("projects") / project_name / path_map[word_type]

    if not path.exists():
        return False

    with open(path) as f:
        data = json.load(f)
        if isinstance(data, dict):
            return word_key in data
        if isinstance(data, list) and word_type == "adjective":
            return any(entry.get("adjective") == word_key for entry in data)

    return False

def infer_type(value: str):
    try:
        if value.lower() in ['true', 'false']:
            return value.lower() == 'true'
        elif '.' in value:
            return float(value)
        else:
            return int(value)
    except ValueError:
        return value.strip()


def is_valid_date(date_str: str, fmt: str = "mmddyy") -> bool:
    fmt_map = {
        "mmddyy": "%m%d%y",
        "mmddyyyy": "%m%d%Y",
        "yyyy-mm-dd": "%Y-%m-%d",
        # Add more as needed
    }
    try:
        datetime.strptime(date_str, fmt_map.get(fmt, "%m%d%y"))
        return True
    except ValueError:
        return False

def _editor_adjective_lookup(project_path: Path, noun_name: str):
    """Return ``get_adj(field) -> adjective entry dict | None`` for this noun, backed by the
    one back-compat reader (tolerates the list-or-dict on-disk shape)."""
    from core.words.reader import read_types
    adjs = read_types(project_path, "adjective")

    def get_adj(field: str):
        wt = adjs.get(field)
        if wt and (not wt.attaches_to or noun_name in wt.attaches_to):
            return wt.raw
        # collision suffix-keys (name#scope): match by base name + attached noun
        for w in adjs.values():
            if w.name == field and noun_name in w.attaches_to:
                return w.raw
        return None

    return get_adj


def validate_item_against_schema(item: dict, noun_type: NounType) -> list[str]:
    """
    Validate a single item dict against the noun_type schema.
    Returns a list of error strings (empty = valid).

    Thin adapter over the ONE validation engine (``core.words.validation.validate_instance``):
    the noun's ``type: "adjective"`` fields are resolved against ``adjective_types.json`` and
    references are resolved from the editor's JSONL store. Legacy error wording is preserved.
    """
    from core.words.resolve import resolve_noun_wordtype
    from core.words.id_provider import JsonlIdProvider
    from core.words.validation import validate_instance, render_legacy_errors

    project_path = Path(noun_type.project_path)
    get_adj = _editor_adjective_lookup(project_path, noun_type.name)
    wt = resolve_noun_wordtype(noun_type.name, noun_type.schema, get_adj)

    # Both legacy validators stripped string values before checking; preserve that.
    norm = {k: (v.strip() if isinstance(v, str) else v) for k, v in item.items()}
    findings = validate_instance(norm, wt, JsonlIdProvider(project_path))
    return render_legacy_errors(findings, wt)