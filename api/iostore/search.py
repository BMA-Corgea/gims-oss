# api/iostore/search.py -- split out of api/i_o.py (wiring-neutral). Non-id field search.
from __future__ import annotations
import json
from pathlib import Path
from api.json_proxy import read_text
from .schema import load_schema
from utils.logger import get_logger

log = get_logger(__name__)


def find_non_id_field_value(
    project_path: Path,
    search_value: str,
    word_type: str | list[str] | None = None
) -> list[dict]:
    """
    Search one or more *_types.json files for entries where a non-identifying field
    has a value that matches `search_value`.
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

    valid_types = {"noun", "verb", "adjective", "adverb"}
    types_to_search = (
        [word_type] if isinstance(word_type, str)
        else word_type if isinstance(word_type, list)
        else ["noun", "verb", "adjective", "adverb"]
    )

    for wt in types_to_search:
        if wt not in valid_types:
            continue

        try:
            schema = load_schema(project_path, wt)
        except Exception:
            log.debug(f"[find_non_id_field_value] failed to load {wt} schema; skipping", exc_info=True)
            continue

        if isinstance(schema, dict):  # noun_types or verb_types
            for schema_name, entry in schema.items():
                matches = walk(entry, skip_keys=set())
                for path in matches:
                    results.append({
                        "word_type": wt,
                        "schema_name": schema_name,
                        "match_path": path,
                        "matched_value": search_value,
                        "schema": entry
                    })

        elif isinstance(schema, list):  # adjective_types or adverb_types
            id_field = "adjective" if wt == "adjective" else "adverb"
            for entry in schema:
                schema_name = entry.get(id_field, "(unknown)")
                matches = walk(entry, skip_keys={id_field})
                for path in matches:
                    results.append({
                        "word_type": wt,
                        "schema_name": schema_name,
                        "match_path": path,
                        "matched_value": search_value,
                        "schema": entry
                    })

    return results

def find_in_override_by_non_id_field_value(project_path: Path, search_value: str) -> list[dict]:
    """
    Search override.json for any entries where a non-identifying field (not 'run') 
    has a value matching `search_value`. (S3-AWARE)
    """
    path = project_path / "override.json"
    try:
        payload = read_text(path, encoding="utf-8")
        entries = json.loads(payload)
    except Exception:
        log.warning("[find_in_override_by_non_id_field_value] failed to read/parse override.json", {"path": str(path)}, exc_info=True)
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
