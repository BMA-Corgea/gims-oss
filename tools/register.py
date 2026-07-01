import sys
import os
import json
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from utils import semantics as sem
from utils.handlers.adjective import get_adjective_class_handler, BaseAdjective
from utils.handlers.noun import register_noun_type, NounType
from utils.handlers.verb import VerbType
from utils.handlers.adverb import BaseAdverb
from utils.interface import prompt_if_missing

def get_input(prompt, default=None):
    raw = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ")
    if raw is None:
        return default
    text = raw.strip()
    return text if text else default

def confirm_list(prompt):
    print(f"{prompt} (comma-separated):")
    raw = input("> ").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]

def register_noun_interactive(project_name: str | None = None):
    project_name = sem.generate_project_selector(project_name)
    base = Path("projects") / project_name
    noun_path = base / "noun_types.json"
    alias_path = base / "aliases" / "nouns.json"

    noun_path.parent.mkdir(parents=True, exist_ok=True)
    if not noun_path.exists():
        noun_path.write_text(json.dumps({}))

    noun_key = get_input("Noun key (e.g., Sample, Vial)")
    if not noun_key:
        print("❌ Noun key cannot be blank.")
        return

    with open(noun_path) as f:
        existing = json.load(f)

    noun = NounType(noun_key, {}, noun_path)
    if noun.interactive_register_from_context(existing, alias_path):
        print(f"🎉 Noun '{noun_key}' fully configured.")

def register_verb_interactive(project_name: str | None = None):
    project_name = sem.generate_project_selector(project_name)

    verb_key = input("🆕 Verb key (e.g., test_potency, move_inventory): ").strip()
    if not verb_key:
        print("❌ Verb key cannot be blank.")
        return

    verb = VerbType(verb_key, project_name)
    if verb.interactive_register_from_context():
        print(f"✅ Verb '{verb_key}' registered successfully.")


def register_adjective_interactive(project_name: str | None = None):
    project_name = sem.generate_project_selector(project_name)
    base = Path("projects") / project_name

    noun_types_path = base / "noun_types.json"
    adjective_path  = base / "adjective_types.json"
    verb_types_path = base / "verb_types.json"

    noun_types = sem.load_json(noun_types_path)
    adjectives = sem.load_json(adjective_path)
    verb_types = sem.load_json(verb_types_path)

    handler = BaseAdjective({}, project_name=project_name)
    handler = handler.interactive_register_from_context(noun_types, verb_types, adjectives)
    if handler:
        from utils.handlers.adjective import ReferenceAdjective
        if isinstance(handler, ReferenceAdjective) and hasattr(handler, "configure_uniqueness"):
            handler.configure_uniqueness()
        adjectives.append(handler.data)
        sem.save_json(adjective_path, adjectives)
        print(f"\n✅ Promoted '{handler.data['adjective']}' on '{handler.noun_type}' to '{handler.data['adjective_class']}'.")

def register_adverb_interactive(project: str | None = None):
    project_name = sem.generate_project_selector(project)
    base = Path("projects") / project_name

    verb_types = sem.load_json(base / "verb_types.json") or {}
    existing   = sem.load_json(base / "adverb_types.json") or []

    # kick off the CLI flow in adverb.py
    handler = BaseAdverb(project_name, None)
    handler.interactive_register_from_context(verb_types, existing)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from utils.interface import prompt_if_missing

    # Optional CLI args
    word_type_arg = sys.argv[1] if len(sys.argv) > 1 else None
    project_arg   = sys.argv[2] if len(sys.argv) > 2 else None

    # Choose what to register
    word_types = ["noun", "verb", "adjective", "adverb"]
    word_type  = prompt_if_missing(word_type_arg, word_types, label="word type")

    # Choose project
    project_names = [d.name for d in Path("projects").iterdir() if d.is_dir()]
    project = prompt_if_missing(project_arg, project_names, label="project")

    # Dispatch to the correct interactive function
    if word_type == "noun":
        register_noun_interactive(project)
    elif word_type == "verb":
        register_verb_interactive(project)
    elif word_type == "adjective":
        register_adjective_interactive(project)
    elif word_type == "adverb":
        register_adverb_interactive(project)
    else:
        print(f"❌ Unknown word type: {word_type}")