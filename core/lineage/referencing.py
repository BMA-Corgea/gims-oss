from utils.logger import get_logger

log = get_logger(__name__)


def analyze_referencing_runs(
    noun_instance_id: str,
    referenced_noun_type: str,
    noun_schema: dict,
    verb_schemas: dict[str, dict],
    adjective_schemas: list[dict],
    all_noun_schemas: dict[str, dict],
    all_noun_items: dict[str, list[dict]],
    run_id_map: dict[str, str],
) -> tuple[dict[str, dict], list[dict]]:
    """
    Finds all runs that reference a given noun instance via ActionRequirement adjectives.

    Returns:
    - run_map: dict[run_id] = { run_id, verb, referencing_nouns }
    - flat_referencing_nouns: list[dict] — all noun entries that referenced the instance
    """
    log.debug(f"\n[analyze_referencing_runs] start for {referenced_noun_type} ID: {noun_instance_id}")
    results = {}
    flat_referencing_nouns = []

    # Step 1: Find all adjective fields in this noun schema that are ActionRequirement
    adjective_fields = [
        (field_name, field_spec["adjective_class"])
        for field_name, field_spec in noun_schema.get("fields", {}).items()
        if field_spec.get("type") == "adjective"
        and field_spec.get("adjective_class") == "ActionRequirement"
    ]
    log.debug(f"[analyze_referencing_runs] AR fields: {[f[0] for f in adjective_fields]}")

    for field_name, _adj_class in adjective_fields:
        # Step 2: Find matching adjective schema from global adjective list
        adj_schema = next(
            (a for a in adjective_schemas
             if a.get("adjective") == field_name and
             referenced_noun_type in a.get("applies_to", [])),
            None
        )
        if not adj_schema:
            log.debug(f"[analyze_referencing_runs] skip {field_name}: no matching adjective schema applies to {referenced_noun_type}")
            continue

        # Step 3: Extract verbs that use this AR adjective
        verb_lists = adj_schema.get("request_options", {}).values()
        all_verbs = sorted({verb for lst in verb_lists for verb in lst})
        log.debug(f"[analyze_referencing_runs] field '{field_name}' links to verbs: {all_verbs}")

        for verb in all_verbs:
            verb_schema = verb_schemas.get(verb)
            if not verb_schema:
                log.debug(f"[analyze_referencing_runs] missing verb schema for {verb}, skipping")
                continue

            setup_inputs = verb_schema.get("data_entry_schema", {}).get("set_up_inputs", {})
            source_noun = setup_inputs.get("noun_type_ref")
            if not source_noun:
                log.debug(f"[analyze_referencing_runs] verb {verb} has no noun_type_ref, skipping")
                continue
            if source_noun not in all_noun_items:
                log.debug(f"[analyze_referencing_runs] no items found for source noun type: {source_noun}")
                continue

            items = all_noun_items[source_noun]
            pk_field = all_noun_schemas.get(source_noun, {}).get("primary_id_field", "")

            log.debug(f"[analyze_referencing_runs] scanning {len(items)} {source_noun} item(s) for references to {noun_instance_id} via any field")

            for row in items:
                match = any(
                    v == noun_instance_id
                    if isinstance(v, str) else
                    noun_instance_id in v
                    if isinstance(v, list) else
                    False
                    for v in row.values()
                )
                if not match:
                    continue

                run_id = row.get("_runID")
                if not run_id:
                    log.debug(f"[analyze_referencing_runs] ! matched row has no _runID, skipping: {row}")
                    continue

                resolved_verb = run_id_map.get(run_id, verb) or verb

                annotated = dict(row)
                annotated["_noun_type"] = source_noun
                annotated["_primary_id_field"] = pk_field
                annotated["_runID"] = run_id

                flat_referencing_nouns.append(annotated)

                if run_id not in results:
                    results[run_id] = {
                        "run_id": run_id,
                        "verb": resolved_verb,
                        "referencing_nouns": []
                    }
                    log.debug(f"[analyze_referencing_runs]  new referencing run: {run_id} via verb '{resolved_verb}'")
                else:
                    log.debug(f"[analyze_referencing_runs] appending to existing run: {run_id}")

                results[run_id]["referencing_nouns"].append(annotated)

    log.debug(f"[analyze_referencing_runs] done. runs={len(results)}, noun_records={len(flat_referencing_nouns)}")
    return results, flat_referencing_nouns
