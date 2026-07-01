# tools/view_runlog.py

import sys
import json
from pathlib import Path

sys.path.append(str(Path.cwd()))
from utils.interface import prompt_if_missing
from utils.data_dump import open_data_dump
from utils.interface import menu_prompt

def load_verb_log_items(project_path: Path, verb_group: str) -> list[dict]:
    """
    Load entries from the verb group's log file, skipping malformed lines.

    Looks for:
      projects/{project}/verbs/{verb_group}/{verb_group}_log.jsonl
    """
    log_path = Path("projects") / project_path / "verbs" / verb_group / f"{verb_group}_log.jsonl"

    if not log_path.exists():
        print(f"⚠️ Log file not found: {log_path}")
        return []

    entries = []
    with open(log_path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if isinstance(entry, dict):
                    entries.append(entry)
                else:
                    print(f"⚠️ Line {lineno}: Not a JSON object → skipped.")
            except json.JSONDecodeError as e:
                print(f"❌ Line {lineno} in log is malformed: {e}")

    return entries

def derive_status(entry: dict, required_fields: list[str]) -> str:
    # (unchanged)
    if all(entry.get(f) not in (None, "") for f in required_fields):
        return "✅ Done"
    if any(entry.get(f) not in (None, "") for f in required_fields):
        return "🔄 In Progress"
    return "⏳ Pending"


def format_verb_table(
    items: list[dict],
    verb_group: str,
    required_fields: list[str]
) -> str:
    """
    Build a Markdown‐style table with an extra “test_type” column if present.
    Includes a leading index column.
    """
    if not items:
        return "⚠️ No entries found."

    # 1) Collect all field‐names from every run entry, plus “__status”
    headers = set().union(*(itm.keys() for itm in items)) | {"__status"}

    # Ensure “test_type” (or “verb”) is visible up front if it exists
    if "test_type" in headers:
        headers = ["test_type"] + sorted(headers - {"test_type"})
    elif "verb" in headers:
        headers = ["verb"] + sorted(headers - {"verb"})
    else:
        headers = sorted(headers)

    # Add index column
    headers = ["#"] + headers

    # Build a dict of column widths
    col_widths = {h: len(h) for h in headers}
    for idx, itm in enumerate(items):
        itm["__status"] = derive_status(itm, required_fields)
        col_widths["#"] = max(col_widths["#"], len(str(idx)))
        for h in headers[1:]:
            col_widths[h] = max(col_widths[h], len(str(itm.get(h, ""))))

    # Header row
    header_row = "| " + " | ".join(h.ljust(col_widths[h]) for h in headers) + " |"
    # Divider row
    divider_row = "|-" + "-|-".join("-" * col_widths[h] for h in headers) + "-|"
    # Data rows
    data_rows = []
    for idx, itm in enumerate(items):
        cells = [str(idx).ljust(col_widths["#"])]
        for h in headers[1:]:
            cells.append(str(itm.get(h, "")).ljust(col_widths[h]))
        row = "| " + " | ".join(cells) + " |"
        data_rows.append(row)

    return "\n".join([header_row, divider_row] + data_rows)


def view_runlog_main(project: str, verb_group: str):
    # Load the <verb_group>_log_config.json to know which fields are required
    config_path = Path(f"projects/{project}/verbs/{verb_group}/{verb_group}_log_config.json")
    if not config_path.exists():
        print(f"❌ Log config for verb group '{verb_group}' not found.")
        return

    with config_path.open() as f:
        config = json.load(f)

    required_fields = [k for k, v in config.get("fields", {}).items() if v.get("required")]
    items = load_verb_log_items(Path(project), verb_group)

    print(f"\n🧪 {verb_group} Run Log from project: {project}")
    while True:
        print("\n" + format_verb_table(items, verb_group, required_fields))
        action = menu_prompt({
            "v": "view entry",
            "d": "data dump",
            "q": "quit"
        })
        if action == 'q':
            break

        prompt = f"Enter run index (0–{len(items)-1}) (or 'q' to cancel): "
        sel = input(prompt).strip().lower()
        if sel == 'q':
            continue
        if not sel.isdigit():
            print("❌ Please enter a number or 'q'.")
            continue

        idx = int(sel)
        if idx < 0 or idx >= len(items):
            print("❌ Index out of range.")
            continue

        chosen = items[idx]
        if action == 'v':
            print("\n" + json.dumps(chosen, indent=2))
        else:  # action == 'd'
            open_data_dump(Path("projects") / project, verb_group, chosen)

if __name__ == "__main__":
    project_arg = sys.argv[1] if len(sys.argv) > 1 else None
    verb_arg = sys.argv[2] if len(sys.argv) > 2 else None

    projects = [p.name for p in Path("projects").iterdir() if p.is_dir()]
    project = prompt_if_missing(project_arg, projects, label="project")

    verb_dir = Path("projects") / project / "verbs"
    if not verb_dir.exists():
        print(f"❌ No verbs found for project '{project}'")
        sys.exit(1)

    verb_groups = [p.name for p in verb_dir.iterdir() if p.is_dir()]
    verb_group = prompt_if_missing(verb_arg, verb_groups, label="verb group")

    view_runlog_main(project, verb_group)
