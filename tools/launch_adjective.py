# tools/adjective_launch.py

import json, sys, logging
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.interface import core_indexed_choice, core_menu_prompt, core_prompt_if_missing
from core.disambiguation import load_schema
from core.handlers.adjective_type import create_adjective_handler, load_adjective_handler, promote_noun_field_to_adjective
from core.adjectives.action_requirement import ActionRequirementAdjective
from core.adjectives.state_control import StateControlAdjective
from core.adjectives.tag import TagAdjective
from core.adjectives.reference import ReferenceAdjective
from core.adjectives.reference_list import ReferenceListAdjective
from core.adjectives.picture import PictureAdjective

# ─── Reconfigure root logger to suppress prefixes ──────────────────────────────
root = logging.getLogger()
for h in list(root.handlers):
    root.removeHandler(h)
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(message)s"))
root.addHandler(stream_handler)
root.setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def get_handler_map():
    return {
        "ActionRequirement": ActionRequirementAdjective,
        "StateControl":      StateControlAdjective,
        "Tag":               TagAdjective,
        "Reference":         ReferenceAdjective,
        "ReferenceList":     ReferenceListAdjective,
        "Picture":           PictureAdjective
    }


def cli_prompt_editable_field(fields: list[str], context: dict):
    print(f"\nEditing adjective: {context['adjective']} on {context['noun_type']}")
    for i, f in enumerate(fields):
        current_val = context["current_values"].get(f, "")
        print(f"  ({i}) {f} — current: {current_val}")
    choice = input("Select field number (or q): ").strip()
    if not choice.isdigit():
        return None
    idx = int(choice)
    if idx >= len(fields):
        return None
    field_key = fields[idx]
    new_value = input(f"Enter new value for '{field_key}': ").strip()
    return field_key, new_value


def edit_adjective(project: str):
    project_path = Path("projects") / project

    # 1) Pick noun type
    noun_schema = load_schema(project_path, "noun")
    noun_type = core_prompt_if_missing(None, list(noun_schema.keys()), "noun type")

    # 2) List adjectives on that noun
    all_adjs = load_schema(project_path, "adjective")  # list of dicts
    candidates = [e for e in all_adjs if noun_type in e.get("applies_to", [])]
    options = [e["adjective"] for e in candidates]
    if not options:
        logger.warning("⚠️ No adjectives found for that noun type.")
        return
    idx = core_indexed_choice(options, "Select adjective to edit")
    if idx is None:
        return
    adj_field = options[idx]

    # 3) Load the handler dynamically
    handler = load_adjective_handler(
        project_path=project_path,
        noun_type=noun_type,
        adjective_name=adj_field
    )

    # 4) Launch interactive edit session
    if handler.data.get("adjective") == "request_options" or handler.data.get("adjective_class") == "ActionRequirement":
        success = edit_request_options_cli(handler)
    else:
        success = handler.interactive_edit(cli_prompt_editable_field)
    if success:
        logger.info("✅ Adjective updated and saved.")
    else:
        logger.warning("❌ Edit cancelled or failed.")

def register_adjective(project: str):
    project_path = Path("projects") / project

    # 1) pick noun type
    noun_schema = load_schema(project_path, "noun")
    noun_type   = core_prompt_if_missing(None, list(noun_schema.keys()), "noun type")

    # 2) pick field to promote
    fields = noun_schema[noun_type]["fields"]
    candidates = [f for f, info in fields.items() if info.get("type") != "adjective"]

    if not candidates:
        logger.warning(f"⚠️ All fields on '{noun_type}' are already adjectives.")
        return
    idx = core_indexed_choice(candidates, "Select field to promote")
    if idx is None:
        return
    field_name = candidates[idx]

    # 3) pick adjective class
    classes = list(get_handler_map().keys())
    idx = core_indexed_choice(classes, "Select adjective class")
    if idx is None:
        return
    adj_class = classes[idx]

    # 4) create handler
    handler = create_adjective_handler(
        project_path=project_path,
        noun_type=noun_type,
        field_name=field_name,
        adjective_class=adj_class
    )

    # 5) run interactive config if needed
    if isinstance(handler, ActionRequirementAdjective):
        cancelled = not edit_request_options_cli(handler)
        if cancelled:
            logger.warning("❌ Configuration cancelled.")
            return
    elif isinstance(handler, ReferenceListAdjective):
        cancelled = not edit_reference_list_cli(handler)
        if cancelled:
            logger.warning("❌ Configuration cancelled.")
            return

    # 6) now persist after config is complete
    path = project_path / "adjective_types.json"
    entries = json.loads(path.read_text())
    entries.append(handler.data)
    path.write_text(json.dumps(entries, indent=2))

    # Promote noun field only after successful config and save
    info = getattr(handler, "_deferred_noun_field_info", None)
    if info:
        promote_noun_field_to_adjective(project_path, info)

    logger.info(f"✅ Registered new adjective: {field_name} ({adj_class})")

def launch_adjective_cli():
    # project first
    project_names = [p.name for p in Path("projects").iterdir() if p.is_dir()]
    project = core_prompt_if_missing(None, project_names, "project")

    logger.info(f"\n📂 Project: {project}")
    logger.info("🧭 What would you like to do?\n")
    action = core_menu_prompt({
        'e': 'Edit existing adjective',
        'r': 'Register new adjective',
        'q': 'Quit'
    })

    if action == 'e':
        edit_adjective(project)
    elif action == 'r':
        register_adjective(project)
    elif action == 'q':
        logger.info("👋 Done.")

def edit_request_options_cli(handler):
    current = handler.get_request_options()

    while True:
        logger.info("\nCurrent request_options:")
        if not current:
            logger.info("  (none declared)")
        else:
            for i, (req, verbs) in enumerate(current.items()):
                logger.info(f"[{i}] {req}: {', '.join(verbs)}")

        action = core_menu_prompt({
            'a': 'Add new request',
            'e': 'Edit existing request',
            'd': 'Delete request',
            'q': 'Quit'
        })

        if action == 'q':
            if not current:
                logger.warning("❌ Cannot save. You must define at least one request.")
                continue  # keep looping
            handler.set_request_options(current)
            return True  # success

        elif action == 'a':
            name = input("➕ New request name: ").strip()
            if name in current:
                logger.warning("❌ That request already exists.")
                continue
            verbs = _ask_verbs(handler)
            if verbs:
                current[name] = verbs

        elif action == 'e':
            reqs = list(current.keys())
            idx = indexed_choice(reqs, "Select request name to edit")
            if idx is None:
                continue
            old_name = reqs[idx]
            new_name = input(f"✏️ New name for '{old_name}': ").strip()
            verbs = _ask_verbs(handler)
            if verbs:
                del current[old_name]
                current[new_name] = verbs

        elif action == 'd':
            reqs = list(current.keys())
            idx = indexed_choice(reqs, "Select request name to delete")
            if idx is None:
                continue
            del current[reqs[idx]]

    handler.set_request_options(current)

def edit_reference_list_cli(handler):
    from utils.interface import core_indexed_choice
    from core.disambiguation import load_schema

    project_path = Path("projects") / handler.project_name
    noun_schema = load_schema(project_path, "noun")
    noun_options = list(noun_schema.keys())
    selected: list[str] = []

    logger.info("\nSelect reference nouns (blank to finish):")
    while True:
        idx = core_indexed_choice(noun_options, "Select a noun")
        if idx is None:
            break
        choice = noun_options[idx]
        if choice not in selected:
            selected.append(choice)

    if not selected:
        return False

    handler.set_reference_noun(selected)

    return True

def _ask_verbs(handler) -> list[str]:
    selected = []
    available = list(handler.verb_names)  # Copy to avoid mutation

    logger.info("✅ Select one or more verbs (blank to finish):")
    while True:
        remaining = [v for v in available if v not in selected]
        if not remaining:
            logger.info("🎉 All verbs selected.")
            break
        idx = core_indexed_choice(remaining, "Select verb")
        if idx is None:
            break
        selected.append(remaining[idx])
    return selected


if __name__ == "__main__":
    launch_adjective_cli()
