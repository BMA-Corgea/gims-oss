import sys
import json
import argparse
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from utils.interface import indexed_choice, prompt_if_missing
from utils.handlers.adjective import get_adjective_class_handler


def load_schema(project_path: Path, type_name: str) -> tuple[dict, Path, str, str]:
    noun_path = project_path / "noun_types.json"
    if noun_path.exists():
        with open(noun_path) as f:
            noun_defs = json.load(f)
        if type_name in noun_defs:
            schema = noun_defs[type_name]
            data_path = project_path / "nouns" / type_name / "items.jsonl"
            primary_id = schema.get("primary_id_field", next(iter(schema.get("fields", {}))))
            return schema["fields"], data_path, primary_id, "noun"

    group_config = project_path / "verbs" / type_name / f"{type_name}_log_config.json"
    if group_config.exists():
        with open(group_config) as f:
            config = json.load(f)
        data_path = project_path / "verbs" / type_name / f"{type_name}_log.jsonl"
        primary_id = config["primary_id"]
        return config["fields"], data_path, primary_id, "verb"

    raise ValueError(f"❌ Could not find noun or verb group named '{type_name}'")


def load_items(data_path: Path) -> list[dict]:
    if not data_path.exists():
        return []
    with open(data_path) as f:
        return [json.loads(line) for line in f if line.strip()]


def save_items(data_path: Path, items: list[dict]):
    with open(data_path, "w") as f:
        for item in items:
            f.write(json.dumps(item) + "\n")


def edit_item(
    schema: dict,
    item: dict,
    project_path: Path,
    word_type: str,
    type_name: str
) -> dict:
    adjective_path = project_path / "adjective_types.json"
    adjectives = {}
    if adjective_path.exists():
        with open(adjective_path) as f:
            adj_list = json.load(f)
            adjectives = {entry["adjective"]: entry for entry in adj_list}

    verb_types = {}
    verb_file = project_path / "verb_types.json"
    if verb_file.exists():
        with open(verb_file) as f:
            verb_types = json.load(f)

    while True:
        print("\n🛠 Select a field to edit:")
        fields = list(schema.keys())
        for i, field in enumerate(fields):
            val = item.get(field, "[not set]")
            print(f"[{i}] {field} = {val}")
        print("[q] Quit editing")

        choice = input("Field index (or 'q'): ").strip().lower()
        if choice == 'q':
            return item
        if not choice.isdigit() or not (0 <= int(choice) < len(fields)):
            print("❌ Invalid selection.")
            continue

        field = fields[int(choice)]
        props = schema[field]
        current = item.get(field, "")
        ftype = props.get("type")
        required = props.get("required", False)

        if word_type == "noun" and ftype == "adjective":
            adjective_def = adjectives.get(field)
            if not adjective_def:
                print(f"⚠️ No adjective config for '{field}'. Treating as plain text.")
            else:
                adj_class = adjective_def.get("adjective_class")
                handler_cls = get_adjective_class_handler(adj_class)
                handler = handler_cls(
                    adjective_def,
                    noun_type=type_name,
                    verb_types=verb_types,
                    project_name=project_path.name
                )
                newval = handler.prompt_instance_edit(field, current)
                if newval is not None:
                    item[field] = newval
                continue

        prompt = f"{field} ({ftype}) [current: {current}]{' [required]' if required else ''}: "
        val = input(prompt).strip()
        if val == "":
            continue
        item[field] = val


def main():
    args = sys.argv[1:]
    project_root = Path("projects")

    projects = [p.name for p in project_root.iterdir() if p.is_dir()]
    project = prompt_if_missing(args[1] if len(args) > 1 else None, projects, label="project")

    noun_types = []
    noun_path = project_root / project / "noun_types.json"
    if noun_path.exists():
        with open(noun_path) as f:
            noun_types = list(json.load(f).keys())

    verb_groups = []
    verb_dir = project_root / project / "verbs"
    if verb_dir.exists():
        verb_groups = [p.name for p in verb_dir.iterdir() if p.is_dir()]

    # Ask user what category they want to edit
    word_type = prompt_if_missing(
        None,
        ["noun", "verb"],
        label="type to edit"
    )

    # Get type-specific options
    if word_type == "noun":
        if not noun_types:
            print("❌ No noun types available.")
            return
        type_name = prompt_if_missing(
            args[0] if len(args) > 0 else None,
            noun_types,
            label="noun type"
        )
    else:
        if not verb_groups:
            print("❌ No verb groups available.")
            return
        type_name = prompt_if_missing(
            args[0] if len(args) > 0 else None,
            verb_groups,
            label="verb group"
        )

    project_path = project_root / project
    schema, data_path, primary_id, word_type = load_schema(project_path, type_name)
    items = load_items(data_path)

    if not items:
        print("⚠️ No items to edit.")
        return

    options = [f"Manual entry of {primary_id}"] + [
        f"{i+1}. {itm.get(primary_id, '[no ID]')}" for i, itm in enumerate(items)
    ]
    idx = indexed_choice(options, "Select item to edit")
    if idx is None:
        return

    if idx == 0:
        while True:
            target_id = input(f"Enter value for {primary_id} (or 'q' to cancel): ").strip()
            if target_id.lower() == 'q':
                return
            match_idx = next((i for i, x in enumerate(items) if x.get(primary_id) == target_id), None)
            if match_idx is not None:
                break
            print("❌ No match found. Try again or 'q' to cancel.")
    else:
        match_idx = idx - 1

    print(f"\n✏️ Editing item #{match_idx} ({primary_id} = {items[match_idx].get(primary_id)})")
    items[match_idx] = edit_item(
        schema,
        items[match_idx],
        project_path,
        word_type,
        type_name
    )
    save_items(data_path, items)
    print("✅ Item updated.")

if __name__ == "__main__":
    main()