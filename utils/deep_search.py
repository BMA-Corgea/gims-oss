# utils/deep_search.py

from pathlib import Path
import json
import glob

def normalize_string(s: str) -> str:
    """
    Lowercase and remove all whitespace for basic fuzzy matching.
    """
    return "".join(s.lower().split())


def deep_search(search_term: str, project_path) -> list:
    """
    Returns a list of match dicts with 'type' and 'data'.
    """
    project_path = Path(project_path)  # ensure Path type

    matches = []
    normalized_search = normalize_string(search_term)

    # 1) Schema search
    schema_match = search_schema_definitions(search_term, project_path, normalized_search)
    if schema_match:
        matches.append({
            'type': schema_match['type'],
            'data': schema_match['schema']
        })

    # 2) Noun instances
    noun_instances = search_noun_instances(search_term, normalized_search, project_path)
    matches.extend(noun_instances)

    # 3) Verb run logs
    verb_runs = search_verb_logs(search_term, normalized_search, project_path)
    matches.extend(verb_runs)

    # 4) Adjective/adverb value usage
    adj_adv_usages = search_adjective_or_adverb_values(search_term, normalized_search, project_path)
    matches.extend(adj_adv_usages)

    # 5) Attribute value general search
    attribute_values = search_attribute_values(search_term, normalized_search, project_path)
    matches.extend(attribute_values)

    # 6) Deduplicate noun_instance entries
    matches = deduplicate_matches(matches, project_path)

    return matches

def search_schema_definitions(search_term: str, project_path: Path, normalized_search: str) -> dict:
    """
    Searches all *_types.json files for schema definitions matching search_term.
    Partial, case-insensitive, and normalized matches.
    """
    project_path = Path(project_path)  # enforce Path type

    for part in ['noun', 'verb', 'adjective', 'adverb']:
        path = project_path / f"{part}_types.json"
        if not path.exists():
            continue

        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            continue

        for key, value in data.items():
            if not isinstance(key, str):
                continue
            key_norm = normalize_string(key)
            if (search_term.lower() in key.lower()
                or normalized_search == key_norm
                or normalized_search in key_norm):
                return {'type': f"{part}_schema", 'schema': value}

    return None

def search_noun_instances(search_term: str, normalized_search: str, project_path: Path) -> list:
    """
    Searches all noun items.jsonl files for instances matching search_term.
    Matches on primary_id_field (if defined) or any field via partial/fuzzy logic.
    """
    matches = []
    # load noun types to get primary_id_field for each noun_type
    noun_types_path = project_path / "noun_types.json"
    noun_types = json.loads(noun_types_path.read_text()) if noun_types_path.exists() else {}

    from api.i_o import get_noun_items  # instances-first, with legacy JSONL fallback
    for noun_type in noun_types.keys():
        primary_id_field = noun_types.get(noun_type, {}).get("primary_id_field")
        for raw in get_noun_items(project_path, noun_type):
            if not isinstance(raw, dict):
                continue
            item = {**raw, '_noun_type': noun_type}

            # 1) primary ID matching
            if primary_id_field:
                pid = str(item.get(primary_id_field, ""))
                pid_norm = normalize_string(pid)
                if (search_term.lower() in pid.lower()
                    or normalized_search == pid_norm
                    or normalized_search in pid_norm):
                    matches.append({'type': 'noun_instance', 'data': item})
                    continue

            # 2) any field matching
            for v in item.values():
                v_str = str(v)
                v_norm = normalize_string(v_str)
                if (search_term.lower() in v_str.lower()
                    or normalized_search == v_norm
                    or normalized_search in v_norm):
                    matches.append({'type': 'noun_instance', 'data': item})
                    break

    return matches

def search_verb_logs(search_term: str, normalized_search: str, project_path: Path) -> list:
    """
    Searches all verb *_log.jsonl files for run_ID or any field matching search_term.
    """
    matches = []
    for file in glob.glob(str(project_path / "verbs/*/*_log.jsonl")):
        verb_group = Path(file).stem.replace("_log", "")
        with open(file) as f:
            for line in f:
                run = json.loads(line)
                run['_verb_group'] = verb_group

                # 1) run_ID matching
                run_id = str(run.get("run_ID", ""))
                run_norm = normalize_string(run_id)
                if (search_term.lower() in run_id.lower()
                    or normalized_search == run_norm
                    or normalized_search in run_norm):
                    matches.append({'type': 'verb_run_instance', 'data': run})
                    continue

                # 2) any field matching
                for v in run.values():
                    v_str = str(v)
                    v_norm = normalize_string(v_str)
                    if (search_term.lower() in v_str.lower()
                        or normalized_search == v_norm
                        or normalized_search in v_norm):
                        matches.append({'type': 'verb_run_instance', 'data': run})
                        break

    return matches

def search_adjective_or_adverb_values(search_term: str, normalized_search: str, project_path: Path) -> list:
    """
    Reverse lookup: finds any noun instance containing the search_term as a value.
    """
    matches = []
    noun_types_path = project_path / "noun_types.json"
    noun_types = json.loads(noun_types_path.read_text()) if noun_types_path.exists() else {}
    from api.i_o import get_noun_items  # instances-first, with legacy JSONL fallback
    for noun_type in noun_types.keys():
        for raw in get_noun_items(project_path, noun_type):
            if not isinstance(raw, dict):
                continue
            item = {**raw, '_noun_type': noun_type}
            for v in item.values():
                v_str = str(v)
                v_norm = normalize_string(v_str)
                if (search_term.lower() in v_str.lower()
                    or normalized_search == v_norm
                    or normalized_search in v_norm):
                    matches.append({'type': 'noun_instance', 'data': item})
                    break

    return matches

def search_attribute_values(search_term: str, normalized_search: str, project_path: Path) -> list:
    """
    General attribute search across all noun instances (same as reverse lookup).
    """
    # identical to adjective/adverb reverse lookup
    return search_adjective_or_adverb_values(search_term, normalized_search, project_path)

def explain_schema(schema: dict, type_name: str) -> str:
    """
    Converts any schema dict to a human-readable explanation.
    """
    lines = [f"🗂️ **{type_name.capitalize()} Schema**"]

    if 'description' in schema:
        lines.append(f"📄 Description: {schema['description']}")

    if 'fields' in schema:
        lines.append("\n🔑 **Fields:**")
        for name, info in schema['fields'].items():
            lines.append(f"  • {name}: {info}")

    if schema.get('primary_id_field'):
        lines.append(f"\n🆔 **Primary ID Field:** {schema['primary_id_field']}")

    if schema.get('autogenerate_id'):
        lines.append("⚙️ **Autogenerate ID:** Enabled")
        if 'autogenerate_segments' in schema:
            lines.append("  Segments:")
            for seg in schema['autogenerate_segments']:
                lines.append(f"   - {seg}")

    return "\n".join(lines)

def explain_instance(instance_row: dict) -> str:
    """
    Formats a noun or verb-run instance into aesthetic CLI output.
    """
    noun_type = instance_row.get('_noun_type')
    verb_group = instance_row.get('_verb_group')

    if noun_type:
        header = f"🧾 **Noun Instance: {noun_type.capitalize()}**"
    elif verb_group:
        header = f"🧾 **Verb Group: {verb_group.capitalize()}**"
    else:
        header = "🧾 **Instance**"

    lines = [header]
    for k, v in instance_row.items():
        if k not in ['_noun_type', '_verb_group']:
            lines.append(f"  • {k}: {v}")

    return "\n".join(lines)

def deduplicate_matches(matches: list, project_path: Path) -> list:
    """
    Removes duplicate noun_instance entries based on their configured primary_id_field.
    """
    seen = set()
    unique = []

    # Load noun_types schema for primary_id_field lookup
    noun_types_path = project_path / "noun_types.json"
    noun_types = json.loads(noun_types_path.read_text()) if noun_types_path.exists() else {}

    for m in matches:
        if m['type'] == 'noun_instance':
            item = m['data']
            noun_type = item.get('_noun_type')
            pid_field = noun_types.get(noun_type, {}).get('primary_id_field')

            if pid_field:
                pid = item.get(pid_field)
                if pid and (noun_type, pid) not in seen:
                    seen.add((noun_type, pid))
                    unique.append(m)
            else:
                unique.append(m)
        else:
            unique.append(m)

    return unique
