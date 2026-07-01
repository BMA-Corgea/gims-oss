from pathlib import Path
import json
import logging
logger = logging.getLogger(__name__)


def menu_prompt(options: dict[str, str]) -> str:
    for key, label in options.items():
        # Format like (d)efine or (s)ave depending on what feels natural
        if label.lower().startswith(key):
            print(f"({key}){label[1:]}")
        else:
            print(f"({key}) {label}")
    while True:
        choice = input("Action: ").strip().lower()
        if choice in options:
            return choice
        print("❌ Invalid selection.")

def core_menu_prompt(options: dict[str, str]) -> str:
    for key, label in options.items():
        # Format like (d)efine or (s)ave depending on what feels natural
        if label.lower().startswith(key):
            logger.info(f"({key}){label[1:]}")
        else:
            logger.info(f"({key}) {label}")
    while True:
        choice = input("Action: ").strip().lower()
        if choice in options:
            return choice
        logger.warning("❌ Invalid selection.")


def core_indexed_choice(options: list[str], prompt_msg="Select an option") -> int | None:
    """
    Displays a numbered list and prompts for a selection.
    Returns the selected index or None if invalid.
    Uses logging instead of print.
    """
    if not options:
        logger.warning("⚠️ No options available.")
        return None

    for i, option in enumerate(options):
        logger.info(f"[{i}] {option}")

    while True:
        choice = input(f"{prompt_msg} (or 'q' to quit): ").strip().lower()
        if choice == 'q':
            return None
        if choice.isdigit():
            index = int(choice)
            if 0 <= index < len(options):
                return index
        logger.warning("❌ Invalid selection. Try again.")


def indexed_choice(options: list[str], prompt_msg="Select an option") -> int | None:
    """
    Displays a numbered list and prompts for a selection.
    Returns the selected index or None if invalid.
    """
    if not options:
        print("⚠️ No options available.")
        return None

    for i, option in enumerate(options):
        print(f"[{i}] {option}")

    while True:
        choice = input(f"{prompt_msg} (or 'q' to quit): ").strip().lower()
        if choice == 'q':
            return None
        if choice.isdigit():
            index = int(choice)
            if 0 <= index < len(options):
                return index
        print("❌ Invalid selection. Try again.")

def prompt_if_missing(arg_value, options: list, label: str, lowercase=False) -> str:
    """
    If `arg_value` is provided, returns it.
    If not, prompts the user to pick from a numbered list of `options`.

    - `label`: Used in prompt text like "Select a {label}"
    - `lowercase`: If True, will lowercase the returned value

    Returns: selected value from `options`
    """
    from .interface import indexed_choice  # avoid circular import if needed

    if arg_value:
        return arg_value.lower() if lowercase else arg_value

    idx = indexed_choice(options, f"Select a {label}")
    if idx is None:
        print("❎ Cancelled.")
        exit()
    result = options[idx]
    return result.lower() if lowercase else result

def core_prompt_if_missing(arg_value, options: list, label: str, lowercase=False) -> str:
    """
    If `arg_value` is provided, returns it.
    If not, prompts the user to pick from a numbered list of `options`.

    - `label`: Used in prompt text like "Select a {label}"
    - `lowercase`: If True, will lowercase the returned value

    Returns: selected value from `options`
    """
    from .interface import indexed_choice  # avoid circular import if needed

    if arg_value:
        return arg_value.lower() if lowercase else arg_value

    idx = indexed_choice(options, f"Select a {label}")
    if idx is None:
        logger.info("❎ Cancelled.")
        exit()
    result = options[idx]
    return result.lower() if lowercase else result

def select_fields_from_noun_type(project_path: Path, noun_type: str, prompt_text: str = None) -> list[str]:
    """
    Load available fields from a noun type and prompt the user to select a subset.

    Returns a list of selected field names.
    """
    noun_types_path = project_path / "noun_types.json"
    if not noun_types_path.exists():
        print(f"❌ noun_types.json not found at {noun_types_path}")
        return []

    noun_defs = json.loads(noun_types_path.read_text())
    if noun_type not in noun_defs:
        print(f"❌ Noun type '{noun_type}' not found in noun_types.json")
        return []

    field_names = list(noun_defs[noun_type].get("fields", {}).keys())
    if not field_names:
        print(f"⚠️ No fields defined for noun type '{noun_type}'")
        return []

    if prompt_text:
        print(prompt_text)
    else:
        print(f"📌 Select fields from '{noun_type}':")

    selected = []
    while True:
        for i, f in enumerate(field_names):
            print(f"[{i}] {f}")
        choice = input("Choose index (or 'q' to finish): ").strip().lower()
        if choice == "q":
            break
        if choice.isdigit() and 0 <= int(choice) < len(field_names):
            selected_field = field_names[int(choice)]
            if selected_field not in selected:
                selected.append(selected_field)
        else:
            print("❌ Invalid choice.")

    return selected