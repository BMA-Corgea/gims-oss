from utils.logger import get_logger

log = get_logger(__name__)


def resolve_parents(
    noun_type: str,
    instance: dict,
    noun_schema: dict,
    adjective_schemas: dict[str, dict],      # expect a dict here!
    all_noun_items: dict[str, list[dict]],
    all_noun_schemas: dict[str, dict],
    seen_ids=None
) -> list[dict]:
    if seen_ids is None:
        seen_ids = set()

    parents = []
    pk_field_self = noun_schema.get("primary_id_field", "(no pk)")
    log.debug(f"\n[resolve_parents] noun_type={noun_type}, self PK field='{pk_field_self}', instance ID={instance.get(pk_field_self)}")

    # DEBUG: inspect the whole adjective_schemas structure
    log.debug(f"[resolve_parents] adjective_schemas (type={type(adjective_schemas)}) keys={list(adjective_schemas.keys())}")

    for field_name, field_def in noun_schema.get("fields", {}).items():
        if field_def.get("type") != "adjective":
            continue

        log.debug(f"\n  -> Field '{field_name}' is adjective; field_def={field_def!r}")

        # 1) Attempt to .get() the schema for this adjective field
        try:
            adj_schema = adjective_schemas.get(field_name)
        except Exception as e:
            log.debug(f"    !!! ERROR: adjective_schemas.get('{field_name}') -> {e!r}")
            raise
        log.debug(f"    -> adj_schema (type={type(adj_schema)}) = {adj_schema!r}")

        if not adj_schema:
            log.debug(f"    X no schema for adjective '{field_name}'")
            continue
        if adj_schema.get("adjective_class") != "Reference":
            log.debug(f"    X adjective_class != Reference (found {adj_schema.get('adjective_class')!r})")
            continue

        ref_noun_type = adj_schema.get("reference_noun")
        log.debug(f"    -> reference_noun = {ref_noun_type!r}")
        if not ref_noun_type:
            continue

        ref_inst_id = instance.get(field_name)
        log.debug(f"    -> instance['{field_name}'] = {ref_inst_id!r}")
        if not ref_inst_id or not isinstance(ref_inst_id, str):
            continue

        ref_schema = all_noun_schemas.get(ref_noun_type)
        log.debug(f"    -> ref_schema for {ref_noun_type!r} (type={type(ref_schema)}) = {ref_schema!r}")
        if not ref_schema:
            continue

        pk_field = ref_schema.get("primary_id_field")
        log.debug(f"    -> ref_schema primary_id_field = {pk_field!r}")
        if not pk_field:
            continue

        # verify ActionRequirement field...
        ar_field = None
        for f, f_def in ref_schema.get("fields", {}).items():
            if f_def.get("type") == "adjective" and f_def.get("adjective_class") == "ActionRequirement":
                ar_field = f
                break
        log.debug(f"    -> resolved ar_field = {ar_field!r}")
        if not ar_field:
            continue

        for candidate in all_noun_items.get(ref_noun_type, []):
            if candidate.get(pk_field) != ref_inst_id:
                continue

            key = (ref_noun_type, ref_inst_id)
            if key in seen_ids:
                continue
            seen_ids.add(key)

            ar_value = candidate.get(ar_field)
            log.debug(f"    -> candidate[{ar_field}] = {ar_value!r}")
            if not ar_value:
                continue

            log.debug(f"     Found parent {key} with AR={ar_value!r}")
            parent_entry = {
                "noun_type": ref_noun_type,
                "noun_id": ref_inst_id,
                "action_requirement": ar_value,
                "siblings": []
            }

            grandparents = resolve_parents(
                noun_type=ref_noun_type,
                instance=candidate,
                noun_schema=ref_schema,
                adjective_schemas=adjective_schemas,
                all_noun_items=all_noun_items,
                all_noun_schemas=all_noun_schemas,
                seen_ids=seen_ids
            )
            parents.append(parent_entry)
            parents.extend(grandparents)

    log.debug(f"[resolve_parents] returning {len(parents)} parent(s)\n")
    return parents
