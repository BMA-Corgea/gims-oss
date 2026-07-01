from pathlib import Path
from utils.logger import get_logger

log = get_logger(__name__)

from .parents import resolve_parents
from .siblings import resolve_siblings
from .overrides import resolve_overrides
from .referencing import analyze_referencing_runs


def _grouped_verb_data(verb_data: dict[tuple[str, str], list[dict]]) -> dict[str, dict[str, list[dict]]]:
    grouped: dict[str, dict[str, list[dict]]] = {}
    for (group, run_id), entries in verb_data.items():
        grouped.setdefault(group, {})[run_id] = entries
    return grouped

def get_lineage(
    project_path: Path,
    noun_type: str,
    record: dict,
    items: list[dict],
    noun_schema: dict,
    all_noun_schemas: dict[str, dict],
    all_noun_items: dict[str, list[dict]],
    verb_schemas: dict[str, dict],
    adjective_schemas: list[dict],
    run_id_map: dict[str, str],  # run_id -> test_type
    override_entries: list[dict],
    verb_data: dict[tuple[str, str], list[dict]],
    precomputed_verb_group_map: dict[str, str],
    precomputed_noun_type_map: dict[str, str],
) -> dict:
    """
    Pure logic version of get_lineage.
    Expects all file content (items, schema) and resolver functions to be passed in.
    """

    log.debug(f"\n[get_lineage] start noun_type={noun_type}")
    log.debug(f"[get_lineage] record keys: {list(record.keys())}")

    def get_primary_id_field(schema: dict, noun_type: str) -> str:
        return schema.get("primary_id_field", f"{noun_type.lower()}_id")

    def find_matching_instance(
        items: list[dict],
        id_field: str,
        id_value: str,
        run_id: str | None = None
    ) -> dict | None:
        for row in items:
            if row.get(id_field) != id_value:
                continue
            if run_id and row.get("_runID") != run_id:
                continue
            return row
        return None

    pk_field = get_primary_id_field(noun_schema, noun_type)
    instance_id = record.get(pk_field)
    run_id = record.get("_runID")

    log.debug(f"[get_lineage] primary_id_field={pk_field!r}")
    log.debug(f"[get_lineage] seeking instance_id={instance_id!r} run_id={run_id!r}")

    target = find_matching_instance(items, pk_field, instance_id, run_id)
    if not target:
        log.debug("[get_lineage] X No matching instance found.")
        return {}

    log.debug(f"[get_lineage]  Found target instance: {target.get(pk_field)}")
    target["_noun_type"] = noun_type
    target["_primary_id_field"] = pk_field

    # Find referencing verb runs
    log.debug("[get_lineage] scanning referencing runs...")
    found_runs, referencing_nouns = analyze_referencing_runs(
        noun_instance_id=instance_id,
        referenced_noun_type=noun_type,
        noun_schema=noun_schema,
        verb_schemas=verb_schemas,
        adjective_schemas=adjective_schemas,
        all_noun_schemas=all_noun_schemas,
        all_noun_items=all_noun_items,
        run_id_map=run_id_map
    )
    log.debug(f"[get_lineage] referencing runs found: {len(found_runs)}")

    # Resolve parents
    log.debug("[get_lineage] resolving parents...")

    # 🔁 Convert list -> dict first
    adj_schema_map = {a["adjective"]: a for a in adjective_schemas}

    # Sanity check all argument types before entering resolve_parents
    log.debug("[get_lineage] resolve_parents sanity check:")
    log.debug(f"  noun_type={noun_type!r}")
    log.debug(f"  instance keys: {list(target.keys())}")
    log.debug(f"  noun_schema type={type(noun_schema)}")
    log.debug(f"  adjective_schemas(dict) keys={list(adj_schema_map.keys())}")
    log.debug(f"  all_noun_schemas keys={list(all_noun_schemas.keys())}")
    log.debug(f"  all_noun_items keys={list(all_noun_items.keys())}")

    parents = resolve_parents(
        noun_type=noun_type,
        instance=target,
        noun_schema=noun_schema,
        adjective_schemas=adj_schema_map,  #  dict now
        all_noun_items=all_noun_items,
        all_noun_schemas=all_noun_schemas
    )

    # Resolve siblings
    _grouped = _grouped_verb_data(verb_data)
    for parent in parents:
        p_type = parent["noun_type"]
        p_id = parent["noun_id"]
        ar = parent.get("action_requirement")
        log.debug(f"[get_lineage] resolving siblings for parent {p_type} -> {p_id} (AR: {ar})")

        siblings = resolve_siblings(
            parent_noun_type=p_type,
            parent_id=p_id,
            ar_value=ar,
            exclude_noun_type=noun_type,
            exclude_noun_id=instance_id,
            adjective_schemas=adjective_schemas,
            verb_schemas=verb_schemas,
            all_noun_schemas=all_noun_schemas,
            all_noun_items=all_noun_items
        )
        log.debug(f"[get_lineage] siblings found: {len(siblings)}")
        parent["siblings"] = siblings

    # Flatten referencing nouns
    referencing = []
    for run in found_runs.values():
        referencing.extend(run.get("referencing_nouns", []))
    log.debug(f"[get_lineage] collected {len(referencing)} referencing noun records")

    # Retest resolution
    log.debug("[get_lineage] checking for retests/overrides...")
    retests = resolve_overrides(
        referencing_nouns=referencing,
        noun_instance=target,
        overrides=override_entries,
        verb_data=verb_data,
        noun_schemas=all_noun_schemas,
        verb_group_map=precomputed_verb_group_map,
        noun_type_map=precomputed_noun_type_map
    )
    log.debug(f"[get_lineage] retests / overrides: {len(retests)}")

    result = {
        "noun": target,
        "noun_type": noun_type,
        "runs": list(found_runs.values()),
        "parents": parents,
        "retests": retests,
    }

    log.debug("[get_lineage]  done.")
    return result
