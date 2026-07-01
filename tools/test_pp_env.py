import sys
from pathlib import Path

# --- Define base project root and active project ---
project_root = Path(__file__).resolve().parent.parent
active_project = project_root / "projects" / "LIMS-System"

# Insert project root into sys.path for utils imports
sys.path.insert(0, str(project_root))

from utils.handlers.prepositional_phrase import run_custom_prepositional_phrase

def execute_pp_runner(project_root: Path, project_path: Path, phrase_name: str) -> bool:
    """
    Locate and run the declared prepositional phrase script for the given phrase_name
    INSIDE a container, using run_custom_prepositional_phrase().
    """
    # Locate runner folder under docker/Prepositional Phrases
    runner_folder = project_root / "docker" / "Prepositional Phrases" / phrase_name
    if not runner_folder.exists():
        raise FileNotFoundError(f"❌ Prepositional phrase folder '{runner_folder}' not found.")

    print(f"🔍 Running '{phrase_name}' inside container...")
    success = run_custom_prepositional_phrase(
        project_path=project_path,
        phrase_name=phrase_name,
        runner_folder=runner_folder,
        entrypoint=f"{phrase_name}.py",
        active_project=active_project
    )
    return success

if __name__ == "__main__":
    phrase_name = "coa_generator"
    success = execute_pp_runner(project_root, active_project, phrase_name)
    sys.exit(0 if success else 1)