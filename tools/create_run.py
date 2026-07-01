#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import argparse
import json

from utils.interface import indexed_choice, prompt_if_missing

def list_available_verbs(project_path: Path) -> list[str]:
    verb_file = project_path / "verb_types.json"
    if not verb_file.exists():
        print("❌ No verb_types.json found.")
        return []
    with open(verb_file) as f:
        return list(json.load(f).keys())

def load_verb_metadata(project_path: Path, verb_name: str) -> dict:
    verb_file = project_path / "verb_types.json"
    with open(verb_file) as f:
        return json.load(f).get(verb_name, {})

def load_log_config(project_path: Path, group_name: str) -> dict:
    config_path = project_path / "verbs" / group_name / f"{group_name}_log_config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"❌ Log config not found for verb group '{group_name}'")
    with open(config_path) as f:
        return json.load(f)

def save_log_config(project_path: Path, group_name: str, config: dict):
    config_path = project_path / "verbs" / group_name / f"{group_name}_log_config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

def prompt_for_log_fields(log_config: dict) -> dict:
    data = {}
    for field_name, field_info in log_config["fields"].items():
        required = field_info.get("required", False)
        ftype = field_info.get("type")
        # Skip "test_type" since we will fill it automatically
        if field_name == "test_type":
            continue

        val = input(f"Enter value for '{field_name}' ({ftype}){' [required]' if required else ''}: ").strip()
        if required and not val:
            raise ValueError(f"Field '{field_name}' is required.")
        data[field_name] = val
    return data

def add_log_entry(project_path: Path, verb_name: str):
    # Step 1: Load verb metadata
    verb_meta = load_verb_metadata(project_path, verb_name)
    group_name = verb_meta.get("verb_group")
    if not group_name:
        raise ValueError(f"Verb '{verb_name}' does not have an associated verb group.")

    # Step 2: Load (and possibly patch) the group's log config
    log_config = load_log_config(project_path, group_name)
    fields = log_config.setdefault("fields", {})

    if "test_type" not in fields:
        fields["test_type"] = {"type": "string", "required": True}
        save_log_config(project_path, group_name, log_config)
        print(f"🛠️  Added 'test_type' to {group_name}_log_config.json")

    primary_id_field = log_config.get("primary_id")
    if not primary_id_field:
        raise ValueError(f"❌ 'primary_id' not set in {group_name}_log_config.json")

    # Step 3: Prompt for all fields except "test_type"
    entry = prompt_for_log_fields(log_config)
    entry["test_type"] = verb_name

    # Step 4: Write to log file
    log_path = project_path / "verbs" / group_name / f"{group_name}_log.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"✅ Entry added to '{group_name}_log.jsonl'")

    # Step 5: Create data dump folder
    run_id = entry.get(primary_id_field)
    if not run_id:
        print("⚠️ Primary ID field is missing from the entry; cannot create data dump.")
        return

    dump_root = project_path / "verbs" / group_name / "data_dumps" / str(run_id)
    if dump_root.exists():
        print(f"ℹ️ Data dump already exists at {dump_root}")
        return

    print(f"🔧 Creating data dump directory at {dump_root}...")
    dump_root.mkdir(parents=True, exist_ok=True)

    # ── HERE WE INLINE load_verb_schema ──
    verb_types_path = project_path / "verb_types.json"
    if not verb_types_path.exists():
        raise FileNotFoundError(f"No verb_types.json found in {project_path}")
    with open(verb_types_path) as vf:
        all_verbs = json.load(vf)

    if verb_name not in all_verbs:
        raise ValueError(f"Verb '{verb_name}' not found in verb_types.json")

    verb_schema = all_verbs[verb_name].get("data_entry_schema", {})
    # ── END INLINE ──

    instructions = verb_schema.get("instructions", [])
    raw_inputs = verb_schema.get("raw_data_inputs", [])
    interpretation_tabs = verb_schema.get("interpretation", {}).get("tabs", [])
    if isinstance(interpretation_tabs, dict):
        interpretation_tabs = list(interpretation_tabs.keys())

    # 5a) Instructions.md
    instructions_md = "# Instructions\n\n"
    if instructions:
        instructions_md += "\n".join(f"{i+1}. {line}" for i, line in enumerate(instructions)) + "\n"
    else:
        instructions_md += "(No instructions defined)\n"
    (dump_root / "Instructions.md").write_text(instructions_md)

    # 5b) Raw Data pockets (one folder per entry in raw_inputs)
    for pocket in raw_inputs:
        (dump_root / pocket).mkdir(parents=True, exist_ok=True)

    # 5c) DataEntry.json (empty list)
    (dump_root / "DataEntry.json").write_text(json.dumps([], indent=2))

    # 5d) Interpretation CSVs
    for tab in interpretation_tabs:
        (dump_root / f"{tab}.csv").write_text("")

    # 5e) Adverbs.json
    verb_def   = all_verbs[verb_name]
    adv_schema = verb_def.get("adverb_schema", {})

    # Gather definitions only for this verb’s adverbs
    adverb_defs = {}
    adv_path = project_path / "adverb_types.json"
    if adv_path.exists():
        with open(adv_path) as af:
            for entry in json.load(af):
                name = entry.get("adverb")
                if name not in adv_schema:
                    continue
                if entry.get("verb") and entry["verb"] != verb_name:
                    continue
                if entry.get("verbs") and verb_name not in entry["verbs"]:
                    continue
                adverb_defs[name] = entry

    # Initialize adverb data ("" for Reference, [] for ReferenceList)
    adv_data = {}
    for name, cfg in adv_schema.items():
        cls = adverb_defs.get(name, {}).get("adverb_class")
        adv_data[name] = [] if cls == "ReferenceList" else ""

    # Always write something (empty dict if no schema)
    (dump_root / "adverbs.json").write_text(json.dumps(adv_data, indent=2))

    # 5f) Status.json (initial manual_approval=False)
    status_data = {
        "interpretation": {
            "manual_approval": False
        }
    }
    (dump_root / "Status.json").write_text(json.dumps(status_data, indent=2))

    print(f"✅ Data dump created for run '{run_id}' at:\n  {dump_root}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project", nargs="?", help="Project name")
    parser.add_argument("--verb", help="Verb to create entry for")
    args = parser.parse_args()

    # Prompt for project if missing
    project_root = Path("projects")
    project_names = [p.name for p in project_root.iterdir() if p.is_dir()]
    project_name = prompt_if_missing(args.project, project_names, label="project")
    project_path = project_root / project_name

    # Prompt for verb if missing
    verbs = list_available_verbs(project_path)
    if not verbs:
        print("❌ No verbs found in project.")
        exit(1)
    verb = prompt_if_missing(args.verb, verbs, label="verb")

    # Proceed
    add_log_entry(project_path, verb)
