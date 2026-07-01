# core/core_deep_search.py

# Debug control - set to False to disable all backend debug logging
from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()


def normalize_string(s: str) -> str:
    """Lowercase and remove all whitespace for fuzzy matching (values only)."""
    result = "".join(str(s).lower().split())
    log.debug("[normalize_string] in:", repr(s), "-> out:", repr(result))
    return result


# -----------------------
# Key resolution helpers
# -----------------------
def _norm_key(k: str) -> str:
    """
    Normalize a dict key for tolerant matching:
    - lowercase
    - remove spaces, underscores, and hyphens
    """
    k = str(k)
    return "".join(ch for ch in k.lower() if ch not in " _-")


def find_actual_key(obj: dict, desired_key: str) -> str | None:
    """
    Given a desired field name from config (e.g., 'run_ID' or 'general ID'),
    find the actual key in 'obj' regardless of case and spaces/underscores/hyphens.
    Returns the real key or None if not found.
    """
    want = _norm_key(desired_key)
    for k in obj.keys():
        if _norm_key(k) == want:
            return k
    return None


def _normalize_group_for_lookup(s: str) -> list[str]:
    """
    Normalize a candidate group string for fuzzy map lookup.
    Produces singular/plural variants (e.g., 'tests' <-> 'test').
    """
    t = (s or "").strip().lower()
    if not t:
        return []
    variants = {t}
    if t.endswith("s"):
        variants.add(t[:-1])
    else:
        variants.add(t + "s")
    return list(variants)


def _resolve_group_and_primary_field(item: dict, primary_id_by_group: dict) -> tuple[str | None, str | None]:
    """
    Returns (group_name, primary_id_field_from_config) using:
      1) direct read from common group fields,
      2) fuzzy singular/plural match against map keys,
      3) fallback by presence: if an item has any mapped primary-id field (tolerant match), assume that group.
    """
    if not primary_id_by_group:
        return (None, None)

    # Build normalized map {lower_group: (original_group, pid_field)}
    norm_map = {}
    for grp, pid in (primary_id_by_group or {}).items():
        g = (grp or "").strip()
        if not g or not pid:
            continue
        norm_map[g.lower()] = (g, pid)

    # 1) Try to read group from common fields
    group_field_candidates = ("group", "verb_group", "category", "type", "test_type", "_verb_group")
    for gf in group_field_candidates:
        if gf in item and isinstance(item[gf], str):
            for variant in _normalize_group_for_lookup(item[gf]):
                if variant in norm_map:
                    g_orig, pid = norm_map[variant]
                    log.debug("[_resolve_group_and_primary_field] via field", gf, "->", g_orig, "/", pid)
                    return (g_orig, pid)

    # 2) Presence-based fallback: if item contains any mapped primary-id field, choose that group
    for g_lower, (g_orig, pid) in norm_map.items():
        actual = find_actual_key(item, pid)
        if actual is not None:
            log.debug("[_resolve_group_and_primary_field] via presence of field", pid, "as", actual, "->", g_orig)
            return (g_orig, pid)

    log.debug("[_resolve_group_and_primary_field] unresolved")
    return (None, None)


def match_schema_definitions(search_term: str, schemas: dict) -> list[dict]:
    """
    Given loaded schemas (noun/verb/adjective/adverb), return matches.
    schemas = {
        "noun": {...},
        "verb": {...},
        "adjective": [...],
        "adverb": [...]
    }
    """
    log.debug("[match_schema_definitions] start", {"search_term": search_term})
    matches = []
    normalized_search = normalize_string(search_term)

    for part, schema_dict in schemas.items():
        if schema_dict is None:
            log.debug(f"[match_schema_definitions] skip '{part}': schema_dict is None")
            continue

        # Nouns/verbs are dicts; adjectives/adverbs are lists
        if isinstance(schema_dict, dict):
            items_iter = schema_dict.items()
            total_items = len(schema_dict)
        else:
            id_field = "adjective" if part == "adjective" else ("adverb" if part == "adverb" else None)
            items_iter = ((entry.get(id_field, ""), entry) for entry in schema_dict) if id_field else []
            total_items = len(schema_dict) if hasattr(schema_dict, "__len__") else -1

        log.debug(f"[match_schema_definitions] part={part} items={total_items}")

        local_count = 0
        for key, value in items_iter:
            try:
                key_str = str(key)
                key_norm = normalize_string(key_str)
                cond = (
                    search_term.lower() in key_str.lower()
                    or normalized_search == key_norm
                    or normalized_search in key_norm
                )
                if cond:
                    matches.append({
                        "schema_type": part,
                        "schema_name": key,
                        "path": f"{part}/{key}",
                        "match_context": value
                    })
                    local_count += 1
            except Exception as e:
                log.debug(f"[match_schema_definitions] warn: failed processing key='{key}' in part='{part}': {e}")

        log.debug(f"[match_schema_definitions] part='{part}' matched={local_count}")

    log.debug("[match_schema_definitions] done total_matches:", len(matches))
    return matches


def match_instances(
    search_term: str,
    instances: list[dict],
    primary_id_field: str | None = None,
    primary_id_lookup: dict | None = None,
    primary_id_map_by_group: dict | None = None,
) -> list[dict]:
    """
    Search loaded noun/verb instances (list of dicts).

    - primary_id_field: a single, global primary ID to prefer (used for nouns previously).
    - primary_id_lookup: noun_type -> primary_id_field (for nouns).
    - primary_id_map_by_group: verb_group -> primary_id_field (for verbs).

    This function does NO I/O. All inputs must be provided by the caller.
    """
    log.debug("[match_instances] start", {
        "search_term": search_term,
        "instances": len(instances),
        "primary_id_field": primary_id_field,
        "has_primary_id_lookup": bool(primary_id_lookup),
        "has_primary_id_map_by_group": bool(primary_id_map_by_group),
    })

    matches = []
    normalized_search = normalize_string(search_term)
    high_quality_hits = 0
    field_scans = 0

    for idx, item in enumerate(instances):
        # Track best match for this item
        best_match = {
            "score": 0,  # Higher is better
            "field": None,
            "value": None,
            "match_type": None
        }

        # Determine noun/verb context
        noun_type = item.get("_noun_type")
        verb_group = item.get("_verb_group")

        # Determine item-specific primary ID field (from config)
        item_primary_id_field_from_cfg = None
        resolved_group = None

        # For nouns: use noun-specific primary ID lookup if present
        if noun_type and primary_id_lookup and noun_type in primary_id_lookup:
            item_primary_id_field_from_cfg = primary_id_lookup.get(noun_type)
            resolved_group = None
            log.debug(f"[match_instances][noun] idx={idx} noun_type={noun_type} primary_id={item_primary_id_field_from_cfg}")

        # For verbs: use per-group config mapping (deterministic)
        if (not item_primary_id_field_from_cfg) and primary_id_map_by_group:
            # 1) Check if we have a pre-tagged primary ID field from config
            if "_primary_id_field_from_config" in item:
                item_primary_id_field_from_cfg = item["_primary_id_field_from_config"]
                resolved_group = item.get("_verb_group")
                log.debug(f"[match_instances][verb] idx={idx} using pre-tagged primary_id='{item_primary_id_field_from_cfg}' from group='{resolved_group}'")
            # 2) Trust the explicit _verb_group tag and look up its configured PID
            elif verb_group and verb_group in primary_id_map_by_group:
                expected_pid_field = primary_id_map_by_group[verb_group]
                if find_actual_key(item, expected_pid_field):
                    item_primary_id_field_from_cfg = expected_pid_field
                    resolved_group = verb_group
                    log.debug(f"[match_instances][verb] idx={idx} via trusted _verb_group='{verb_group}' primary_id='{expected_pid_field}'")
            # 3) Fall back to the resolver if needed
            if not item_primary_id_field_from_cfg:
                g, pid = _resolve_group_and_primary_field(item, primary_id_map_by_group)
                if pid:
                    item_primary_id_field_from_cfg = pid
                    resolved_group = g
                    log.debug(f"[match_instances][verb] idx={idx} via fallback resolver group='{g}' primary_id='{pid}'")

        # Figure out the actual key in the item for the declared primary-id (tolerant)
        actual_primary_key = None
        if item_primary_id_field_from_cfg:
            actual_primary_key = find_actual_key(item, item_primary_id_field_from_cfg)
            if actual_primary_key and actual_primary_key != item_primary_id_field_from_cfg:
                log.debug(f"[match_instances] idx={idx} primary id key resolved: {item_primary_id_field_from_cfg!r} -> {actual_primary_key!r}")
            elif not actual_primary_key:
                log.debug(f"[match_instances] idx={idx} primary id key {item_primary_id_field_from_cfg!r} not present (any-casing/spacing)")

        # Build potential ID fields (prioritize the resolved actual key)
        potential_id_fields = []
        if actual_primary_key:
            potential_id_fields = [actual_primary_key]

        # Also allow an explicitly supplied global field (back-compat)
        if primary_id_field:
            k = find_actual_key(item, primary_id_field)
            if k and k not in potential_id_fields:
                potential_id_fields.append(k)

        # As a last resort, consider common ID-ish names (helps loose matches)
        for alt in ["id", "run_id", "runID", "RunID", "Run ID", "sample_id", "Sample ID", "sampleID",
                    "batch_id", "Batch ID", "batchID", "submission_id", "Submission ID", "submissionID"]:
            k = find_actual_key(item, alt)
            if k and k not in potential_id_fields:
                potential_id_fields.append(k)

        # Check ID fields (prefer high-quality matches early exit)
        for field in potential_id_fields:
            # NB: 'field' here is ALWAYS an actual key in the item
            value = str(item[field])
            value_norm = normalize_string(value)

            if search_term.lower() == value.lower():
                best_match = {"score": 100, "field": field, "value": value, "match_type": "exact_id"}
                log.debug(f"[match_instances] idx={idx} HIGH exact_id field={field} value={value}")
                break
            elif normalized_search == value_norm:
                if best_match["score"] < 90:
                    best_match = {"score": 90, "field": field, "value": value, "match_type": "normalized_exact_id"}
            elif search_term.lower() in value.lower():
                if best_match["score"] < 80:
                    best_match = {"score": 80, "field": field, "value": value, "match_type": "substring_id"}
            elif normalized_search in value_norm:
                if best_match["score"] < 70:
                    best_match = {"score": 70, "field": field, "value": value, "match_type": "normalized_substring_id"}

        if best_match["score"] >= 80:
            # High-quality ID hit — fast path
            high_quality_hits += 1
            item_with_context = item.copy()
            item_with_context["match_context"] = {best_match["field"]: best_match["value"]}
            item_with_context["match_score"] = best_match["score"]
            item_with_context["match_type"] = best_match["match_type"]

            if item_primary_id_field_from_cfg:
                item_with_context["_primary_id_field"] = item_primary_id_field_from_cfg
                if actual_primary_key:
                    item_with_context["_primary_id_field_resolved"] = actual_primary_key
                    item_with_context["_primary_id_value"] = item.get(actual_primary_key)
            if resolved_group:
                item_with_context["_resolved_group"] = resolved_group

            matches.append({"type": "noun_instance" if "_noun_type" in item else "verb_run", "data": item_with_context})
            continue

        # Otherwise scan other fields
        for field, v in item.items():
            if field.startswith("_") or field in potential_id_fields:
                continue
            field_scans += 1

            v_str = str(v)
            v_norm = normalize_string(v_str)

            if search_term.lower() == v_str.lower():
                if best_match["score"] < 60:
                    best_match = {"score": 60, "field": field, "value": v_str, "match_type": "exact_field"}
            elif search_term.lower() in v_str.lower():
                if best_match["score"] < 40:
                    best_match = {"score": 40, "field": field, "value": v_str, "match_type": "substring_field"}
            elif normalized_search == v_norm:
                if best_match["score"] < 50:
                    best_match = {"score": 50, "field": field, "value": v_str, "match_type": "normalized_exact_field"}
            elif normalized_search in v_norm:
                if best_match["score"] < 30:
                    best_match = {"score": 30, "field": field, "value": v_str, "match_type": "normalized_substring_field"}

        if best_match["score"] > 0:
            item_with_context = item.copy()
            item_with_context["match_context"] = {best_match["field"]: best_match["value"]}
            item_with_context["match_score"] = best_match["score"]
            item_with_context["match_type"] = best_match["match_type"]

            if item_primary_id_field_from_cfg:
                item_with_context["_primary_id_field"] = item_primary_id_field_from_cfg
                if actual_primary_key:
                    item_with_context["_primary_id_field_resolved"] = actual_primary_key
                    item_with_context["_primary_id_value"] = item.get(actual_primary_key)
            if resolved_group:
                item_with_context["_resolved_group"] = resolved_group

            matches.append({"type": "noun_instance" if "_noun_type" in item else "verb_run", "data": item_with_context})

    matches.sort(key=lambda m: m["data"].get("match_score", 0), reverse=True)
    log.debug("[match_instances] done", {
        "total_instances": len(instances),
        "total_matches": len(matches),
        "high_quality_id_hits": high_quality_hits,
        "field_scans": field_scans,
    })
    return matches


def deduplicate_matches(matches: list, primary_id_lookup: dict) -> list:
    """
    Deduplicate noun_instance matches using primary_id_lookup:
    { noun_type: "primary_id_field" }

    Verb runs are NOT deduplicated here (that’s handled by their primary keys upstream).
    """
    log.debug("[deduplicate_matches] start", {"incoming": len(matches)})
    seen = set()
    unique = []
    deduped = 0

    for m in matches:
        # Schema matches pass through
        if "schema_type" in m:
            unique.append(m)
            continue

        if "type" in m and m["type"] == "noun_instance":
            item = m["data"]
            noun_type = item.get("_noun_type")
            pid_field_cfg = primary_id_lookup.get(noun_type) if primary_id_lookup else None
            if pid_field_cfg:
                actual = find_actual_key(item, pid_field_cfg)
                pid = item.get(actual) if actual else None
                if pid and (noun_type, pid) not in seen:
                    seen.add((noun_type, pid))
                    unique.append(m)
                else:
                    deduped += 1
            else:
                unique.append(m)
        else:
            unique.append(m)

    log.debug("[deduplicate_matches] done", {"outgoing": len(unique), "deduped": deduped})
    return unique


def cascade_deep_search(
    search_term: str,
    schemas: dict,
    noun_instances: list[dict],
    verb_runs: list[dict],
    primary_id_lookup: dict,
    verb_primary_id_by_group: dict | None = None,
) -> dict:
    """
    Pure cascade: run through schemas, noun instances, verb runs.
    Inputs must already be loaded into memory. No I/O occurs here.

    - verb_primary_id_by_group: { verb_group: primary_id_field }
      e.g., {"Tests": "run_ID", "Move": "move_ID", "General": "general ID", ...}
    """
    log.debug("[cascade_deep_search] start", {
        "search_term": search_term,
        "schemas_keys": list(schemas.keys()) if isinstance(schemas, dict) else None,
        "noun_instances": len(noun_instances),
        "verb_runs": len(verb_runs),
        "has_primary_id_lookup": bool(primary_id_lookup),
        "verb_primary_id_groups": list(verb_primary_id_by_group.keys()) if verb_primary_id_by_group else [],
    })

    matches = []

    # 1) Schemas
    schema_matches = match_schema_definitions(search_term, schemas)
    log.debug("[cascade_deep_search] schema_matches:", len(schema_matches))

    # 2) Nouns (noun primary IDs come from primary_id_lookup)
    noun_matches = match_instances(
        search_term,
        noun_instances,
        primary_id_field=None,
        primary_id_lookup=primary_id_lookup,
        primary_id_map_by_group=None,
    )
    log.debug("[cascade_deep_search] noun_matches:", len(noun_matches))

    # 3) Verbs (per-group deterministic primary ID from verb log configs)
    verb_matches = match_instances(
        search_term,
        verb_runs,
        primary_id_field=None,
        primary_id_lookup=None,
        primary_id_map_by_group=verb_primary_id_by_group or {},
    )
    log.debug("[cascade_deep_search] verb_matches:", len(verb_matches))

    matches.extend(schema_matches)
    matches.extend(noun_matches)
    matches.extend(verb_matches)

    # Deduplicate and organize
    unique_matches = deduplicate_matches(matches, primary_id_lookup)
    log.debug("[cascade_deep_search] unique_matches:", len(unique_matches))

    result = {
        "schema": [],
        "noun_instances": [],
        "verb_runs": []
    }

    for match in unique_matches:
        if match.get("schema_type"):  # Schema match
            result["schema"].append(match)
        elif match.get("type") == "noun_instance":
            result["noun_instances"].append(match["data"])
        elif match.get("type") == "verb_run":
            result["verb_runs"].append(match["data"])

    log.debug("[cascade_deep_search] done", {
        "schema_out": len(result["schema"]),
        "noun_instances_out": len(result["noun_instances"]),
        "verb_runs_out": len(result["verb_runs"]),
    })
    return result
