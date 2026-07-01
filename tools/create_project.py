import json
import os
from pathlib import Path

BASE = Path("projects")

def create_project(name):
    project_dir = BASE / name
    aliases_dir = project_dir / "aliases"

    if project_dir.exists():
        print(f"\n🚫 Project '{name}' already exists at {project_dir}.")
        print("To avoid accidental data loss, creation has been blocked.")
        print("If you need to add to or modify this project, use register tools instead.\n")
        return

    # Create directories
    for sub in ["nouns", "verbs", "adjectives", "adverbs", "aliases"]:
        os.makedirs(project_dir / sub, exist_ok=True)

    # Aliases (blank)
    alias_files = ["nouns", "verbs", "adjectives", "adverbs"]
    for alias in alias_files:
        alias_path = aliases_dir / f"{alias}.json"
        with open(alias_path, "w") as f:
            json.dump({}, f, indent=2)

    # Config
    config = {
        "name": name,
        "description": "",
        "version": "0.1.0"
    }
    with open(project_dir / "config.json", "w") as f:
        json.dump(config, f, indent=2)

    print(f"\n✅ Project '{name}' created with base folders and config.")

if __name__ == "__main__":
    name = input("Enter new project name: ").strip()
    if not name:
        print("❌ Project name cannot be empty.")
        exit(1)

    create_project(name)