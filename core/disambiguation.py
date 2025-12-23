# api/disambiguation.py

import json
from pathlib import Path
from api.manifest.resolver import resolve_path
from api.i_o import load_schema

def resolve_run_id_to_test_type(project_path: Path, run_id: str) -> str | None:
    """
    Search through all verb group logs to find which test_type a run_ID belongs to.
    Returns the matching test_type string or None if not found.
    """
    verbs_dir = project_path / "verbs"
    if not verbs_dir.exists():
        raise FileNotFoundError(f"verbs directory not found in {project_path}")

    for group_path in verbs_dir.iterdir():
        if group_path.is_dir():
            verb_group = group_path.name
            try:
                log_path = resolve_path(project_path, "verb_group_log", verb_group=verb_group)
                with log_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            if entry.get("run_ID") == run_id:
                                return entry.get("test_type")
                        except json.JSONDecodeError:
                            continue
            except FileNotFoundError:
                continue
    return None


def resolve_verb_group_from_test_type(project_path: Path, test_type: str) -> str:
    """
    Return the verb_group that a given test_type belongs to, based on its schema.
    Defaults to 'Tests' if missing.
    """
    schema = load_schema(project_path, "verb")
    verb_schema = schema.get(test_type)
    if not verb_schema:
        raise ValueError(f"Verb schema for {test_type} not found.")
    return verb_schema.get("verb_group", "Tests")


def resolve_noun_type_from_override(project_path: Path, override_entry: dict) -> str | None:
    """
    Given an override entry, return the noun_type that the referenced verb operates on.
    """
    verb_name = override_entry.get("verb")
    if not verb_name:
        return None
    schema = load_schema(project_path, "verb")
    verb_schema = schema.get(verb_name)
    if not verb_schema:
        return None
    return verb_schema.get("data_entry_schema", {}).get("set_up_inputs", {}).get("noun_type_ref")
