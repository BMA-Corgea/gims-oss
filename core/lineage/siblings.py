from utils.logger import get_logger

log = get_logger(__name__)


def resolve_siblings(
    parent_noun_type: str,
    parent_id: str,
    ar_value: str,
    exclude_noun_type: str,
    exclude_noun_id: str,
    adjective_schemas: list[dict],           # expect a list here!
    verb_schemas: dict[str, dict],
    all_noun_schemas: dict[str, dict],
    all_noun_items: dict[str, list[dict]]
) -> list[dict]:
    log.debug(f"\n[resolve_siblings] parent_noun_type={parent_noun_type}, parent_id={parent_id}, AR='{ar_value}'")

    # DEBUG: inspect the list and types in adjective_schemas
    log.debug(f"[resolve_siblings] adjective_schemas list length: {len(adjective_schemas)}")
    for i, adj in enumerate(adjective_schemas):
        log.debug(f"   idx={i} type={type(adj)} value={adj.get('adjective', '(no name)')}")

    # 1) find the AR adjective in the list
    ar_adj = next(
        (a for a in adjective_schemas
         if isinstance(a, dict)
         and a.get("adjective_class") == "ActionRequirement"
         and parent_noun_type in a.get("applies_to", [])),
        None
    )
    log.debug(f"[resolve_siblings] matched ar_adj (type={type(ar_adj)}) = {ar_adj.get('adjective') if isinstance(ar_adj, dict) else ar_adj!r}")
    if not ar_adj:
        log.debug("[resolve_siblings] X No AR adjective found")
        return []

    verb_list = ar_adj.get("request_options", {}).get(ar_value, [])
    log.debug(f"[resolve_siblings] verb_list = {verb_list!r}")
    if not verb_list:
        log.debug("[resolve_siblings] X No verbs for this AR value")
        return []

    siblings = []
    for verb_name in verb_list:
        verb_def = verb_schemas.get(verb_name)
        log.debug(f"\n    -> Checking verb '{verb_name}' -> verb_def exists={bool(verb_def)}")
        if not isinstance(verb_def, dict):
            log.debug(f"      X verb_def is not a dict, it's a {type(verb_def)}")
            continue

        child_noun_type = verb_def.get("data_entry_schema", {}) \
                                  .get("set_up_inputs", {}) \
                                  .get("noun_type_ref")
        log.debug(f"      -> child_noun_type = {child_noun_type!r}")
        if not child_noun_type:
            log.debug("      X No child noun type")
            continue

        child_schema = all_noun_schemas.get(child_noun_type)
        log.debug(f"      -> child_schema present={bool(child_schema)}")
        if not child_schema:
            continue

        pk_field = child_schema.get("primary_id_field")
        log.debug(f"      -> pk_field = {pk_field!r}")
        if not pk_field:
            continue

        # find the field in child_schema that references this parent
        ref_field = None
        for f, f_def in child_schema.get("fields", {}).items():
            if f_def.get("type") != "adjective":
                continue
            # WARNING: this may be where you confused a string for a dict
            adj_meta = next(
                (a for a in adjective_schemas
                if a.get("adjective_class") == "Reference"
                and f == a.get("adjective")  # field name match
                and a.get("reference_noun") == parent_noun_type),
                None
            )
            log.debug(f"         checking child field '{f}', adj_meta exists={bool(adj_meta)}")
            if isinstance(adj_meta, dict) \
               and adj_meta.get("adjective_class") == "Reference" \
               and adj_meta.get("reference_noun") == parent_noun_type:
                ref_field = f
                break
        log.debug(f"      -> ref_field = {ref_field!r}")
        if not ref_field:
            continue

        for inst in all_noun_items.get(child_noun_type, []):
            # match parent_id in that ref_field
            if inst.get(ref_field) != parent_id:
                continue
            if inst.get(pk_field) == exclude_noun_id:
                continue
            log.debug(f"       sibling found: {child_noun_type} {inst.get(pk_field)!r}")
            siblings.append({
                **inst,
                "_noun_type": child_noun_type,
                "_primary_id_field": pk_field,
                "_run_id": inst.get("_runID")
            })

    log.debug(f"[resolve_siblings] returning {len(siblings)} sibling(s)\n")
    return siblings
