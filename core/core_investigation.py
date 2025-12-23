import sys, json
from pathlib import Path
import re
sys.path.append(str(Path(__file__).resolve().parents[1]))
from typing import Callable

# =========================
# Debug helpers
# =========================
# Set to False to silence backend debug logging.
DEBUG_ENABLED = False  # Change to False to disable debug logs

def debug(*args, **kwargs):
    """Debug print that respects DEBUG_ENABLED flag."""
    if DEBUG_ENABLED:
        print(*args, **kwargs)


def filter_fields_by_schema(items: list[dict], noun_schema: dict) -> list[dict]:
    """
    Return a copy of each item containing only fields defined in the provided noun schema.
    """
    valid_fields = set(noun_schema.get("fields", {}).keys())
    pid = noun_schema.get("primary_id_field")
    if pid:
        valid_fields.add(pid)
    if not valid_fields:
        return items
    return [{k: v for k, v in item.items() if k in valid_fields} for item in items]

def apply_filter(items, field, value):
    return [item for item in items if str(value) in str(item.get(field, ""))]

def apply_exclude(items, field, value):
    return [item for item in items if str(value) not in str(item.get(field, ""))]

def apply_sort(items, field):
    return sorted(items, key=lambda x: str(x.get(field, "")))

def parse_args(args: list[str]) -> dict:
    """
    Parses CLI-style args into sort/filter/exclude instructions.
    Example input: ["--sort", "batch_id", "--filter", "status:approved"]
    """
    opts = {"sort": None, "filter": [], "exclude": []}
    i = 0
    while i < len(args):
        if args[i] == "--sort" and i + 1 < len(args):
            opts["sort"] = args[i + 1]
            i += 2
        elif args[i] == "--filter" and i + 1 < len(args):
            f, v = args[i + 1].split(":", 1)
            opts["filter"].append((f, v))
            i += 2
        elif args[i] == "--exclude" and i + 1 < len(args):
            f, v = args[i + 1].split(":", 1)
            opts["exclude"].append((f, v))
            i += 2
        else:
            i += 1
    return opts

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

    debug(f"\n[get_lineage] start noun_type={noun_type}")
    debug(f"[get_lineage] record keys: {list(record.keys())}")

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

    debug(f"[get_lineage] primary_id_field={pk_field!r}")
    debug(f"[get_lineage] seeking instance_id={instance_id!r} run_id={run_id!r}")

    target = find_matching_instance(items, pk_field, instance_id, run_id)
    if not target:
        debug("[get_lineage] X No matching instance found.")
        return {}

    debug(f"[get_lineage]  Found target instance: {target.get(pk_field)}")
    target["_noun_type"] = noun_type
    target["_primary_id_field"] = pk_field

    # Find referencing verb runs
    debug("[get_lineage] scanning referencing runs...")
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
    debug(f"[get_lineage] referencing runs found: {len(found_runs)}")

    # Resolve parents
    debug("[get_lineage] resolving parents...")

    # 🔁 Convert list -> dict first
    adj_schema_map = {a["adjective"]: a for a in adjective_schemas}

    # Sanity check all argument types before entering resolve_parents
    debug("[get_lineage] resolve_parents sanity check:")
    debug(f"  noun_type={noun_type!r}")
    debug(f"  instance keys: {list(target.keys())}")
    debug(f"  noun_schema type={type(noun_schema)}")
    debug(f"  adjective_schemas(dict) keys={list(adj_schema_map.keys())}")
    debug(f"  all_noun_schemas keys={list(all_noun_schemas.keys())}")
    debug(f"  all_noun_items keys={list(all_noun_items.keys())}")

    parents = resolve_parents(
        noun_type=noun_type,
        instance=target,
        noun_schema=noun_schema,
        adjective_schemas=adj_schema_map,  #  dict now
        all_noun_items=all_noun_items,
        all_noun_schemas=all_noun_schemas
    )

    # Resolve siblings
    grouped_verb_data = _grouped_verb_data(verb_data)
    for parent in parents:
        p_type = parent["noun_type"]
        p_id = parent["noun_id"]
        ar = parent.get("action_requirement")
        debug(f"[get_lineage] resolving siblings for parent {p_type} -> {p_id} (AR: {ar})")

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
        debug(f"[get_lineage] siblings found: {len(siblings)}")
        parent["siblings"] = siblings

    # Flatten referencing nouns
    referencing = []
    for run in found_runs.values():
        referencing.extend(run.get("referencing_nouns", []))
    debug(f"[get_lineage] collected {len(referencing)} referencing noun records")

    # Retest resolution
    debug("[get_lineage] checking for retests/overrides...")
    retests = resolve_overrides(
        referencing_nouns=referencing,
        noun_instance=target,
        overrides=override_entries,
        verb_data=verb_data,
        noun_schemas=all_noun_schemas,
        verb_group_map=precomputed_verb_group_map,
        noun_type_map=precomputed_noun_type_map
    )
    debug(f"[get_lineage] retests / overrides: {len(retests)}")

    result = {
        "noun": target,
        "noun_type": noun_type,
        "runs": list(found_runs.values()),
        "parents": parents,
        "retests": retests,
    }

    debug("[get_lineage]  done.")
    return result

def format_lineage_text(
    lineage: dict,
    noun_schemas: dict,
    verb_schemas: dict,
    adverb_schemas: list[dict],
    raw_inputs_map: dict[str, list[str]],
    verb_data: dict[tuple[str, str], list[dict]],
    status_json_map: dict[str, dict],
    adverb_json_map: dict[str, dict],
    project_path: Path
) -> str:
    from core.status import render_status_bar

    out = ["🧬 Lineage Investigation\n"]

    # --- Noun
    noun = lineage.get("noun")
    noun_type = lineage.get("noun_type")
    out.append("🔖 Noun instance:")
    if noun:
        out.append(f"  ({noun_type})")
        for k, v in noun.items():
            if not k.startswith("_"):
                out.append(f"  {k}: {v}")
    else:
        out.append("  (not found)")

    # --- Runs
    out.append("\n🧪 Referenced in Runs:")
    runs = lineage.get("runs", [])
    if runs:
        for run in runs:
            run_id = run["run_id"]
            verb = run.get("verb", "(unknown)")
            verb_schema = verb_schemas.get(verb, {})
            verb_group = verb_schema.get("verb_group", "Tests")
            noun_type_ref = verb_schema.get("data_entry_schema", {}).get("set_up_inputs", {}).get("noun_type_ref")
            noun_schema = noun_schemas.get(noun_type_ref, {})

            data_entry = verb_data.get((verb_group, run_id), [])
            adverb_data = adverb_json_map.get(run_id, {})
            status_data = status_json_map.get(run_id, {})

            breakdown = build_run_status_breakdown(
                project_path=project_path,
                run_id=run_id,
                verb=verb,
                verb_schema=verb_schema,
                noun_schema=noun_schema,
                data_entry=data_entry,
                adverb_data=adverb_data,
                status_data=status_data
            )

            out.append(f"  • Run ID: {run_id} | Verb: {verb}")
            out.append(f"    {render_status_bar(breakdown)}")
            for k, v in breakdown.items():
                out.append(f"      • {k}: {v}")

            for ref in run.get("referencing_nouns", []):
                ref_type = ref.get("_noun_type", "(unknown)")
                pk_field = ref.get("_primary_id_field")
                pk_value = ref.get(pk_field, "(no ID)") if pk_field else "(no ID)"
                out.append(f"    ↳ {ref_type}: {pk_field} = {pk_value}")
    else:
        out.append("  (none)")

    # --- Parents and Siblings
    out.append("\n⬆ Referencing Parents (and Siblings):")
    for parent in lineage.get("parents", []):
        p_type = parent["noun_type"]
        p_id = parent["noun_id"]
        ar = parent.get("action_requirement", "(unknown)")
        out.append(f"  • Parent: {p_type} {p_id} (AR: {ar})")
        siblings = parent.get("siblings", [])
        if siblings:
            out.append("     ├── Siblings:")
            for i, sib in enumerate(siblings):
                sib_type = sib.get("_noun_type", "(unknown)")
                sib_field = sib.get("_primary_id_field")
                sib_id = sib.get(sib_field, "(no ID)")
                run_id = sib.get("_run_id", "(no run)")
                branch = "     │   " if i < len(siblings) - 1 else "     └── "
                out.append(f"{branch}↪️ {sib_type}: {sib_field} = {sib_id} | run: {run_id}")
        else:
            out.append("     └── No siblings found")

    # --- Retests
    out.append("\n🔁 Retests via Overrides:")
    retests = lineage.get("retests", [])
    if retests:
        for rt in retests:
            noun = rt.get("noun_instance", {})
            rt_type = noun.get("_noun_type", "(unknown)")
            pk_field = noun.get("_primary_id_field")
            pk_value = noun.get(pk_field, "(no ID)") if pk_field else "(no ID)"
            orig_run = rt.get("retest_of", "(unknown)")
            out.append(f"  • {rt_type}: {pk_field} = {pk_value} (retest of {orig_run})")
    else:
        out.append("  (none)")

    return "\n".join(out)

def build_run_status_breakdown(
    project_path: Path,
    run_id: str,
    verb: str,
    verb_schema: dict,
    noun_schema: dict,
    data_entry: list[dict],
    adverb_data: dict,
    status_data: dict
) -> dict:
    from api.manifest.resolver import resolve_data_dump_contents
    from core.status import get_status_breakdown_core

    verb_group = verb_schema.get("verb_group", "Tests")
    raw_inputs = verb_schema.get("data_entry_schema", {}).get("raw_data_inputs", [])
    adverb_schema = verb_schema.get("adverb_schema", {})

    # Get actual folder contents to infer raw_uploaded and uploaded_tabs
    dump = resolve_data_dump_contents(project_path, verb_group, run_id)

    #  1. Resolve raw data uploads
    raw_uploaded = [
        folder for folder in raw_inputs
        if dump.get("folders", {}).get(folder, {}).get("files")
    ]

    #  2. Resolve interpretation tabs from root-level .csv files
    interp_tabs = verb_schema.get("data_entry_schema", {}).get("interpretation", {}).get("tabs", [])
    uploaded_tabs = []

    debug(f"\n[build_run_status_breakdown] run={run_id}")
    debug(f"[build_run_status_breakdown] expected interp_tabs: {interp_tabs}")

    for tab in interp_tabs:
        tab_file = dump["files"]["other_files"].get(f"{tab}.csv")
        if tab_file:
            try:
                contents = tab_file.read_text().strip()
                debug(f"   found {tab}.csv | non-empty: {bool(contents)}")
                if contents:
                    uploaded_tabs.append(tab)
                    debug(f"    -> marked {tab} as uploaded")
                else:
                    debug(f"    -> {tab}.csv is empty")
            except Exception as e:
                debug(f"  ! error reading {tab}.csv: {e}")
        else:
            debug(f"  [NO] missing tab file: {tab}.csv")

    #  3. Patch the status_data
    patched_status = status_data.copy()
    patched_status["raw_uploaded"] = raw_uploaded
    patched_status.setdefault("interpretation", {})
    patched_status["interpretation"]["uploaded_tabs"] = uploaded_tabs

    #  4. Call pure logic
    return get_status_breakdown_core(project_path, run_id)

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
    debug(f"\n[resolve_parents] noun_type={noun_type}, self PK field='{pk_field_self}', instance ID={instance.get(pk_field_self)}")

    # DEBUG: inspect the whole adjective_schemas structure
    debug(f"[resolve_parents] adjective_schemas (type={type(adjective_schemas)}) keys={list(adjective_schemas.keys())}")

    for field_name, field_def in noun_schema.get("fields", {}).items():
        if field_def.get("type") != "adjective":
            continue

        debug(f"\n  -> Field '{field_name}' is adjective; field_def={field_def!r}")

        # 1) Attempt to .get() the schema for this adjective field
        try:
            adj_schema = adjective_schemas.get(field_name)
        except Exception as e:
            debug(f"    !!! ERROR: adjective_schemas.get('{field_name}') -> {e!r}")
            raise
        debug(f"    -> adj_schema (type={type(adj_schema)}) = {adj_schema!r}")

        if not adj_schema:
            debug(f"    X no schema for adjective '{field_name}'")
            continue
        if adj_schema.get("adjective_class") != "Reference":
            debug(f"    X adjective_class != Reference (found {adj_schema.get('adjective_class')!r})")
            continue

        ref_noun_type = adj_schema.get("reference_noun")
        debug(f"    -> reference_noun = {ref_noun_type!r}")
        if not ref_noun_type:
            continue

        ref_inst_id = instance.get(field_name)
        debug(f"    -> instance['{field_name}'] = {ref_inst_id!r}")
        if not ref_inst_id or not isinstance(ref_inst_id, str):
            continue

        ref_schema = all_noun_schemas.get(ref_noun_type)
        debug(f"    -> ref_schema for {ref_noun_type!r} (type={type(ref_schema)}) = {ref_schema!r}")
        if not ref_schema:
            continue

        pk_field = ref_schema.get("primary_id_field")
        debug(f"    -> ref_schema primary_id_field = {pk_field!r}")
        if not pk_field:
            continue

        # verify ActionRequirement field...
        ar_field = None
        for f, f_def in ref_schema.get("fields", {}).items():
            if f_def.get("type") == "adjective" and f_def.get("adjective_class") == "ActionRequirement":
                ar_field = f
                break
        debug(f"    -> resolved ar_field = {ar_field!r}")
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
            debug(f"    -> candidate[{ar_field}] = {ar_value!r}")
            if not ar_value:
                continue

            debug(f"     Found parent {key} with AR={ar_value!r}")
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

    debug(f"[resolve_parents] returning {len(parents)} parent(s)\n")
    return parents

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
    debug(f"\n[resolve_siblings] parent_noun_type={parent_noun_type}, parent_id={parent_id}, AR='{ar_value}'")

    # DEBUG: inspect the list and types in adjective_schemas
    debug(f"[resolve_siblings] adjective_schemas list length: {len(adjective_schemas)}")
    for i, adj in enumerate(adjective_schemas):
        debug(f"   idx={i} type={type(adj)} value={adj.get('adjective', '(no name)')}")

    # 1) find the AR adjective in the list
    ar_adj = next(
        (a for a in adjective_schemas
         if isinstance(a, dict)
         and a.get("adjective_class") == "ActionRequirement"
         and parent_noun_type in a.get("applies_to", [])),
        None
    )
    debug(f"[resolve_siblings] matched ar_adj (type={type(ar_adj)}) = {ar_adj.get('adjective') if isinstance(ar_adj, dict) else ar_adj!r}")
    if not ar_adj:
        debug("[resolve_siblings] X No AR adjective found")
        return []

    verb_list = ar_adj.get("request_options", {}).get(ar_value, [])
    debug(f"[resolve_siblings] verb_list = {verb_list!r}")
    if not verb_list:
        debug("[resolve_siblings] X No verbs for this AR value")
        return []

    siblings = []
    for verb_name in verb_list:
        verb_def = verb_schemas.get(verb_name)
        debug(f"\n    -> Checking verb '{verb_name}' -> verb_def exists={bool(verb_def)}")
        if not isinstance(verb_def, dict):
            debug(f"      X verb_def is not a dict, it's a {type(verb_def)}")
            continue

        child_noun_type = verb_def.get("data_entry_schema", {}) \
                                  .get("set_up_inputs", {}) \
                                  .get("noun_type_ref")
        debug(f"      -> child_noun_type = {child_noun_type!r}")
        if not child_noun_type:
            debug("      X No child noun type")
            continue

        child_schema = all_noun_schemas.get(child_noun_type)
        debug(f"      -> child_schema present={bool(child_schema)}")
        if not child_schema:
            continue

        pk_field = child_schema.get("primary_id_field")
        debug(f"      -> pk_field = {pk_field!r}")
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
            debug(f"         checking child field '{f}', adj_meta exists={bool(adj_meta)}")
            if isinstance(adj_meta, dict) \
               and adj_meta.get("adjective_class") == "Reference" \
               and adj_meta.get("reference_noun") == parent_noun_type:
                ref_field = f
                break
        debug(f"      -> ref_field = {ref_field!r}")
        if not ref_field:
            continue

        for inst in all_noun_items.get(child_noun_type, []):
            # match parent_id in that ref_field
            if inst.get(ref_field) != parent_id:
                continue
            if inst.get(pk_field) == exclude_noun_id:
                continue
            debug(f"       sibling found: {child_noun_type} {inst.get(pk_field)!r}")
            siblings.append({
                **inst,
                "_noun_type": child_noun_type,
                "_primary_id_field": pk_field,
                "_run_id": inst.get("_runID")
            })

    debug(f"[resolve_siblings] returning {len(siblings)} sibling(s)\n")
    return siblings

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

def build_table_rows(
    rows: list[dict],
    noun_type: str,
    schema: dict,
    runid_map: dict[str, str],
    format_display_id: callable
) -> tuple[list[str], list[list[str]]]:
    """
    Builds headers and row content for display from noun records, using passed schema and runID map.

    Returns:
    - all_fields: list of column headers
    - table_rows: list of row value lists
    """
    pk_field = schema.get("primary_id_field", f"{noun_type.lower()}_id")

    # Inject _runID where missing
    for row in rows:
        if "_runID" not in row:
            row["_runID"] = runid_map.get(row.get(pk_field, ""), "")

    show_run_id = any(r.get("_runID") for r in rows)
    base_fields = list(schema.get("fields", {}).keys())
    all_fields = base_fields + (["_runID"] if show_run_id else [])

    table_rows = []
    for row in rows:
        display_row = []
        for field in all_fields:
            val = row.get(field, "")
            if field == pk_field:
                val = format_display_id(row, noun_type)
            elif isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            elif val is None:
                val = ""
            else:
                val = str(val)
            display_row.append(val)
        table_rows.append(display_row)

    return all_fields, table_rows

def get_all_fields(items: list[dict]) -> list[str]:
    """Returns all unique keys from a list of dicts."""
    return sorted(set().union(*(item.keys() for item in items)))

def build_filter_function(field: str, value: str, exclude: bool = False):
    """
    Builds a lambda for filtering items by field and value.
    Supports regex when value is wrapped in /slashes/.
    """
    if value.startswith("/") and value.endswith("/") and len(value) > 2:
        try:
            pattern = re.compile(value.strip("/"), re.IGNORECASE)
            return (lambda x: not pattern.search(str(x.get(field, "")))) if exclude else (
                   lambda x: bool(pattern.search(str(x.get(field, "")))))
        except re.error:
            return None
    else:
        return (lambda x: value not in str(x.get(field, ""))) if exclude else (
               lambda x: value in str(x.get(field, "")))

def apply_all_filters(items: list[dict], filters_applied: list[tuple[str, list]]) -> list[dict]:
    """
    Applies all filters to items using AND/OR logic.
    filters_applied: list of (op, filter/lambdas)
    """
    temp = items
    for op, filt in filters_applied:
        if op == "AND":
            temp = list(filter(filt, temp))
        elif op == "OR":
            temp = [x for x in temp if any(f(x) for f in filt)]
    return temp

def sort_items_by_field(items: list[dict], field: str) -> list[dict]:
    try:
        return sorted(items, key=lambda x: x.get(field, ""))
    except Exception:
        return items  # fallback if sorting fails

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
    debug(f"\n[analyze_referencing_runs] start for {referenced_noun_type} ID: {noun_instance_id}")
    results = {}
    flat_referencing_nouns = []

    # Step 1: Find all adjective fields in this noun schema that are ActionRequirement
    adjective_fields = [
        (field_name, field_spec["adjective_class"])
        for field_name, field_spec in noun_schema.get("fields", {}).items()
        if field_spec.get("type") == "adjective"
        and field_spec.get("adjective_class") == "ActionRequirement"
    ]
    debug(f"[analyze_referencing_runs] AR fields: {[f[0] for f in adjective_fields]}")

    for field_name, _adj_class in adjective_fields:
        # Step 2: Find matching adjective schema from global adjective list
        adj_schema = next(
            (a for a in adjective_schemas
             if a.get("adjective") == field_name and
             referenced_noun_type in a.get("applies_to", [])),
            None
        )
        if not adj_schema:
            debug(f"[analyze_referencing_runs] skip {field_name}: no matching adjective schema applies to {referenced_noun_type}")
            continue

        # Step 3: Extract verbs that use this AR adjective
        verb_lists = adj_schema.get("request_options", {}).values()
        all_verbs = sorted({verb for lst in verb_lists for verb in lst})
        debug(f"[analyze_referencing_runs] field '{field_name}' links to verbs: {all_verbs}")

        for verb in all_verbs:
            verb_schema = verb_schemas.get(verb)
            if not verb_schema:
                debug(f"[analyze_referencing_runs] missing verb schema for {verb}, skipping")
                continue

            setup_inputs = verb_schema.get("data_entry_schema", {}).get("set_up_inputs", {})
            source_noun = setup_inputs.get("noun_type_ref")
            if not source_noun:
                debug(f"[analyze_referencing_runs] verb {verb} has no noun_type_ref, skipping")
                continue
            if source_noun not in all_noun_items:
                debug(f"[analyze_referencing_runs] no items found for source noun type: {source_noun}")
                continue

            items = all_noun_items[source_noun]
            pk_field = all_noun_schemas.get(source_noun, {}).get("primary_id_field", "")

            debug(f"[analyze_referencing_runs] scanning {len(items)} {source_noun} item(s) for references to {noun_instance_id} via any field")

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
                    debug(f"[analyze_referencing_runs] ! matched row has no _runID, skipping: {row}")
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
                    debug(f"[analyze_referencing_runs]  new referencing run: {run_id} via verb '{resolved_verb}'")
                else:
                    debug(f"[analyze_referencing_runs] appending to existing run: {run_id}")

                results[run_id]["referencing_nouns"].append(annotated)

    debug(f"[analyze_referencing_runs] done. runs={len(results)}, noun_records={len(flat_referencing_nouns)}")
    return results, flat_referencing_nouns
