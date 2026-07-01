# utils/data_dump.py

import json
import csv
from pathlib import Path

from api.i_o import load_schema  # S3-aware *_types.json reader (replaces json.load(open(...)))
from utils.interface import indexed_choice, menu_prompt
from utils.file_ops import upload_file_to_folder, _print_csv_or_xlsx
from utils.spreadsheet_ui import run_spreadsheet_ui
from utils.adjective_ui_loader import load_adjective_field_config
from utils.status import get_status_breakdown, render_status_bar
from utils.handlers.adverb import (
    ReferenceAdverb,
    ReferenceListAdverb,
    TagAdverb,
    AttributeAdverb,
    load_adverb_handler,
)
from utils.handlers.conjunction import manage_conjunctions
from utils.runner_env import execute_parser_runner
from datetime import datetime
import traceback
from utils.status_ui import print_colored_status

def load_full_verb_def(project_path: Path, verb_key: str) -> dict:
    """
    Loads the entire verb definition (not just data_entry_schema)
    from verb_types.json.
    """
    with open(project_path / "verb_types.json") as vf:
        all_verbs = json.load(vf)
    return all_verbs.get(verb_key, {})

def load_verb_schema(project_path: Path, verb_key: str) -> dict:
    """
    Load the data_entry_schema for the given verb from verb_types.json.
    Returns {} if missing.
    """
    verb_types_path = project_path / "verb_types.json"
    if not verb_types_path.exists():
        return {}

    with open(verb_types_path) as f:
        verb_types = json.load(f)

    return verb_types.get(verb_key, {}).get("data_entry_schema", {})


def _print_csv(csv_path: Path):
    """
    Pretty-print a CSV file with aligned columns and visible borders.
    """
    if not csv_path.exists():
        print("⚠️ File not found:", csv_path)
        return

    with open(csv_path, newline='') as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        print("⚠️ Empty CSV file.")
        return

    # Compute column widths
    col_widths = [max(len(str(cell)) for cell in column) for column in zip(*rows)]

    def format_row(row):
        return "│ " + " │ ".join(
            str(cell).ljust(width) for cell, width in zip(row, col_widths)
        ) + " │"

    # Build output
    header = format_row(rows[0])
    divider = "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤"
    data_rows = [format_row(row) for row in rows[1:]]

    # Print table
    print("┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐")
    print(header)
    print(divider)
    for row in data_rows:
        print(row)
    print("└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘")

def toggle_interpretation_approval(status_path: Path):
    """
    Toggle the 'manual_approval' flag in Status.json for interpretation.
    """
    if not status_path.exists():
        status_data = {}
    else:
        try:
            status_data = json.loads(status_path.read_text())
        except json.JSONDecodeError:
            status_data = {}

    interp = status_data.get("interpretation", {})
    current = interp.get("manual_approval", False)

    print(f"🔁 Current status: {'✅ Manually Completed' if current else '❌ Not Marked'}")
    choice = menu_prompt({
        "y": "yes — toggle to opposite",
        "n": "no — leave unchanged"
    })

    if choice == "y":
        interp["manual_approval"] = not current
        status_data["interpretation"] = interp
        status_path.write_text(json.dumps(status_data, indent=2))
        print(f"✅ Interpretation manually marked as {'complete' if interp['manual_approval'] else 'incomplete'}.")
    else:
        print("❎ No changes made.")

def _print_json(json_path: Path):
    """
    Pretty-print a JSON object (one key:value per line), or warn if missing/empty.
    Supports both dict- and list-rooted JSON.
    """
    if not json_path.exists():
        print("⚠️ File not found:", json_path)
        return

    try:
        data = json.loads(json_path.read_text())
    except json.JSONDecodeError:
        print("❌ Corrupt JSON:", json_path)
        return

    if not data:
        print("⚠️ Empty JSON file.")
        return

    # Support both dict and list roots
    if isinstance(data, dict):
        for k, v in data.items():
            print(f"{k}: {v}")
    elif isinstance(data, list):
        print(f"📋 JSON List with {len(data)} items.")
        for i, entry in enumerate(data):
            print(f"[{i}] {entry}")
    else:
        print(f"❌ Unsupported JSON root type: {type(data).__name__}")

def render_status_breakdown(
    project_path: Path,
    run_entry: dict,
    dump_root: Path,
    raw_inputs: list[str] = None,
    adverb_schema: dict | None = None,
    verb_key: str | None = None,
):
    """Load & print the status breakdown once."""
    # … your existing code for steps 1–4, but only the printing part …
    with open(project_path / "verb_types.json") as f:
        verb_config = json.load(f)
    verb_type = run_entry.get("verb") or run_entry.get("test_type")
    noun_type_name = verb_config.get(verb_type, {}) \
        .get("data_entry_schema", {}) \
        .get("set_up_inputs", {}) \
        .get("noun_type_ref")
    noun_schema = None
    if noun_type_name:
        ntp = project_path / "noun_types.json"
        if ntp.exists():
            noun_schema = load_schema(project_path, "noun").get(noun_type_name)

    breakdown = get_status_breakdown(
        dump_root,
        noun_schema=noun_schema,
        raw_inputs=raw_inputs or [],
        adverb_schema=adverb_schema,
        verb_key=verb_key,
        project_path=project_path
    )

    print("\n📌 Status:\n")
    print(render_status_bar(breakdown))
    print()
    print_colored_status(breakdown)
    print()


def show_status_menu(
    project_path: Path,
    run_entry: dict,
    dump_root: Path,
    raw_inputs: list[str] = None,
    adverb_schema: dict | None = None,
    verb_key: str = None
):
    """
    Presents an indexed menu for status actions:
      [0] View Status Breakdown
      [1] Manage Overrides / Conjunctions
      [2] Back
    """
    while True:
        options = [
            "🔍 View Status Breakdown",
            "🌀 Manage Overrides / Conjunctions",
            "❌ Back"
        ]
        choice = indexed_choice(options, "Select action")
        if choice is None or choice == 2:
            # q or “Back”
            break

        if choice == 0:
            render_status_breakdown(
                project_path,
                run_entry,
                dump_root,
                raw_inputs=raw_inputs,
                adverb_schema=adverb_schema,
                verb_key=verb_key
            )
        elif choice == 1:
            manage_conjunctions(project_path, dump_root, run_entry)
    
def open_data_dump(project_path: Path, verb_group: str, run_entry: dict):
    """
    Explore (and re‐create/update) the data dump directory for a given run.

    Expectation: `run_entry` must contain either "test_type" or "verb" to identify the verb_key.
    """
    # 1) Determine primary_id
    primary_id = (
        run_entry.get("run_ID")
        or run_entry.get("id")
        or next(iter(run_entry.values()), "")
    )
    if not primary_id:
        print("❗ No primary ID found in run entry.")
        return

    dump_root = project_path / "verbs" / verb_group / "data_dumps" / str(primary_id)
    dump_root.mkdir(parents=True, exist_ok=True)

    # 2) Determine verb_key
    verb_key = run_entry.get("test_type") or run_entry.get("verb")
    if not verb_key:
        print("❗ This run entry has no ‘test_type’ or ‘verb’ field. Cannot load schema.")
        return

    # 3) Load data_entry_schema
    schema = load_verb_schema(project_path, verb_key)

    # 3a) Instructions.md
    instr_lines = schema.get("instructions", [])
    instructions_md = "# Instructions\n\n"
    if instr_lines:
        instructions_md += "\n".join(
            f"{i+1}. {line}" for i, line in enumerate(instr_lines)
        ) + "\n"
    else:
        instructions_md += "(No instructions defined)\n"
    (dump_root / "Instructions.md").write_text(instructions_md)

    # 3c) Raw data inputs → create one subfolder per expected tab
    raw_inputs = schema.get("raw_data_inputs", [])
    for tab in raw_inputs:
        pocket = dump_root / tab
        pocket.mkdir(parents=True, exist_ok=True)

    # 3d) DataEntry.json
    data_entry_path = dump_root / "DataEntry.json"
    if not data_entry_path.exists():
        data_entry_path.write_text(json.dumps([], indent=2))

    # 3e) Interpretation CSVs
    interpretation = schema.get("interpretation", {})
    tabs = interpretation.get("tabs", [])
    if isinstance(tabs, dict):
        tabs = list(tabs.keys())
    for tab in tabs:
        fpath = dump_root / f"{tab}.csv"
        if not fpath.exists():
            fpath.write_text("")

    # 3f) Adverbs.json
    adv_schema = schema.get("adverb_schema", {})
    adv_path = dump_root / "adverbs.json"
    if not adv_path.exists():
        adv_path.write_text(json.dumps({k: "" for k in adv_schema.keys()}, indent=2))

    # 3g) Status.json (replaces Status.txt)
    status_path = dump_root / "Status.json"
    if not status_path.exists():
        status_path.write_text(json.dumps({
            "interpretation": {
                "manual_approval": False
            }
        }, indent=2))

    # 4) CLI menu
    while True:
        print(f"\n📂 Viewing data dump for run: {primary_id}\n")

        # Reload both data_entry and adverb schemas
        verb_def     = load_full_verb_def(project_path, verb_key)
        data_schema  = verb_def.get("data_entry_schema", {})
        adv_schema   = verb_def.get("adverb_schema", {})

        raw_inputs   = data_schema.get("raw_data_inputs", [])
        interpretation = data_schema.get("interpretation", {})
        tabs = interpretation.get("tabs", [])
        if isinstance(tabs, dict):
            tabs = list(tabs.keys())

        options = []
        actions = []
        
        # 4a) Instructions
        options.append("📘 Instructions")
        actions.append(lambda: print(
            "\n📘 Instructions:\n\n" + (dump_root / "Instructions.md").read_text()
        ))

        # 4c) Raw Data Input pockets
        for tab in raw_inputs:
            label = f"📊 Raw Data: {tab}"
            options.append(label)

            def make_raw_handler(p=tab):
                """
                For each raw-data pocket, hand off the run root;
                handle_raw_data_zone will append the pocket folder.
                """
                return lambda: handle_raw_data_zone(zone_name=p, run_path=dump_root)

            actions.append(make_raw_handler())

        # 4d) Data Entry

        # Load setup schema
        setup_schema = load_verb_schema(project_path, verb_key).get("set_up_inputs", {})
        noun_type_ref = setup_schema.get("noun_type_ref", "")
        adjective_config = load_adjective_field_config(project_path, noun_type_ref)

        # 🆕 Retest targets injection
        overrides_path = project_path / "override.json"
        overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else []

        # Check if current run is a retest
        retest_override = next(
            (o for o in overrides if o.get("run") == primary_id and o.get("type") == "Is a retest"),
            None
        )

        retest_targets = []
        if retest_override:
            referenced_runs = retest_override.get("retest of", [])
            verb_types = load_schema(project_path, "verb")

            for prev_run in referenced_runs:
                for vg in (project_path / "verbs").iterdir():
                    log_path = vg / f"{vg.name}_log.jsonl"
                    if not log_path.exists():
                        continue

                    with log_path.open() as f:
                        for line in f:
                            entry = json.loads(line)
                            if entry.get("run_ID") == prev_run:
                                test_type = entry.get("test_type")
                                verb_cfg = verb_types.get(test_type, {})
                                noun_type_r = verb_cfg.get("data_entry_schema", {}).get("set_up_inputs", {}).get("noun_type_ref")
                                if not noun_type_r:
                                    continue

                                items_path = project_path / "nouns" / noun_type_r / "items.jsonl"
                                if not items_path.exists():
                                    continue

                                noun_types = load_schema(project_path, "noun")
                                pid_field = noun_types.get(noun_type_r, {}).get("primary_id_field")

                                with items_path.open() as nf:
                                    for iline in nf:
                                        ni = json.loads(iline)
                                        if ni.get("_runID") == prev_run:
                                            pid = ni.get(pid_field)
                                            if pid:
                                                retest_targets.append(pid)

        # Inject retest_targets into setup_schema if found
        setup_schema["retest_targets"] = retest_targets

        options.append("📝 Data Entry")
        actions.append(lambda: run_spreadsheet_ui(
            project_path,
            setup_schema,
            dump_root / "DataEntry.json",
            adjective_config=adjective_config,
            run_id=primary_id
        ))

        # ✒️ Adverbs (only if there *is* an adverb_schema)
        if adv_schema:
            options.append("✒️ Adverbs")
            actions.append(lambda: handle_adverb_zone(
                project_path,
                dump_root,
                verb_key,
                adv_schema
            ))

        # 4f) Interpretation zone
        interp = schema.get("interpretation")
        if interp and "tabs" in interp:
            options.append("📐 Interpretation")
            actions.append(lambda: handle_interpretation_zone(
                project_path,
                dump_root,
                verb_key,              # already extracted earlier
                str(primary_id),       # run ID
                interp
            ))

        # 4g) Status
        options.append("📌 Status")
        actions.append(lambda: show_status_menu(
            project_path,
            run_entry,
            dump_root,
            raw_inputs=raw_inputs,
            adverb_schema=adv_schema,  # use the correctly loaded adv_schema here
            verb_key=verb_key
        ))

        # 4h) Quit
        options.append("❌ Quit")
        actions.append(lambda: None)

        idx = indexed_choice(options, "Select view")
        if idx is None or idx == len(actions) - 1:
            break

        try:
            actions[idx]()
        except Exception as e:
            print(f"❌ Error displaying file: {e}")

def handle_interpretation_zone(project_path, run_path, verb_key, run_id, interp_schema):
    method = interp_schema.get("method")
    tabs = interp_schema.get("tabs", [])

    while True:
        print("\n📐 Interpretation Menu:")
        action = menu_prompt({
            "v": "view/edit individual tabs",
            "r": "run parser(s)",
            "q": "quit"
        })

        if action == "q":
            break

        elif action == "v":
            handle_interpretation_tabs(run_path, tabs, method)

        elif action == "r":
            run_parsers_with_error_handling(
                project_path,
                verb_key,
                run_id,
                run_path,
                interp_schema.get("parsers", [])
            )

def handle_interpretation_tabs(run_path: Path, tabs: list[str], method: str):
    while True:
        opts = []
        for tab in tabs:
            status = "✅" if (run_path / f"{tab}.csv").exists() else "❌"
            opts.append(f"{tab} {status}")
        opts.append("❌ Quit")

        idx = indexed_choice(opts, "Choose a tab")
        if idx is None or idx == len(tabs):
            break

        tab = tabs[idx]
        csv_path = run_path / f"{tab}.csv"

        while True:
            print(f"\n📐 Menu for '{tab}':")
            actions = {"v": "view CSV"}
            if method == "uploaded":
                actions["u"] = "upload file"
            actions["q"] = "back"

            sub = menu_prompt(actions)
            if sub == "v":
                if csv_path.exists():
                    print(csv_path.read_text())
                else:
                    print("⚠️ File does not exist.")
            elif sub == "u" and method == "uploaded":
                success = upload_csv_file(csv_path)  # noqa: F821  # legacy CLI: upload_csv_file was never ported to this layer
                if success:
                    print("✅ File uploaded.")
            elif sub == "q":
                break

def run_parsers_with_error_handling(project_path: Path, verb_key: str, run_id: str, run_path: Path, parser_names: list[str]):
    try:
        print("⚙️ Running parser(s)...")
        success = execute_parser_runner(project_path, verb_key, run_id)
        if success:
            print("✅ All parser(s) completed successfully.")
        else:
            raise RuntimeError("One or more parsers failed (unknown reason)")
    except Exception:
        tb = traceback.format_exc()
        first_parser = parser_names[0] if parser_names else "unknown"
        write_error_log(run_path, first_parser, tb)
        print(f"❌ Parser failed. Logged error to Error.md\n\n{tb}")


def write_error_log(run_path: Path, parser_name: str, traceback_str: str):
    """
    Append a Markdown-formatted error log to Error.md in the run folder.
    Each entry starts with --- and includes timestamp, parser name, and traceback.
    """
    log_path = run_path / "Error.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    entry = (
        "---\n"
        f"❌ Error running parser: {parser_name}\n"
        f"Date: {now}\n"
        "Traceback:\n\n"
        "```\n"
        f"{traceback_str.strip()}\n"
        "```\n"
    )

    with open(log_path, "a") as f:
        f.write(entry)

def handle_adverb_zone(project_path: Path, run_path: Path, verb_name: str, schema: dict) -> None:

    adv_path = run_path / "adverbs.json"
    current = json.load(adv_path.open()) if adv_path.exists() else {}

    while True:
        names = list(schema.keys())
        opts = [f"{n}: {current.get(n, '')}" for n in names] + ["❌ Quit"]
        idx = indexed_choice(opts, "Select adverb to edit")
        if idx is None or idx == len(names):
            break

        adv_name = names[idx]
        handler = load_adverb_handler(project_path, verb_name, adv_name)

        # ---- Type‐based dispatch ----
        if isinstance(handler, ReferenceAdverb):
            val = handler.prompt_for_value(project_path)
            if val and handler.validate(val, project_path):
                current[adv_name] = val
                print("✅ Saved")

        elif isinstance(handler, ReferenceListAdverb):
            current.setdefault(adv_name, [])
            current[adv_name] = handler.edit_value_for_run(current[adv_name], project_path)

        elif isinstance(handler, TagAdverb):
            val = handler.prompt_for_value(project_path)
            if val and handler.validate(val, project_path):
                current[adv_name] = val
                print("✅ Saved")

        elif isinstance(handler, AttributeAdverb):
            val = handler.prompt_for_value(project_path)
            if val and handler.validate(val, project_path):
                current[adv_name] = val
                print("✅ Saved")
            else:
                print("❌ Invalid value.")

        else:
            print(f"⚠️ Unhandled adverb type: {handler.__class__.__name__}")

        adv_path.write_text(json.dumps(current, indent=2))
        current = json.load(adv_path.open())

def handle_raw_data_zone(zone_name: str, run_path: Path) -> None:
    """
    Handle CLI interaction for raw data zones: view, upload, delete a single CSV or XLSX file.
    Files are stored under:
      {run_path}/{zone_name}/<your_uploaded_file>.csv|.xlsx
    """
    zone_folder = run_path / zone_name
    zone_folder.mkdir(parents=True, exist_ok=True)

    def get_existing_file():
        files = list(zone_folder.glob("*.csv")) + list(zone_folder.glob("*.xlsx"))
        return files[0] if files else None

    while True:
        print(f"\n📂 Selected zone: {zone_name}")
        action = menu_prompt({
            "v": "View current file",
            "u": "Upload new file",
            "d": "Delete current file",
            "q": "Quit"
        })

        if action == "v":
            existing = get_existing_file()
            if existing:
                print(f"📄 Viewing: {existing.name}")
                _print_csv_or_xlsx(existing)  # Replaces _print_csv to support both CSV and XLSX
            else:
                print("⚠️ No file found.")

        elif action == "u":
            uploaded = upload_file_to_folder(zone_folder)
            if uploaded:
                # Optionally delete other files if you only want one per zone
                existing = get_existing_file()
                if existing and existing != uploaded:
                    existing.unlink()
                    print(f"🗑️ Removed old file: {existing}")

        elif action == "d":
            existing = get_existing_file()
            if existing:
                existing.unlink()
                print("🗑️ File deleted.")
            else:
                print("⚠️ No file to delete.")

        elif action == "q":
            break

        else:
            print("❓ Invalid option.")