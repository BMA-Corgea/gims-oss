import os
import zipfile
from pathlib import Path

OUTPUT_FILE = "AWS GIMS Project 1-0-0.zip"

# Folders and files to exclude
EXCLUDES = { 
    ".git", 
    "__pycache__",
    "node_modules", 
    ".venv", 
    "dist", 
    "build", 
    "backups", 
    OUTPUT_FILE, 
    "make_deploy_zip.py", 
    ".venv", 
    ".pytest_cache", 
    "build", 
    "dist",
    "gims-electron",
    "docker",
    }

# Special-case: exclude backups/* but keep backups/_config
def should_exclude(path: Path) -> bool:
    parts = path.parts

    # Exclude if it matches a top-level exclusion
    for part in parts:
        if part in EXCLUDES:
            return True

    # Handle backups/ rule
    if "backups" in parts:
        # If it's inside backups/ but NOT backups/_config, exclude
        if "_config" not in parts:
            return True

    return False


def make_zip():
    root = Path(".").resolve()
    zip_path = root / OUTPUT_FILE
    if zip_path.exists():
        zip_path.unlink()  # remove old zip

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in root.rglob("*"):
            if path.is_file() and not should_exclude(path.relative_to(root)):
                zf.write(path, path.relative_to(root))

    print(f"Created {zip_path} successfully!")


if __name__ == "__main__":
    make_zip()
