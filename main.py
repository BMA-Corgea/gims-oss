import os
import json
from pathlib import Path
from utils.semantics import get_display_name

PROJECTS_DIR = Path("projects")


def list_projects():
    return [f.name for f in PROJECTS_DIR.iterdir() if f.is_dir()]


def choose_project():
    projects = list_projects()
    if not projects:
        print("No projects found.")
        exit(1)

    print("Choose a project:")
    for i, name in enumerate(projects, start=1):
        print(f"{i}. {name}")

    choice = input("> ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(projects)):
        print("Invalid choice.")
        exit(1)

    return projects[int(choice) - 1]


def load_language_components(project_name):
    base = PROJECTS_DIR / project_name

    def load(file_name):
        with open(base / file_name) as f:
            return json.load(f)

    def load_alias(file_name):
        path = base / "aliases" / file_name
        if not path.exists():
            return {}
        with open(path) as f:
            return json.load(f)

    return {
        "config": load("config.json"),
        "nouns": load("noun_types.json"),
        "verbs": load("verb_types.json"),
        "adjectives": load("adjective_types.json"),
        "adverbs": load("adverb_types.json"),
        "aliases": {
            "nouns": load_alias("nouns.json"),
            "verbs": load_alias("verbs.json"),
            "adjectives": load_alias("adjectives.json"),
            "adverbs": load_alias("adverbs.json")
        }
    }


if __name__ == "__main__":
    project = choose_project()
    print(f"\n Loading project: {project}")
    language = load_language_components(project)
    print(" Language components loaded.\n")

    # Show off the noun display names from aliases
    print(" Noun Types and Display Names:")
    for noun_key in language["nouns"].keys():
        display = get_display_name(noun_key, "nouns", language)
        print(f" - {noun_key} -> {display}")