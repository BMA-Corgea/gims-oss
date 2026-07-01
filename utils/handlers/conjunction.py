import json
from pathlib import Path
from datetime import datetime
from utils.interface import menu_prompt, indexed_choice
from utils.semantics import is_valid_date

OVERRIDE_LOG_FILENAME = "override.json"
STATUS_TYPES = ["Error", "Exception", "Cancelled", "Notification"]

def prompt_status_overrides(
    existing_overrides: list[dict] | None = None,
    project_name:       str             = ""
) -> list[dict]:
    project_root    = Path("projects") / project_name
    noun_types_path = project_root / "noun_types.json"
    if noun_types_path.exists():
        all_nouns    = json.load(noun_types_path.open())
        noun_choices = list(all_nouns.keys()) + ["Run"]
    else:
        all_nouns    = {}
        noun_choices = []

    print("\n📈 Define status overrides for this verb.")
    overrides = existing_overrides[:] if existing_overrides else []

    def format_field(f):
        if isinstance(f, str):
            return f
        if isinstance(f, dict) and f.get("type") == "reference":
            return f"{f['label']}→{f['reference_noun']} ({f.get('mode')})"
        return "unknown"

    while True:
        print("\nCurrent status overrides:")
        if not overrides:
            print("  (none yet)")
        else:
            for i, o in enumerate(overrides):
                if not isinstance(o, dict):
                    print(f"[{i}] ❌ Invalid override: {o} (expected dict)")
                    continue
                fields = ', '.join(format_field(f) for f in o.get("fields", []))
                print(f"[{i}] {o['name']} → {o['status']} (fields: {fields})")

        action = menu_prompt({
            'a': 'Add override',
            'e': 'Edit override',
            'd': 'Delete override',
            'q': 'Finish'
        })
        if action == 'q':
            return overrides

        if action == 'd':
            if not overrides:
                print("❌ No overrides to delete.")
                continue
            idx = indexed_choice([o['name'] for o in overrides], "Choose override to delete")
            if idx is not None:
                overrides.pop(idx)
            continue

        if action == 'e':
            idx = indexed_choice([o['name'] for o in overrides], "Choose override to edit")
            if idx is not None:
                overrides[idx] = prompt_status_overrides_single(overrides[idx], project_name)
            continue

        # Add new override
        name = input("Override type (e.g., 'Quarantine'): ").strip()
        idx  = indexed_choice(STATUS_TYPES, "What status does this override trigger?")
        if idx is None:
            continue
        status = STATUS_TYPES[idx]

        field_choices    = ["note", "initials", "date", "reference"]
        required_fields: list[str|dict] = []

        while True:
            curr = ', '.join(format_field(f) for f in required_fields) or "(none)"
            print(f"Current required fields: {curr}")
            fa = menu_prompt({'a': 'Add field', 'd': 'Delete field', 'q': 'Done'})
            if fa == 'q':
                break

            if fa == 'd':
                if not required_fields:
                    print("❌ No fields to remove.")
                    continue
                rem = indexed_choice([format_field(f) for f in required_fields],
                                     "Choose field to remove")
                if rem is not None:
                    required_fields.pop(rem)
                continue

            fc = indexed_choice(field_choices, "Choose field to add")
            if fc is None:
                continue
            fld = field_choices[fc]

            if fld == "reference":
                label = input("📝 Field label (e.g., 'previous_runs'): ").strip()

                # enforce indexed noun pick
                if not noun_choices:
                    print("❌ No noun types defined. Cannot add reference.")
                    continue
                ni = indexed_choice(noun_choices, "Select noun type to reference")
                if ni is None:
                    continue
                noun = noun_choices[ni]
                noun_schema = all_nouns.get(noun, {}).get("fields", {})

                mi = indexed_choice(["Reference", "ReferenceList"], "Single or list reference?")
                if mi is None:
                    continue
                mode = "Reference" if mi == 0 else "ReferenceList"

                filters: dict[str, list|str] = {}
                if noun == "Run":
                    status_options  = ["Error", "Exception", "Cancelled",
                                       "Complete", "Pending", "Missing Required Fields"]
                    status_filters: list[str] = []
                    while True:
                        print("Currently selected statuses:",
                              ", ".join(status_filters) or "(none)")
                        choice = menu_prompt({
                            "a": "Add status filter",
                            "d": "Remove status filter",
                            "q": "Done"
                        })
                        if choice == "q":
                            break
                        if choice == "a":
                            avail = [s for s in status_options
                                     if s not in status_filters]
                            if not avail:
                                print("✅ All statuses selected.")
                                continue
                            si = indexed_choice(avail, "Select a status to add")
                            if si is not None:
                                status_filters.append(avail[si])
                        elif choice == "d":
                            if not status_filters:
                                print("❌ No statuses to remove.")
                                continue
                            si = indexed_choice(status_filters, "Select a status to remove")
                            if si is not None:
                                status_filters.pop(si)
                    filters["status"] = status_filters
                else:
                    field_names = list(noun_schema.keys())
                    if not field_names:
                        print("⚠️ No fields on that noun; skipping filters.")
                    else:
                        while True:
                            opts = field_names + ["Done"]
                            fi = indexed_choice(opts, "Choose field to filter by")
                            if fi is None or fi == len(field_names):
                                break
                            fn = field_names[fi]
                            val = input(f"Value for '{fn}': ").strip()
                            filters[fn] = val

                required_fields.append({
                    "type":           "reference",
                    "mode":           mode,
                    "label":          label,
                    "reference_noun": noun,
                    "filters":        filters
                })

            else:
                if fld not in required_fields:
                    required_fields.append(fld)

        overrides.append({
            "name":   name,
            "status": status,
            "fields": required_fields
        })


def prompt_status_overrides_single(
    existing:     dict,
    project_name: str  = ""
) -> dict:
    project_root    = Path("projects") / project_name
    noun_types_path = project_root / "noun_types.json"
    if noun_types_path.exists():
        all_nouns    = json.load(noun_types_path.open())
        noun_choices = list(all_nouns.keys())
    else:
        all_nouns    = {}
        noun_choices = []

    def format_field(f):
        if isinstance(f, str):
            return f
        if isinstance(f, dict) and f.get("type") == "reference":
            return f"{f['label']}→{f['reference_noun']} ({f.get('mode')})"
        return "unknown"

    name = input(f"Override type [{existing['name']}]: ").strip() or existing['name']
    idx  = indexed_choice(STATUS_TYPES, f"Status triggered [{existing['status']}]")
    status = STATUS_TYPES[idx] if idx is not None else existing['status']

    field_choices   = ["note", "initials", "date", "reference"]
    required_fields = existing.get("fields", [])[:]

    while True:
        curr = ', '.join(format_field(f) for f in required_fields) or "(none)"
        print(f"\nCurrent required fields: {curr}")
        fa = menu_prompt({'a': 'Add field', 'd': 'Delete field', 'q': 'Done'})
        if fa == 'q':
            break

        if fa == 'd':
            if not required_fields:
                print("❌ No fields to remove.")
                continue
            rem = indexed_choice([format_field(f) for f in required_fields],
                                 "Choose field to remove")
            if rem is not None:
                required_fields.pop(rem)
            continue

        fc = indexed_choice(field_choices, "Choose field to add")
        if fc is None:
            continue
        fld = field_choices[fc]

        if fld == "reference":
            label = input("📝 Field label (e.g., 'previous_runs'): ").strip()

            if not noun_choices:
                print("❌ No noun types defined. Cannot add reference.")
                continue
            ni = indexed_choice(noun_choices, "Select noun type to reference")
            if ni is None:
                continue
            noun = noun_choices[ni]
            noun_schema = all_nouns.get(noun, {}).get("fields", {})

            mi = indexed_choice(["Reference", "ReferenceList"], "Single or list reference?")
            if mi is None:
                continue
            mode = "Reference" if mi == 0 else "ReferenceList"

            filters: dict[str, list|str] = {}
            if noun == "Run":
                raw = input("Filter runs by status (comma-separated, e.g. Exception,Error): ").strip()
                statuses = [s.strip() for s in raw.split(',') if s.strip()]
                filters["status"] = statuses
            else:
                field_names = list(noun_schema.keys())
                if not field_names:
                    print("⚠️ No fields on that noun; skipping filters.")
                else:
                    while True:
                        opts = field_names + ["Done"]
                        fi = indexed_choice(opts, "Choose field to filter by")
                        if fi is None or fi == len(field_names):
                            break
                        fn = field_names[fi]
                        val = input(f"Value for '{fn}': ").strip()
                        filters[fn] = val

            required_fields.append({
                "type":           "reference",
                "mode":           mode,
                "label":          label,
                "reference_noun": noun,
                "filters":        filters
            })

        else:
            if fld not in required_fields:
                required_fields.append(fld)

    return {
        "name":   name,
        "status": status,
        "fields": required_fields
    }

def get_conjunction(run_path: Path) -> dict | None:
    """Load conjunction (if any) from Status.json"""
    status_file = run_path / "Status.json"
    if not status_file.exists():
        return None
    with open(status_file) as f:
        data = json.load(f)
    return data.get("conjunction")

def save_conjunction(run_path: Path, project_path: Path, run_id: str, verb_group: str):
    """Prompt user to enter or edit a conjunction (override receipt)"""
    status_file = run_path / "Status.json"
    status_data = {}
    if status_file.exists():
        with open(status_file) as f:
            status_data = json.load(f)

    existing = status_data.get("conjunction", {})

    print("\n🌀 Add/Edit Override Receipt (Conjunction):")

    # Prompt override type
    override_type = input(f"Override type [{existing.get('override_type', '')}]: ").strip() or existing.get("override_type")

    # Prompt initials
    initials = input(f"Your initials [{existing.get('initials', '')}]: ").strip() or existing.get("initials")

    # Prompt date
    date_val = input(f"Date (YYYY-MM-DD) [{existing.get('date', '') or datetime.today().strftime('%Y-%m-%d')}]: ").strip()
    if not date_val:
        date_val = datetime.today().strftime('%Y-%m-%d')
    while not is_valid_date(date_val, "yyyy-mm-dd"):
        print("❌ Invalid date. Format must be YYYY-MM-DD.")
        date_val = input("Date (YYYY-MM-DD): ").strip()

    # Prompt note
    note = input(f"Note [{existing.get('note', '')}]: ").strip() or existing.get("note")

    # Prompt optional linked run
    linked_run = input(f"Linked run ID (optional) [{existing.get('linked_run', '')}]: ").strip() or existing.get("linked_run")

    conjunction_data = {
        "override_type": override_type,
        "initials": initials,
        "date": date_val,
        "note": note,
        "linked_run": linked_run
    }

    status_data["conjunction"] = conjunction_data

    with open(status_file, "w") as f:
        json.dump(status_data, f, indent=2)

    _log_global_override(project_path, run_id, verb_group, conjunction_data)
    print("✅ Conjunction saved and override logged.")


def delete_conjunction(run_path: Path):
    """Delete conjunction from Status.json"""
    status_file = run_path / "Status.json"
    if not status_file.exists():
        print("❌ No Status.json found.")
        return
    with open(status_file) as f:
        data = json.load(f)
    if "conjunction" not in data:
        print("⚠️ No conjunction present.")
        return
    del data["conjunction"]
    with open(status_file, "w") as f:
        json.dump(data, f, indent=2)
    print("🗑️ Conjunction deleted.")


def _log_global_override(project_path: Path, run_id: str, verb_group: str, conj: dict):
    """Append override receipt to override.json"""
    override_file = project_path / OVERRIDE_LOG_FILENAME
    record = conj.copy()
    record.update({
        "run_id": run_id,
        "verb_group": verb_group
    })

    if override_file.exists():
        with open(override_file) as f:
            overrides = json.load(f)
    else:
        overrides = []

    overrides.append(record)

    with open(override_file, "w") as f:
        json.dump(overrides, f, indent=2)

def manage_conjunctions(
    project_path: Path,
    dump_root:   Path,
    run_entry:   dict
):

    status_file = dump_root / "Status.json"
    if not status_file.exists():
        print("⚠️ No Status.json found.")
        return

    status_data = json.loads(status_file.read_text())

    # 1) Determine verb key from the run entry
    verb_key = run_entry.get("test_type") or run_entry.get("verb")
    if not verb_key:
        print("❌ Can't determine verb for this run.")
        return

    # 2) Load verb_types.json and grab the exact config
    verb_defs   = json.loads((project_path / "verb_types.json").read_text())
    verb_config = verb_defs.get(verb_key)
    if not verb_config:
        print(f"❌ No verb config found for '{verb_key}'")
        return

    override_options = verb_config.get("status_values", [])

    # 3) Show existing conjunctions (overrides)
    overrides = status_data.get("conjunctions", [])
    print(f"\n📎 {len(overrides)} override(s) found.\n")
    for i, o in enumerate(overrides):
        # Base line: type → status
        desc = f"{o.get('type','?')} → {o.get('status','?')}"
        details = []

        for key, val in o.items():
            # skip type/status
            if key in ("type", "status"):
                continue

            # special formatting for resolution (list of dicts)
            if key == "resolution" and isinstance(val, list):
                notes = [entry.get("note", "") for entry in val if isinstance(entry, dict)]
                if notes:
                    details.append("resolved: " + "; ".join(notes))
                continue

            # if it's a list of primitives, join them
            if isinstance(val, list) and all(not isinstance(x, dict) for x in val):
                details.append(f"{key}: {', '.join(str(x) for x in val)}")
            # skip lists of dicts (we handled resolution above)
            elif isinstance(val, list):
                continue
            else:
                details.append(f"{key}: {val}")

        if details:
            desc += " (" + "; ".join(details) + ")"

        print(f"[{i}] {desc}")

    # 4) Prompt action (add ‘r’: resolve)
    action = menu_prompt({
        "a": "Add override",
        "d": "Delete override",
        "r": "Resolve override",
        "q": "Back"
    })
    if action == "q":
        return

    # 5) Delete logic
    if action == "d":
        idx = input("Index to delete: ").strip()
        if idx.isdigit() and int(idx) < len(overrides):
            overrides.pop(int(idx))
            status_data["conjunctions"] = overrides
            status_file.write_text(json.dumps(status_data, indent=2))
            print("🗑️ Override deleted.")
        return

    # 6) Resolve logic
    elif action == "r":
        if not overrides:
            print("⚠️ No overrides to resolve.")
            return

        opts = [f"{o['type']} → {o.get('status')}" for o in overrides]
        idx = indexed_choice(opts, "Select override to resolve")
        if idx is None:
            return

        sel = overrides[idx]
        if "resolution" in sel:
            print("⚠️ Already resolved.")
            return

        note = input("Resolution note: ").strip()
        if not note:
            print("❌ Note cannot be empty.")
            return

        # 1) Attach locally
        sel["resolution"] = [{"note": note}]
        status_file.write_text(json.dumps(status_data, indent=2))
        print("✅ Override marked as resolved.")

        # 2) Load global log
        override_log = project_path / "override.json"
        try:
            global_entries = json.loads(override_log.read_text()) if override_log.exists() else []
        except json.JSONDecodeError:
            global_entries = []

        # 3) Filter to this run+verb
        matching = [
            (i, e)
            for i, e in enumerate(global_entries)
            if e.get("run") == dump_root.name and e.get("verb") == verb_key
        ]

        if not matching:
            print("⚠️ No global entries for this run+verb; adding new.")
            entry = {"run": dump_root.name, "verb": verb_key, **sel}
            global_entries.append(entry)
        else:
            # 4) Update the one at the same index
            #    (if there are multiple, pick the same idx)
            #    falling back to the last one if idx out of range
            try:
                global_idx = matching[idx][0]
            except IndexError:
                global_idx = matching[-1][0]
            global_entries[global_idx]["resolution"] = [{"note": note}]

        # 5) Save it back
        override_log.write_text(json.dumps(global_entries, indent=2))
        return

    # 7 ) Add logic
    if action == "a":
        if not override_options:
            print("⚠️ No status_overrides defined for this verb.")
            return

        verb_name  = run_entry.get("test_type") or run_entry.get("verb")
        verb_group = dump_root.parents[1].name  # e.g., "Tests"

        names = [opt["name"] for opt in override_options]
        pick  = indexed_choice(names, "Choose override to apply")
        if pick is None:
            return
        chosen = override_options[pick]

        new_override = {
            "type":   chosen["name"],
            "status": chosen["status"],
        }

        for field in chosen.get("fields", []):
            # --- simple‐string fields ---
            if isinstance(field, str):
                key   = field
                label = field.replace("_", " ").title()

                # If it's the date field, loop until valid
                if key.lower() == "date":
                    while True:
                        val = input(f"{label} (yyyy-mm-dd): ").strip()
                        if is_valid_date(val, "yyyy-mm-dd"):
                            break
                        print("❌ Invalid date format. Use yyyy-mm-dd.")
                else:
                    val = input(f"{label}: ").strip()

                new_override[key] = val
                continue

            # --- structured field ---
            ftype    = field.get("type")
            label    = field.get("label", field.get("name", "Field"))
            ref_noun = field.get("reference_noun")
            mode     = field.get("mode", "Reference")

            # pseudo‐noun Run
            if ftype == "reference" and ref_noun == "Run":
                log_file = project_path / "verbs" / verb_group / f"{verb_group}_log.jsonl"
                if not log_file.exists():
                    print(f"❌ Run log not found: {log_file}")
                    return
                with open(log_file) as lf:
                    runs = [json.loads(line) for line in lf if line.strip()]

                candidates = [r["run_ID"] for r in runs if r.get("test_type") == verb_name]
                if not candidates:
                    print("❌ No matching runs found.")
                    return

                if mode == "ReferenceList":
                    sel = []
                    while True:
                        opts = candidates + ["Done"]
                        idx = indexed_choice(opts, f"{label} (select multiple)")
                        if idx is None or idx == len(candidates):
                            break
                        sel.append(candidates[idx])
                    new_override[label] = sel
                else:
                    idx = indexed_choice(candidates, f"{label}: ")
                    if idx is None:
                        return
                    new_override[label] = candidates[idx]
                continue

            # normal noun reference
            if ftype == "reference":
                items_file = project_path / "nouns" / ref_noun / "items.jsonl"
                if not items_file.exists():
                    print(f"❌ Reference not found: {items_file}")
                    return
                with open(items_file) as nf:
                    items = [json.loads(l) for l in nf if l.strip()]

                with open(project_path / "noun_types.json") as ntf:
                    defs = json.load(ntf)
                pid_field = defs.get(ref_noun, {}).get("primary_id_field", "id")
                candidates = [it.get(pid_field) for it in items]

                if mode == "ReferenceList":
                    sel = []
                    while True:
                        opts = candidates + ["Done"]
                        idx = indexed_choice(opts, f"{label} (select multiple)")
                        if idx is None or idx == len(candidates):
                            break
                        sel.append(candidates[idx])
                    new_override[label] = sel
                else:
                    idx = indexed_choice(candidates, f"{label}: ")
                    if idx is None:
                        return
                    new_override[label] = candidates[idx]
                continue

            # structured date field
            if ftype == "date":
                while True:
                    val = input(f"{label} (yyyy-mm-dd): ").strip()
                    if is_valid_date(val, "yyyy-mm-dd"):
                        break
                    print("❌ Invalid date format. Use yyyy-mm-dd.")
                new_override[label] = val
                continue

            # fallback
            val = input(f"{label}: ").strip()
            new_override[label] = val

        # save back to Status.json
        status_data.setdefault("conjunctions", []).append(new_override)
        (dump_root / "Status.json").write_text(json.dumps(status_data, indent=2))
        print("✅ Override saved.")

        # 8) Save back into Status.json
        status_data["conjunctions"] = overrides
        status_file.write_text(json.dumps(status_data, indent=2))

        # 9) And append to project-wide override log (overrides.json)
        override_log = project_path / "override.json"
        logs = []
        if override_log.exists():
            try:
                logs = json.loads(override_log.read_text())
            except json.JSONDecodeError:
                logs = []

        logs.append({
            "run":  dump_root.name,
            "verb": verb_key,
            **new_override      # <-- use the same dict you built above
        })
        override_log.write_text(json.dumps(logs, indent=2))

        print("✅ Override saved.")