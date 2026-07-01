import shutil
from pathlib import Path
import tkinter as tk
from tkinter import filedialog
import re
import pandas as pd

def sanitize_filename(name: str) -> str:
    """
    Convert zone names like 'Raw Data: PCR Output' → 'Raw_Data_PCR_Output.csv'
    """
    name = name.strip()
    name = re.sub(r'[^a-zA-Z0-9_]+', '_', name)
    name = re.sub(r'_+', '_', name)
    return name.strip('_') + ".csv"

def upload_file_to_folder(dest_folder: Path, allowed_exts={".csv", ".xlsx"}) -> Path | None:
    """
    Launch file picker and upload a file with allowed extensions to dest_folder.
    Returns Path to uploaded file or None if failed.
    """
    root = tk.Tk()
    root.withdraw()  # Hide main tkinter window

    filetypes = [
        ("CSV files", "*.csv"),
        ("Excel files", "*.xlsx")
    ]

    print("📂 Please select a file to upload...")
    filepath = filedialog.askopenfilename(title="Select file", filetypes=filetypes)

    if not filepath:
        print("❌ No file selected.")
        return None

    src_path = Path(filepath)
    if src_path.suffix.lower() not in allowed_exts:
        print(f"❌ Invalid file type: {src_path.suffix}")
        return None

    dest_folder.mkdir(parents=True, exist_ok=True)
    dest_path = dest_folder / src_path.name

    try:
        shutil.copy(src_path, dest_path)
        print(f"✅ File uploaded to: {dest_path}")
        return dest_path
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return None

def _print_csv_or_xlsx(path: Path):
    """
    Prints the first few rows of a CSV or XLSX file for preview.
    """
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    elif path.suffix.lower() == ".xlsx":
        xls = pd.ExcelFile(path)
        print(f"📑 Sheets in {path.name}: {xls.sheet_names}")
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet_name)
            print(f"\n🔹 Sheet: {sheet_name}")
            print(df.head())
    else:
        print(f"❌ Unsupported file type: {path.suffix}")
        return
    print(df.head())

def upload_parser_script(docker_parsers_dir: Path) -> str | None:
    """
    Launch a file picker and upload a `.py` parser script.
    Creates a new folder in docker/Parsers/{parser_name}/
    Includes entrypoint.py, Dockerfile, and an images/ dir.
    Returns the parser name (folder name) if successful, else None.
    """
    print("📂 Please select a Python parser script to upload (*.py)...")

    root = tk.Tk()
    root.withdraw()
    filepath = filedialog.askopenfilename(filetypes=[("Python files", "*.py")])
    root.destroy()

    if not filepath:
        print("❌ No file selected.")
        return None

    try:
        src = Path(filepath)
        parser_name = src.stem
        target_folder = docker_parsers_dir / parser_name

        if target_folder.exists():
            print(f"⚠️ Parser '{parser_name}' already exists in {docker_parsers_dir}.")
            return parser_name

        # Create folder structure
        target_folder.mkdir(parents=True)
        (target_folder / "images").mkdir()

        # Copy main parser script
        shutil.copy(src, target_folder / src.name)

        # Write default entrypoint.py
        (target_folder / "entrypoint.py").write_text(_default_entrypoint_py())

        # Write default Dockerfile
        (target_folder / "Dockerfile").write_text(_default_dockerfile())

        print(f"✅ Uploaded parser '{parser_name}' to: {target_folder}")
        return parser_name

    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return None


def _default_dockerfile() -> str:
    return """FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Let runner_env.py handle dependency logic — we copy only the entrypoint now
COPY entrypoint.py /app/

# Dependencies and script mounting will be handled at runtime
ENTRYPOINT ["python", "entrypoint.py"]
"""


def _default_entrypoint_py() -> str:
    return """import os
import importlib.util
from pathlib import Path

# Load the entrypoint script name from env
entry_name = os.environ.get("PARSER_ENTRYPOINT")
if not entry_name:
    raise RuntimeError("PARSER_ENTRYPOINT not set")

# Resolve the parser module path
parser_path = Path("/app/parser") / entry_name
if not parser_path.exists():
    raise FileNotFoundError(f"Entrypoint not found: {parser_path}")

# Dynamically import the parser module
spec = importlib.util.spec_from_file_location("custom_parser", str(parser_path))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Build inputs dict based on IO manifest
manifest = mod.get_io_manifest()

print(f"📜 Manifest declared: {manifest}")

inputs = {
    alias: Path("/app/inputs") / alias
    for alias, spec_entry in manifest.items()
    if spec_entry.get("mode") == "read"
}

# Determine output directory if any writeable mounts exist
output_dir = None
if any(spec_entry.get("mode") in ("write", "readwrite") for spec_entry in manifest.values()):
    output_dir = Path("/app/output")

print(f"📤 Output dir resolved to: {output_dir} (exists: {output_dir.exists()})")

# Invoke the parser with manifest-driven signature
mod.run_parser(inputs, output_dir)
"""