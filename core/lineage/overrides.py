from utils.logger import get_logger

log = get_logger(__name__)


def resolve_overrides(
    referencing_nouns: list[dict],
    noun_instance: dict | None,
    overrides: list[dict],
    verb_data: dict[tuple[str, str], list[dict]],  # (verb_group, run_id) -> DataEntry rows
    noun_schemas: dict[str, dict],
    verb_group_map: dict[str, str],                # test_type -> verb_group
    noun_type_map: dict[str, str],                 # test_type -> noun_type_ref
    seen_run_ids: set = None
) -> list[dict]:
    """
    Pure logic version. Expects overrides and verb data passed in.
    """
    if seen_run_ids is None:
        seen_run_ids = set()

    retests = []

    def process_one(noun: dict):
        noun_type = noun.get("_noun_type")
        pk_field = noun.get("_primary_id_field")
        val = noun.get(pk_field)
        run_id = noun.get("_runID")

        if not all([noun_type, pk_field, val, run_id]):
            return

        run_id_norm = str(run_id).strip().lower()

        for override in overrides:
            target_run = override.get("run")
            if not target_run or target_run in seen_run_ids or target_run == run_id:
                continue

            matched = False
            for k, v in override.items():
                if k in {"run", "verb"}:
                    continue
                vals = v if isinstance(v, list) else [v]
                vals_norm = [str(x).strip().lower() for x in vals]
                if run_id_norm in vals_norm:
                    seen_run_ids.add(target_run)
                    matched = True
                    break

            if not matched:
                continue

            test_type = override.get("verb")
            if not test_type:
                continue

            verb_group = verb_group_map.get(test_type)
            if not verb_group:
                continue

            data = verb_data.get((verb_group, target_run), [])
            if isinstance(data, dict):
                data = [data]

            match_found = any(
                isinstance(entry.get(field), str) and entry.get(field) == val
                for entry in data for field in entry
            )
            if not match_found:
                continue

            retests.append({
                "noun_instance": {
                    "_noun_type": "Override",
                    "_primary_id_field": "run",
                    "run": target_run
                },
                "retest_of": run_id
            })

            test_type = override.get("verb")
            new_noun_type = noun_type_map.get(test_type)
            if not new_noun_type:
                continue

            noun_schema = noun_schemas.get(new_noun_type)
            if not noun_schema:
                continue

            pk_field_inner = noun_schema.get("primary_id_field")
            if not pk_field_inner:
                continue

            new_nouns = [
                {
                    **entry,
                    "_noun_type": new_noun_type,
                    "_primary_id_field": pk_field_inner,
                    "_runID": target_run
                }
                for entry in data
                if pk_field_inner in entry
            ]

            retests.extend(resolve_overrides(
                referencing_nouns=new_nouns,
                noun_instance=None,
                overrides=overrides,
                verb_data=verb_data,
                noun_schemas=noun_schemas,
                verb_group_map=verb_group_map,
                noun_type_map=noun_type_map,
                seen_run_ids=seen_run_ids
            ))

    for ref in referencing_nouns:
        process_one(ref)
    if noun_instance:
        process_one(noun_instance)

    return retests
