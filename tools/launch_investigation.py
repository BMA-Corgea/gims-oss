from pathlib import Path
import logging
logging.basicConfig(
    level=logging.INFO,  # ← this is the key line
    format="%(levelname)s | %(name)s | %(message)s"
)
import sys, json

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.interface import menu_prompt
from utils.display import format_display_id
from utils.interface import prompt_if_missing
from core.investigation import (
    build_investigation_output,
    get_all_fields,
    build_filter_function,
    apply_all_filters,
    sort_items_by_field,
    load_items,
    prepare_items_for_display,
)

logger = logging.getLogger(__name__)

def format_table(rows: list[dict], noun_type: str, project_path: Path) -> None:
    import logging
    from rich.table import Table
    from rich.console import Console
    from core.investigation import build_table_rows

    logger = logging.getLogger(__name__)
    logger.debug(f"🛠️ Starting format_table() for noun_type: {noun_type}")
    logger.debug(f"📦 Received {len(rows)} row(s)")

    try:
        headers, table_data = build_table_rows(rows, noun_type, project_path)
        logger.debug(f"✅ build_table_rows() returned {len(headers)} headers and {len(table_data)} rows")

        table = Table(show_lines=True)
        table.add_column("#", style="dim")
        for h in headers:
            logger.debug(f"➕ Adding column: {h}")
            table.add_column(h)

        for idx, row in enumerate(table_data):
            if not isinstance(row, list):
                logger.warning(f"⚠️ Row {idx} is not a list: {row}")
            else:
                logger.debug(f"📄 Adding row {idx}: {row}")
            table.add_row(str(idx), *[str(cell) for cell in row])

        console = Console()
        print()  # 👈 This ensures the newline BEFORE the table
        console.print(table)
        logger.debug(f"🖨️ Table rendered successfully")

    except Exception as e:
        logger.exception(f"❌ Error during format_table: {e}")
        print("⚠️ Failed to render table.")

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
    logger.debug(f"🔍 enter_investigate_mode() called for project='{project}', noun_type='{noun_type}'")
    logger.debug(f"📦 Received {len(items)} item(s) to investigate")

    if not items:
        logger.warning("❌ No items to investigate.")
        return

    project_path = Path("projects") / project
    logger.debug(f"🛤️ Computed project_path: {project_path}")

    def investigate(record):
        logger.debug(f"🧪 Investigating record: {record}")
        try:
            # Get raw data from core
            table_rows, lineage_data = build_investigation_output(project_path, noun_type, record)
            logger.debug("📦 Investigation data loaded")

            # Format for CLI output
            from core.investigation import render_lineage

            table_str = format_table(table_rows, noun_type, project_path)
            lineage_str = render_lineage(lineage_data, project_path)
            pk = noun_type.lower() + "_id"
            instance_id = record.get(pk)

            pretty_output = f"\n🕵️ Investigating {noun_type} {instance_id}\n\n{table_str}\n\n{lineage_str}"
            logger.info(pretty_output)
            logger.debug("✅ Investigation output successfully rendered")

        except Exception as e:
            logger.exception("❌ Failed to render investigation view")
            return None

        logger.info("")  # blank line
        logger.debug("📋 Prompting for next action")
        action = menu_prompt({
            "b": "back to list",
            "d": "deep search from here",
            "r": "restart view.py",
        })

        logger.debug(f"🧭 User selected action: {action}")
        if action == "d":
            from tools.launch_deep_search import launch_deep_search
            logger.info("\n🚀 Launching deep search...\n")
            result = launch_deep_search(project)
            if result == "restart":
                logger.debug("🔁 Deep search triggered restart")
                return "restart"
            return None

        if action == "r":
            logger.debug("🔁 Restart requested by user")
            return "restart"

        logger.debug("🔚 Returning from investigate() with None")
        return None

    # Always show interactive selection
    logger.debug("📺 Entering investigation loop")

    while True:
        logger.info(f"\n📋 {noun_type} records:\n")
        try:
            table_str = format_table(items, noun_type, project_path)
            logger.info(table_str)
            logger.debug(f"📊 Table displayed with {len(items)} row(s)")
        except Exception as e:
            logger.error(f"❌ Failed to format table: {e}")
            return

        logger.info("")  # blank line
        choice = input("Select item to investigate (index), or (q)uit: ").strip()
        logger.debug(f"🧾 User entered choice: '{choice}'")

        if choice.lower() == 'q':
            logger.debug("🚪 Exiting investigate loop via 'q'")
            break
        if not choice.isdigit():
            logger.warning("❌ Invalid input. Please enter a number.")
            continue

        idx = int(choice)
        if idx < 0 or idx >= len(items):
            logger.warning(f"❌ {idx} is out of range (0–{len(items)-1}).")
            continue

        logger.debug(f"➡️ Invoking investigate() on item index {idx}")
        result = investigate(items[idx])
        if result == "restart":
            logger.debug("🔁 Restart triggered inside investigate()")
            return "restart"

def interactive_loop(items, noun_type, project):
    original_items = items[:]
    current_items = items[:]
    filters_applied = []

    def handle_filter_group(is_or=False):
        all_fields = get_all_fields(current_items)
        filter_group = []

        while True:
            field = prompt_field_choice(all_fields)
            if not field:
                break
            value = input(f"Enter value for '{field}' (or 'q' to quit): ").strip()
            if value.lower() == 'q':
                break
            filt = build_filter_function(field, value)
            if filt:
                filter_group.append(filt)
            if not is_or:
                filters_applied.append(("AND", filt))
                break

        if is_or and filter_group:
            filters_applied.append(("OR", filter_group))

    while True:
        logger.info("\n" + format_table(current_items, noun_type, Path("projects") / project))
        action = input(
            "\nAction? (s)ort, (f)ilter, (e)xclude, (o)r group, (r)estore, (q)uit: "
        ).strip().lower()

        if action == 'q':
            break
        elif action == 'r':
            filters_applied = []
            current_items = original_items[:]
        elif action == 's':
            all_fields = get_all_fields(current_items)
            field = prompt_field_choice(all_fields)
            if field:
                current_items = sort_items_by_field(current_items, field)
        elif action in ['f', 'e']:
            all_fields = get_all_fields(current_items)
            field = prompt_field_choice(all_fields)
            if not field:
                continue
            value = input(f"Enter value for '{field}' (or 'q' to cancel): ").strip()
            if value.lower() == 'q':
                continue
            filt = build_filter_function(field, value, exclude=(action == 'e'))
            if filt:
                filters_applied.append(("AND", filt))
                current_items = apply_all_filters(original_items, filters_applied)
        elif action == 'o':
            logger.info("🔁 OR‐group mode. Add one or more filters.")
            handle_filter_group(is_or=True)
            current_items = apply_all_filters(original_items, filters_applied)
        elif action == 'i':
            from tools.view import enter_investigate_mode
            result = enter_investigate_mode(project, noun_type, current_items)
            if result == "restart":
                from tools.view import view_main
                logger.info("\n🔄 Restarting view.py...\n")
                view_main(project, noun_type)
                return
        else:
            logger.warning("❌ Invalid action.")
    return current_items

def view_main(project: str, noun_type: str):
    project_path = Path("projects") / project

    # 1) Load and show initial table
    try:
        items = load_items(project_path, noun_type)
    except FileNotFoundError:
        logger.warning(f"❌ No data found for noun '{noun_type}' in project '{project}'")
        return
    except Exception as e:
        logger.error(f"❌ Failed to load items: {e}")
        return

    try:
        display_text = prepare_items_for_display(items, noun_type, project)
        logger.info(f"\n📦 {noun_type} Records from project: {project}\n")
        logger.info(display_text)
    except Exception as e:
        logger.error(f"❌ Failed to format items: {e}")
        return

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
            items = interactive_loop(items, noun_type, project)
            logger.info(f"\n📦 {noun_type} Records from project: {project}\n")
            logger.info(prepare_items_for_display(items, noun_type, project))

        elif mode == 'i':
            result = enter_investigate_mode(project, noun_type, items)
            if result == "restart":
                return view_main(project, noun_type)

            logger.info(f"\n📦 {noun_type} Records from project: {project}\n")
            logger.info(prepare_items_for_display(items, noun_type, project))

def main():
    project_arg = sys.argv[1] if len(sys.argv) > 1 else None
    noun_arg    = sys.argv[2] if len(sys.argv) > 2 else None

    # Get project list
    projects_path = Path("projects")
    projects = [p.name for p in projects_path.iterdir() if p.is_dir()]
    project = prompt_if_missing(project_arg, projects, label="project")

    # Validate noun_types.json exists
    noun_types_path = projects_path / project / "noun_types.json"
    if not noun_types_path.exists():
        logger.warning(f"❌ No noun_types.json found in '{project}'")
        sys.exit(1)

    # Load noun types
    noun_types = list(json.load(open(noun_types_path)).keys())
    noun_type = prompt_if_missing(noun_arg, noun_types, label="noun type")

    # Launch the view
    view_main(project, noun_type)

if __name__ == "__main__":
    main()