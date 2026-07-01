# utils/monitoring.py

from pathlib import Path
from api.i_o import load_schema  # S3-aware *_types.json reader (replaces json.load(open(...)))
from utils.status import get_status_breakdown, render_status_bar
from utils.status_ui import print_colored_status


def check_next_step(project_path: Path, source_noun_type: str, source_id: str, required_verb: str):
    """
    Traverse from a source noun (e.g., Submission) through its ActionRequirement to find linked items,
    returning the linked noun ID and associated run ID (if any).

    Returns:
      List of dicts: {"linked_id": str, "run_id": str | None}
    """
    # 1. Load verb definition
    verb_config = load_schema(project_path, "verb")
    if required_verb not in verb_config:
        raise ValueError(f"Verb '{required_verb}' not defined.")
    verb_def = verb_config[required_verb]

    # 2. Determine the noun type this verb acts on
    noun_ref = (
        verb_def
        .get("data_entry_schema", {})
        .get("set_up_inputs", {})
        .get("noun_type_ref")
    )
    if not noun_ref:
        raise ValueError(f"Verb '{required_verb}' missing noun_type_ref.")

    # 3. Load the child noun's schema to find primary ID and reference fields
    noun_defs = load_schema(project_path, "noun")
    noun_schema = noun_defs.get(noun_ref, {})
    primary_field = noun_schema.get("primary_id_field")
    if not primary_field:
        raise ValueError(f"Noun '{noun_ref}' missing primary_id_field.")

    # Collect all Reference / ReferenceList adjective fields linking back to the source noun type
    reference_fields = [
        fname
        for fname, fdef in noun_schema.get("fields", {}).items()
        if fdef.get("type") == "adjective" and fdef.get("adjective_class") in ("Reference", "ReferenceList")
    ]
    if not reference_fields:
        raise ValueError(
            f"Noun '{noun_ref}' has no Reference or ReferenceList adjectives to {source_noun_type}"
        )

    # 4. Scan child noun items and collect matches
    results = []
    from api.i_o import get_noun_items  # instances-first, with legacy JSONL fallback
    for obj in get_noun_items(project_path, noun_ref):
        if not isinstance(obj, dict):
            continue
        for field in reference_fields:
            val = obj.get(field)
            if val == source_id or (isinstance(val, list) and source_id in val):
                results.append({"linked_id": obj.get(primary_field), "run_id": obj.get("_runID")})
                break

    return results


def evaluate_condition(
    project_path: Path,
    run_id: str,
    verb_group: str,
    noun_schema: dict = None,
    raw_inputs: list[str] = None,
    adverb_schema: dict = None
):
    """
    Always recalculate and persist the status breakdown for a specific run.
    Reads or regenerates Status.json, then displays detailed status information.
    """
    run_path = project_path / "verbs" / verb_group / "data_dumps" / run_id

    # Always recalculate breakdown
    breakdown = get_status_breakdown(
        run_path,
        noun_schema=noun_schema,
        raw_inputs=raw_inputs,
        adverb_schema=adverb_schema,
        project_path=project_path
    )

    print(f"📊 Detailed Status for run {run_id} (verb group: {verb_group}):")
    print_colored_status(breakdown)
    print(render_status_bar(breakdown))
    return breakdown