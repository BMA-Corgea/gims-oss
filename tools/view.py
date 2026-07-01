import sys, json
from pathlib import Path
import re
sys.path.append(str(Path(__file__).resolve().parents[1]))
from utils.interface import prompt_if_missing, indexed_choice, menu_prompt
from utils.data_dump import open_data_dump
from utils.display import format_display_id
from utils import disambiguation as dis
import logging
logger = logging.getLogger(__name__)

def filter_fields_by_schema(items, noun_type, project):
    """Return a copy of each item containing only fields defined in the noun schema."""
    schema_path = Path(f"projects/{project}/noun_types.json")
    if not schema_path.exists():
        return items
    try:
        noun_defs = json.load(open(schema_path))
    except Exception:
        return items
    schema = noun_defs.get(noun_type, {})
    valid_fields = set(schema.get("fields", {}).keys())
    pid = schema.get("primary_id_field")
    if pid:
        valid_fields.add(pid)
    if not valid_fields:
        return items
    filtered = []
    for item in items:
        filtered.append({k: v for k, v in item.items() if k in valid_fields})
    return filtered

def apply_filter(items, field, value):
    return [item for item in items if str(value) in str(item.get(field, ""))]

def apply_exclude(items, field, value):
    return [item for item in items if str(value) not in str(item.get(field, ""))]

def apply_sort(items, field):
    return sorted(items, key=lambda x: str(x.get(field, "")))

def format_table(rows: list[dict], noun_type: str, project_path: Path) -> str:
    """
    Pretty-print a list of noun instances using Rich table.
    Appends tag-based suffixes to the primary ID using format_display_id().
    Adds _runID column if any record in items.jsonl has it for the same primary ID.
    """
    from rich.table import Table
    from rich.console import Console

    # Load schema and get primary ID field
    schema = dis.get_noun_schema(project_path, noun_type)
    pk_field = schema.get("primary_id_field", f"{noun_type.lower()}_id")

    # Load items.jsonl and build ID → runID map
    try:
        item_path = dis.get_noun_items(project_path, noun_type)
        all_items = [json.loads(line) for line in item_path.read_text().splitlines() if line.strip()]
        runid_map = {
            item[pk_field]: item.get("_runID", "")
            for item in all_items
            if pk_field in item and "_runID" in item
        }
    except Exception as e:
        print(f"⚠️ Could not load run IDs from items.jsonl: {e}")
        runid_map = {}

    # Inject _runID into rows if missing
    for row in rows:
        if "_runID" not in row:
            row["_runID"] = runid_map.get(row.get(pk_field, ""), "")

    # Decide whether to show runID column
    show_run_id = any(r.get("_runID") for r in rows)

    # Field list: schema + optional _runID
    base_fields = list(schema.get("fields", {}).keys())
    all_fields = base_fields + (["_runID"] if show_run_id else [])

    # Build Rich table
    table = Table(show_lines=True)
    table.add_column("#", style="dim")
    for f in all_fields:
        table.add_column(f)

    for idx, row in enumerate(rows):
        display_row = []
        for field in all_fields:
            val = row.get(field, "")
            if field == pk_field:
                val = format_display_id(row, noun_type, project_path)
            elif isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            elif val is None:
                val = ""
            else:
                val = str(val)
            display_row.append(val)
        table.add_row(str(idx), *display_row)

    # Output as string
    console = Console()
    with console.capture() as capture:
        console.print(table)
    return capture.get()

def prompt_field_choice(fields: list[str]) -> str | None:
    """
    Prompts the user to select a field from a list of field names.
    Returns the selected field name, or None if canceled/invalid.
    """
    index = indexed_choice(fields, prompt_msg="Select a field")
    if index is None:
        return None
    return fields[index]

def enter_investigate_mode(project, noun_type, items):

    if not items:
        print("❌ No items to investigate.")
        return

    project_path = Path("projects") / project

    def investigate(record):
        pk = noun_type.lower() + "_id"
        instance_id = record.get(pk)

        # ——— Print single record summary ———
        print(f"\n🕵️ Investigating {noun_type} {instance_id}\n")
        print(format_table([record], noun_type, project_path))
        print()

        # ——— Retrieve and display lineage ———
        lineage = get_lineage(project_path, noun_type, record)
        print(render_lineage(lineage, project_path))

        # ——— Menu for next action ———
        print()
        action = menu_prompt({
            "b": "back to list",
            "d": "deep search from here",
            "r": "restart view.py",
        })

        if action == "d":
            from tools.launch_deep_search import launch_deep_search
            print("\n🚀 Launching deep search...\n")
            result = launch_deep_search(project)
            if result == "restart":
                return "restart"
            return None

        if action == "r":
            return "restart"

        return None

    if len(items) == 1:
        result = investigate(items[0])
        if result == "restart":
            return "restart"
        return

    while True:
        # ——— Show table of candidates ———
        print(f"\n📋 {noun_type} records:\n")
        print(format_table(items, noun_type, project_path))
        print()

        # ——— Prompt for index ———
        choice = input("Select item to investigate (index), or (q)uit: ").strip()
        if choice.lower() == 'q':
            break
        if not choice.isdigit():
            print("❌ Invalid input. Please enter a number.")
            continue
        idx = int(choice)
        if idx < 0 or idx >= len(items):
            print(f"❌ {idx} is out of range (0–{len(items)-1}).")
            continue

        result = investigate(items[idx])
        if result == "restart":
            return "restart"

def interactive_loop(items, noun_type, project):
    original_items = items[:]
    filters_applied = []
    current_items = items[:]

    def apply_all_filters():
        temp = original_items
        for op, filt in filters_applied:
            if op == "AND":
                temp = list(filter(filt, temp))
            elif op == "OR":
                temp = [x for x in temp if any(f(x) for f in filt)]
        return temp

    def prompt_filter_lambda(field, value, exclude=False):
        if value.startswith("/") and value.endswith("/") and len(value) > 2:
            try:
                pattern = re.compile(value.strip("/"), re.IGNORECASE)
                return (lambda x, f=field, p=pattern: not p.search(str(x.get(f, "")))) if exclude else (
                       lambda x, f=field, p=pattern: bool(p.search(str(x.get(f, "")))))
            except re.error as e:
                print(f"❌ Invalid regex: {e}")
                return None
        else:
            return (lambda x, f=field, v=value: v not in str(x.get(f, ""))) if exclude else (
                   lambda x, f=field, v=value: v in str(x.get(f, "")))

    def handle_filter(is_or=False):
        all_fields = sorted(set().union(*(item.keys() for item in current_items)))
        filter_group = []

        while True:
            field = prompt_field_choice(all_fields)
            if not field:
                break
            value = input(f"Enter value for '{field}' (or 'q' to quit): ").strip()
            if value.lower() == 'q':
                break
            filt = prompt_filter_lambda(field, value)
            if filt:
                filter_group.append(filt)
            if not is_or:
                filters_applied.append(("AND", filt))
                break

        if is_or and filter_group:
            filters_applied.append(("OR", filter_group))

    while True:
        print("\n" + format_table(current_items, noun_type, Path("projects")/project))
        action = input(
            "\nAction? (s)ort, (f)ilter, (e)xclude, (o)r group, (r)estore, (q)uit: "
        ).strip().lower()

        if action == 'q':
            break
        elif action == 'r':
            filters_applied = []
            current_items = original_items[:]
        elif action == 's':
            all_fields = sorted(set().union(*(item.keys() for item in current_items)))
            field = prompt_field_choice(all_fields)
            if field:
                current_items = apply_sort(current_items, field)
        elif action in ['f', 'e']:
            all_fields = sorted(set().union(*(item.keys() for item in current_items)))
            field = prompt_field_choice(all_fields)
            if not field:
                continue
            value = input(f"Enter value for '{field}' (or 'q' to cancel): ").strip()
            if value.lower() == 'q':
                continue
            filt = prompt_filter_lambda(field, value, exclude=(action=='e'))
            if filt:
                filters_applied.append(("AND", filt))
                current_items = apply_all_filters()
        elif action == 'o':
            print("🔁 OR‐group mode. Add one or more filters.")
            handle_filter(is_or=True)
            current_items = apply_all_filters()
        elif action == 'i':
            result = enter_investigate_mode(project, noun_type, current_items)
            if result == "restart":
                print("\n🔄 Restarting view.py...\n")
                from tools.view import view_main
                view_main(project, noun_type)
                return  # important to stop current loop after restart
        else:
            print("❌ Invalid action.")
    return current_items

def parse_args(args):
    opts = {"sort": None, "filter": [], "exclude": []}
    i = 0
    while i < len(args):
        if args[i] == "--sort" and i + 1 < len(args):
            opts["sort"] = args[i+1]; i+=2
        elif args[i] == "--filter" and i + 1 < len(args):
            f,v = args[i+1].split(":",1); opts["filter"].append((f,v)); i+=2
        elif args[i] == "--exclude" and i + 1 < len(args):
            f,v = args[i+1].split(":",1); opts["exclude"].append((f,v)); i+=2
        else:
            i+=1
    return opts

def view_main(project: str, noun_type: str):
    project_path = Path("projects") / project

    # 1) Load and show initial table
    try:
        item_path = dis.get_noun_items(project_path, noun_type)
        items = [json.loads(line) for line in item_path.read_text().splitlines() if line.strip()]
    except FileNotFoundError:
        print(f"❌ No data found for noun '{noun_type}' in project '{project}'")
        return

    items = filter_fields_by_schema(items, noun_type, project)
    print(f"\n📦 {noun_type} Records from project: {project}\n")
    print(format_table(items, noun_type, project_path))

    # 2) Top-level mode loop
    while True:
        mode = menu_prompt({
            "s": "search",
            "i": "investigate",
            "q": "quit"
        })

        if mode == 'q':
            break

        elif mode == 's':
            # Enter search/filter/sort loop
            items = interactive_loop(items, noun_type, project)
            print(f"\n📦 {noun_type} Records from project: {project}\n")
            print(format_table(items, noun_type, project_path))

        elif mode == 'i':
            result = enter_investigate_mode(project, noun_type, items)

            if result == "restart":
                return view_main(project, noun_type)

            print(f"\n📦 {noun_type} Records from project: {project}\n")
            print(format_table(items, noun_type, project_path))

def get_lineage(project_path: Path, noun_type: str, record: dict) -> dict:
    import json
    from pathlib import Path

    def load_jsonl(path: Path) -> list:
        return [
            json.loads(line)
            for line in path.read_text().splitlines()
            if line.strip()
        ]

    def get_noun_schema(nt: str) -> dict:
        return json.loads((project_path / "noun_types.json").read_text()).get(nt, {})

    def get_primary_id_field(nt: str) -> str:
        return get_noun_schema(nt).get("primary_id_field", f"{nt.lower()}_id")

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

    # ─── Load all noun instances from items.jsonl ─────────────────────────────
    noun_file = project_path / "nouns" / noun_type / "items.jsonl"
    items = load_jsonl(noun_file) if noun_file.exists() else []
    noun_schema = get_noun_schema(noun_type)
    pk_field = noun_schema.get("primary_id_field", f"{noun_type.lower()}_id")

    # ─── Extract the primary ID and run ID from the passed-in record ─────────
    instance_id = record.get(pk_field)
    run_id      = record.get("_runID")

    # ─── Find the exact row in items.jsonl ───────────────────────────────────
    target = find_matching_instance(items, pk_field, instance_id, run_id)
    if not target:
        return {}
    target["_noun_type"] = noun_type
    target["_primary_id_field"] = pk_field

    # ─── Find all Runs that referenced this instance ─────────────────────────
    found_runs, referencing_nouns = find_run_references_to_noun_instance(project_path, noun_type, instance_id)

    # ─── Resolve parent chain ────────────────────────────────────────────────
    parents = resolve_parents(project_path, noun_type, target)

    # ─── Populate siblings for each parent ───────────────────────────────────
    for parent in parents:
        p_type = parent["noun_type"]
        p_id   = parent["noun_id"]
        ar     = parent.get("action_requirement")
        parent["siblings"] = resolve_siblings(
            project_path,
            p_type,
            p_id,
            ar,
            exclude_noun_type=noun_type,
            exclude_noun_id=instance_id
        )

    # ─── Resolve override-based retest lineage ───────────────────────────────
    referencing = []
    for run in found_runs.values():
        referencing.extend(run.get("referencing_nouns", []))
    retests = resolve_overrides(project_path, referencing, noun_instance=target)

    return {
        "noun":       target,
        "noun_type":  noun_type,
        "runs":       list(found_runs.values()),
        "parents":    parents,
        "retests":    retests,
    }

def render_lineage(lineage: dict, project_path: Path) -> str:
    from utils.status import get_status_breakdown, render_status_bar

    out = []
    out.append("🧬 Lineage Investigation\n")

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
            run_id = run['run_id']
            verb = run.get('verb', '(unknown)')
            run_path = project_path / "verbs" / run.get("verb_group", "Tests") / "data_dumps" / run_id
            noun_schema = None
            adverb_schema = None
            verb_key = verb

            try:
                from utils.disambiguation import get_verb_schema, get_noun_schema, get_adverb_schema
                verb_schema = get_verb_schema(project_path, verb)
                noun_type_ref = verb_schema.get("data_entry_schema", {}).get("set_up_inputs", {}).get("noun_type_ref")
                noun_schema = get_noun_schema(project_path, noun_type_ref)
                adverb_schema = get_adverb_schema(project_path, verb)
            except Exception:
                verb_schema = None

            raw_inputs = verb_schema.get("data_entry_schema", {}).get("upload_inputs", []) if verb_schema else []

            out.append(f"  • Run ID: {run_id} | Verb: {verb}")

            # Status bar
            try:
                status = get_status_breakdown(run_path, noun_schema, raw_inputs, adverb_schema, verb_key, project_path)
                bar = render_status_bar(status)
                out.append(f"    {bar}")
                for k, v in status.items():
                    out.append(f"      • {k}: {v}")
            except Exception as e:
                out.append(f"    ⚠️ Could not render status: {e}")

            # Matched noun instance (short version)
            for ref in run.get("referencing_nouns", []):
                ref_type = ref.get("_noun_type", "(unknown)")
                pk_field = ref.get("_primary_id_field")
                pk_value = ref.get(pk_field, "(no ID)") if pk_field else "(no ID)"
                out.append(f"    ↳ {ref_type}: {pk_field} = {pk_value}")
    else:
        out.append("  (none)")

    # --- Parents + Siblings (Family Tree style)
    out.append("\n⬆ Referencing Parents (and Siblings):")
    parents = lineage.get("parents", [])
    if parents:
        for parent in parents:
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
                    sib_keys = ', '.join(k for k in sib if not k.startswith("_"))
                    branch = "     │   " if i < len(siblings) - 1 else "     └── "
                    out.append(
                        f"{branch}↪️ {sib_type}: {sib_field} = {sib_id} | run: {run_id} | Fields: {sib_keys}"
                    )
            else:
                out.append("     └── No siblings found")
    else:
        out.append("  (none)")

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

def resolve_parents(project_path: Path, noun_type: str, instance: dict, seen_ids=None) -> list[dict]:
    """
    Resolve parent noun instances by identifying referenced nouns that themselves
    carry ActionRequirement adjectives. These are the upstream triggers for the current noun.

    Returns a list of dicts like:
        {
            "noun_type": <parent noun>,
            "noun_id": <parent id>,
            "action_requirement": <value>,  # from parent instance
            "siblings": <optional list of related nouns>,
        }
    """
    import json

    if seen_ids is None:
        seen_ids = set()

    parents = []

    noun_schema = dis.get_noun_schema(project_path, noun_type)
    if not noun_schema:
        return []

    fields = noun_schema.get("fields", {})
    for field_name, field_def in fields.items():
        if field_def.get("type") != "adjective":
            continue

        adjective_schema = dis.get_adjective_schema(project_path, field_name, applies_to=noun_type)
        if not adjective_schema:
            continue

        if adjective_schema.get("adjective_class") != "Reference":
            continue

        reference_noun = adjective_schema.get("reference_noun")
        if not reference_noun:
            continue

        parent_id = instance.get(field_name)
        if not parent_id or not isinstance(parent_id, str):
            continue

        parent_schema = dis.get_noun_schema(project_path, reference_noun)
        if not parent_schema:
            continue

        pk_field = parent_schema.get("primary_id_field")
        if not pk_field:
            continue

        try:
            items_path = dis.get_noun_items(project_path, reference_noun)
        except FileNotFoundError:
            continue

        with items_path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    candidate = json.loads(line.strip())
                except:
                    continue

                if candidate.get(pk_field) != parent_id:
                    continue

                parent_key = (reference_noun, parent_id)
                if parent_key in seen_ids:
                    continue
                seen_ids.add(parent_key)

                parent_fields = parent_schema.get("fields", {})
                ar_value = None

                for p_field, p_def in parent_fields.items():
                    if p_def.get("type") == "adjective" and p_def.get("adjective_class") == "ActionRequirement":
                        ar_value = candidate.get(p_field)
                        break

                if ar_value is None:
                    continue

                parent_entry = {
                    "noun_type": reference_noun,
                    "noun_id": parent_id,
                    "action_requirement": ar_value,
                    "siblings": []
                }

                grandparents = resolve_parents(project_path, reference_noun, candidate, seen_ids)
                parents.append(parent_entry)
                parents.extend(grandparents)

        # No need to check `found` — function continues silently if no parent match is found

    return parents

def resolve_siblings(
    project_path: Path,
    parent_noun_type: str,
    parent_id: str,
    ar_value: str,
    exclude_noun_type: str,
    exclude_noun_id: str
) -> list[dict]:
    siblings = []

    all_adjs = dis.load_schema(project_path, "adjective")
    ar_adj = next(
        (adj for adj in all_adjs
         if adj.get("adjective_class") == "ActionRequirement"
            and parent_noun_type in adj.get("applies_to", [])),
        None
    )
    if not ar_adj:
        return []

    verb_list = ar_adj.get("request_options", {}).get(ar_value, [])
    if not verb_list:
        return []

    for verb_name in verb_list:
        verb_def = dis.get_verb_schema(project_path, verb_name)
        if not verb_def:
            continue

        ref_noun_type = verb_def.get("data_entry_schema", {}) \
                                 .get("set_up_inputs", {}) \
                                 .get("noun_type_ref")
        if not ref_noun_type or ref_noun_type == exclude_noun_type:
            continue

        child_schema = dis.get_noun_schema(project_path, ref_noun_type)
        pk_field = child_schema.get("primary_id_field")
        if not pk_field:
            continue

        verb_group = verb_def.get("verb_group", "Tests")
        data_dir = project_path / "verbs" / verb_group / "data_dumps"
        if not data_dir.exists():
            continue

        for run_folder in sorted(data_dir.iterdir()):
            if not run_folder.is_dir():
                continue
            run_id = run_folder.name

            data_path = run_folder / "DataEntry.json"
            if not data_path.exists():
                continue

            try:
                rows = json.loads(data_path.read_text())
                if isinstance(rows, dict):
                    rows = [rows]
            except:
                continue

            for entry in rows:
                cleaned_keys = {
                    re.sub(r'[^a-z0-9]', '', k.lower()): k
                    for k in entry
                }
                cleaned_pk = re.sub(r'[^a-z0-9]', '', pk_field.lower())
                pk_key = cleaned_keys.get(cleaned_pk)
                pk_val = entry.get(pk_key) if pk_key else None

                if pk_val == exclude_noun_id:
                    continue

                if not any(isinstance(v, str) and v == parent_id for v in entry.values()):
                    continue

                sibling = dict(entry)
                sibling["_noun_type"] = ref_noun_type
                sibling["_primary_id_field"] = pk_key or pk_field
                sibling["_run_id"] = run_id

                siblings.append(sibling)

    return siblings

def resolve_overrides(
    project_path: Path,
    referencing_nouns: list[dict],
    noun_instance: dict | None = None,
    seen_run_ids: set = None
) -> list[dict]:
    if seen_run_ids is None:
        seen_run_ids = set()

    overrides = dis.load_override(project_path)
    retests = []

    def process_one(noun: dict) -> None:
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

            verb_group = dis.resolve_verb_group_from_test_type(project_path, test_type)
            if not verb_group:
                continue

            try:
                data_path = dis.get_verb_data_entry(project_path, verb_group, target_run)
                data = json.loads(data_path.read_text())
                if isinstance(data, dict):
                    data = [data]
            except:
                continue

            match_found = any(
                isinstance(entry.get(field), str) and entry.get(field) == val
                for entry in data for field in entry
            )

            if match_found:
                retests.append({
                    "noun_instance": {
                        "_noun_type": "Override",
                        "_primary_id_field": "run",
                        "run": target_run
                    },
                    "retest_of": run_id
                })

                new_noun_type = dis.resolve_noun_type_from_override(project_path, override)
                if not new_noun_type:
                    continue

                noun_schema = dis.get_noun_schema(project_path, new_noun_type)
                if not noun_schema:
                    continue

                pk_field = noun_schema.get("primary_id_field")
                if not pk_field:
                    continue

                new_nouns = []
                for entry in data:
                    if pk_field in entry:
                        enriched = {
                            **entry,
                            "_noun_type": new_noun_type,
                            "_primary_id_field": pk_field,
                            "_runID": target_run
                        }
                        new_nouns.append(enriched)

                retests.extend(resolve_overrides(
                    project_path,
                    new_nouns,
                    seen_run_ids=seen_run_ids
                ))

    for ref in referencing_nouns:
        process_one(ref)

    if noun_instance:
        process_one(noun_instance)

    return retests

def find_run_references_to_noun_instance(project_path: Path, noun_type: str, instance_id: str) -> tuple[dict, list[dict]]:
    """
    Finds all runs that reference a specific noun instance.

    Returns:
    - run_map: dict[run_id] = { run_id, verb, referencing_nouns }
    - referencing_nouns: list[dict] — each dict is a noun entry that referenced the instance_id
    """
    results = {}
    flat_referencing_nouns = []

    references = dis.find_non_id_field_value(project_path, noun_type)

    adjective_refs = [
        ref for ref in references
        if ref["word_type"] == "adjective" and not ref["match_path"].startswith("applies_to")
    ]

    noun_schemas = dis.load_schema(project_path, "noun")

    for ref in adjective_refs:
        adjective_field = ref["schema_name"]
        applies_to_list = ref["schema"].get("applies_to", [])

        for target_noun_type in applies_to_list:
            noun_schema = noun_schemas.get(target_noun_type)
            if not noun_schema:
                continue

            pk_field = noun_schema.get("primary_id_field")
            if not pk_field:
                continue

            try:
                items_path = dis.get_noun_items(project_path, target_noun_type)
            except FileNotFoundError:
                continue

            with items_path.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line.strip())
                    except:
                        continue

                    ref_val = row.get(adjective_field)
                    if not ref_val:
                        continue

                    match = False
                    if isinstance(ref_val, str):
                        match = ref_val == instance_id
                    elif isinstance(ref_val, list):
                        match = instance_id in ref_val

                    if not match:
                        continue

                    run_id = row.get("_runID")
                    if not run_id:
                        continue

                    resolved_verb = dis.resolve_run_id_to_test_type(project_path, run_id) or "(unknown)"

                    annotated = dict(row)
                    annotated["_noun_type"] = target_noun_type
                    annotated["_primary_id_field"] = pk_field
                    annotated["_runID"] = run_id

                    # Add to flat list
                    flat_referencing_nouns.append(annotated)

                    # Add to results dict
                    if run_id not in results:
                        results[run_id] = {
                            "run_id": run_id,
                            "verb": resolved_verb,
                            "referencing_nouns": []
                        }

                    results[run_id]["referencing_nouns"].append(annotated)

    return results, flat_referencing_nouns

if __name__ == "__main__":
    project_arg = sys.argv[1] if len(sys.argv)>1 else None
    noun_arg    = sys.argv[2] if len(sys.argv)>2 else None

    projects = [p.name for p in Path("projects").iterdir() if p.is_dir()]
    project = prompt_if_missing(project_arg, projects, label="project")

    noun_types_path = Path("projects")/project/"noun_types.json"
    if not noun_types_path.exists():
        print(f"❌ No noun_types.json found in '{project}'"); sys.exit(1)

    noun_types = list(json.load(open(noun_types_path)).keys())
    noun_type = prompt_if_missing(noun_arg, noun_types, label="noun type")

    view_main(project, noun_type)
