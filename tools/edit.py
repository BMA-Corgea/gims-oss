#!/usr/bin/env python3
import sys
from pathlib import Path
import json

# Make sure we can import utils/handlers no matter where we run this from
sys.path.append(str(Path(__file__).resolve().parents[1]))

from utils import semantics as sem
from utils.handlers.adjective import get_adjective_class_handler
from utils.handlers.adverb import get_adverb_handler
from utils.word_registry import WordRegistry
from utils.handlers.noun import NounType
from utils.handlers.verb import VerbType
from utils.interface import prompt_if_missing, menu_prompt, indexed_choice
from utils.handlers.adverb import get_adverb_handler

def load_json(path: Path):
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f)

def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(data, f, indent=2)

def edit_noun_interactive(project_name: str):
    base = Path("projects") / project_name
    noun_path = base / "noun_types.json"

    if not noun_path.exists():
        print("❌ No noun types found.")
        return

    noun_data = sem.load_json(noun_path)
    if not noun_data:
        print("❌ noun_types.json is empty.")
        return

    noun_names = list(noun_data.keys())

    print("\n📦 Available noun types:")
    for i, noun in enumerate(noun_names):
        print(f"[{i}] {noun}")

    choice = input("Select a noun to edit (or 'q' to cancel): ").strip().lower()
    if choice == 'q':
        print("❎ Cancelled.")
        return

    if not choice.isdigit() or not (0 <= int(choice) < len(noun_names)):
        print("❌ Invalid selection.")
        return

    noun_key = noun_names[int(choice)]
    schema = noun_data[noun_key]

    noun = NounType(noun_key, schema, noun_path)
    noun.interactive_edit()

def edit_adjective_interactive(project_name: str):
    base            = Path("projects") / project_name
    adjective_path  = base / "adjective_types.json"
    aliases_path    = base / "aliases" / "adjectives.json"
    verb_types_path = base / "verb_types.json"

    adjectives = load_json(adjective_path)
    aliases    = load_json(aliases_path)
    verb_types = load_json(verb_types_path)

    if not adjectives:
        print("❌ No adjectives to edit.")
        return

    print("\n🧠 Available adjectives:")
    for i, adj in enumerate(adjectives):
        noun = adj.get("applies_to", ["<unknown>"])[0]
        print(f"[{i}] {adj['adjective']} (belongs to noun: {noun})")

    choice = input("Select adjective index (or 'q' to cancel): ").strip().lower()
    if choice == "q":
        return
    if not choice.isdigit() or not (0 <= int(choice) < len(adjectives)):
        print("❌ Invalid selection.")
        return

    idx   = int(choice)
    entry = adjectives[idx]
    adjective_class = entry["adjective_class"]
    handler_cls = get_adjective_class_handler(adjective_class)

    handler = handler_cls(
        entry,
        noun_type=entry["applies_to"][0],
        verb_types=verb_types,
        project_name=project_name
    )

    print("\nWhat would you like to do?")
    print("[1] Edit fields")
    print("[2] Demote to plain noun attribute")
    action = input("Select action: ").strip()

    if action == "1":
        updated = handler.interactive_edit()
        from utils.handlers.adjective import ReferenceAdjective
        if isinstance(handler, ReferenceAdjective) and hasattr(handler, "configure_uniqueness"):
            handler.configure_uniqueness()
        if updated:
            adjectives[idx] = handler.data
            save_json(adjective_path, adjectives)
            print("✅ Changes saved.")
    elif action == "2":
        handler.demote_attribute()
        adjectives.pop(idx)
        save_json(adjective_path, adjectives)
        if entry["adjective"] in aliases:
            del aliases[entry["adjective"]]
            save_json(aliases_path, aliases)
    else:
        print("❌ Invalid action.")


def edit_verb_interactive(project_name: str):
    VerbType.edit_existing(project_name)

def edit_adverb_interactive(project_name: str):
    base    = Path("projects") / project_name
    ad_file = base / "adverb_types.json"
    if not ad_file.exists():
        print("❌ No adverbs to edit.")
        return

    with ad_file.open() as f:
        adverbs = json.load(f)

    if not adverbs:
        print("❌ No adverbs to edit.")
        return

    print("\n🔖 Available adverbs:")
    for i, ent in enumerate(adverbs):
        name = ent["adverb"]
        verb = ent["verb"]
        cls  = ent.get("adverb_class", "<unknown>")
        print(f"[{i}] {name} (verb: {verb}, class: {cls})")

    choice = input("Select an adverb index (or 'q' to cancel): ").strip().lower()
    if choice == "q":
        return
    if not choice.isdigit() or not (0 <= int(choice) < len(adverbs)):
        print("❌ Invalid selection.")
        return

    idx   = int(choice)
    entry = adverbs[idx]

    handler = get_adverb_handler(
        name         = entry["adverb"],
        config       = entry,
        project_name = project_name
    )

    if not hasattr(handler, "interactive_configure"):
        print(f"❌ Cannot edit class {entry.get('adverb_class')}.")
        return

    changed = handler.interactive_configure()
    if not changed:
        print("❎ No changes made.")
        return

    # persist global adverb_types.json
    adverbs[idx] = {
        "adverb": entry["adverb"],
        "verb": entry["verb"],
        "adverb_class": entry["adverb_class"],  # ← 🔐 this was missing
        **handler.config
    }
    with ad_file.open("w") as f:
        json.dump(adverbs, f, indent=2)

    # persist per-verb schema
    vt_file = base / "verb_types.json"
    try:
        with vt_file.open() as f:
            vt = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        vt = {}

    vcfg  = vt.setdefault(entry["verb"], {})
    adsch = vcfg.setdefault("adverb_schema", {})
    minimal = {k: v for k, v in handler.config.items() if k != "adverb_class"}
    adsch[entry["adverb"]] = minimal

    with vt_file.open("w") as f:
        json.dump(vt, f, indent=2)

    print("✅ Adverb changes saved.")

if __name__ == "__main__":
    word_type_arg = sys.argv[1] if len(sys.argv) > 1 else None
    project_arg   = sys.argv[2] if len(sys.argv) > 2 else None

    # choose what to edit
    word_types = ["adjective", "noun", "verb", "adverb"]
    word_type  = prompt_if_missing(word_type_arg, word_types, label="word type")

    # choose project
    project_names = [p.name for p in Path("projects").iterdir() if p.is_dir()]
    project_name = prompt_if_missing(project_arg, project_names, label="project")

    if word_type == "noun":
        edit_noun_interactive(project_name)
    elif word_type == "adjective":
        edit_adjective_interactive(project_name)
    elif word_type == "verb":
        edit_verb_interactive(project_name)
    elif word_type == "adverb":
        edit_adverb_interactive(project_name)
    else:
        print(f"❌ Unsupported word type: '{word_type}'")
        print("Supported types: adjective, noun, verb, adverb")
        sys.exit(1)
