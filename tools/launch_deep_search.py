import sys
import json
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from utils.deep_search import deep_search, explain_schema, explain_instance
from utils.interface import prompt_if_missing, indexed_choice
from tools.view import enter_investigate_mode

def launch_deep_search(project_arg=None):
    projects_dir = Path("projects")
    projects = [p.name for p in projects_dir.iterdir() if p.is_dir()]
    project = prompt_if_missing(project_arg, projects, label="project")

    project_path = Path(projects_dir / project)

    print("\n🔎 Enter search terms (or 'q' to quit):")
    while True:
        search_term = input("> ").strip()
        if search_term.lower() in ['q', 'quit', 'exit']:
            print("👋 Exiting deep search.")
            break
        if not search_term:
            print("⚠️ Please enter a search term or 'q' to quit.")
            continue

        matches = deep_search(search_term, project_path)

        if not matches:
            print(f"❌ No match found for '{search_term}'.")
            continue

        # If only one match, select it automatically
        if len(matches) == 1:
            chosen = matches[0]
        else:
            options = []
            for m in matches:
                display_id = "unknown"
                if m['type'] == 'noun_instance':
                    item = m['data']
                    noun_type = item.get('_noun_type')
                    primary_id_field = get_primary_id_field(project_path, noun_type)
                    display_id = item.get(primary_id_field, 'unknown') if primary_id_field else 'unknown'
                elif m['type'] == 'verb_run_instance':
                    display_id = m['data'].get('run_ID', 'unknown')
                options.append(f"{display_id}: {m['type']}")
            idx = indexed_choice(options, f"Multiple matches for '{search_term}'. Select one to view")
            if idx is None:
                print("❎ Cancelled.")
                continue
            chosen = matches[idx]

        print(f"\n🔍 Deep Search Result for '{search_term}':")
        print(f"Type: {chosen['type']}")

        if chosen['type'].endswith('schema'):
            explanation = explain_schema(chosen['data'], chosen['type'])
            print(explanation)
        elif chosen['type'] == 'noun_instance':
            explanation = explain_instance(chosen['data'])
            print(explanation)

            # 🔥 OFFER INVESTIGATE MODE
            yn = input("\n🕵️ Investigate this noun instance? (y/n): ").strip().lower()
            if yn == 'y':
                noun_type = chosen['data'].get('_noun_type')
                items = [chosen['data']]
                enter_investigate_mode(project, noun_type, items)
        elif chosen['type'] == 'verb_run_instance':
            explanation = explain_instance(chosen['data'])
            print(explanation)
        else:
            print(chosen['data'])

        print("\n🔎 Enter another search term (or 'q' to quit):")

def get_primary_id_field(project_path: Path, noun_type: str) -> str:
    noun_types_path = project_path / "noun_types.json"
    noun_types = json.loads(noun_types_path.read_text()) if noun_types_path.exists() else {}
    return noun_types.get(noun_type, {}).get('primary_id_field')

if __name__ == "__main__":
    project_arg = sys.argv[1] if len(sys.argv) > 1 else None
    launch_deep_search(project_arg)
