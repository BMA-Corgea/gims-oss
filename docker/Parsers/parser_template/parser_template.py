# docker/Template/template_custom_parser.py

from pathlib import Path
import json

# ==============================================================================
# 🧠 MAIN PARSER FUNCTION
# This is what gets run inside the Docker container.
# It receives:
# - `inputs`: a dict of read-only input paths from the manifest
# - `output_dir`: a writable output directory if any outputs are declared
# ==============================================================================
def run_parser(inputs: dict[str, Path], output_dir: Path | None = None):
    print("👋 Hello from inside the Docker jail!")

    # 🔍 Print where we’re allowed to write (if at all)
    print(f"📤 Output directory is: {output_dir} (exists: {output_dir.exists() if output_dir else 'N/A'})")
    if not output_dir or not output_dir.exists():
        print("❌ No output_dir provided or it doesn't exist.")
        return

    # ==============================================================================
    # 📘 READ DATA ENTRY (OPTIONAL)
    # If your parser depends on setup inputs from the technician, you can inspect them here.
    # This file is automatically created from the GIMS spreadsheet interface.
    # ==============================================================================
    data_entry_path = inputs.get("data_entry")
    if data_entry_path and data_entry_path.exists():
        print(f"📘 Reading DataEntry.json: {data_entry_path}")
        try:
            with open(data_entry_path) as f:
                data = json.load(f)

            # Preview the data for debugging
            if isinstance(data, list) and data:
                print(f"🔑 Top-level keys: {list(data[0].keys())}")
                print("🔹 First row:", data[0])
                if len(data) > 1:
                    print("🔹 Second row:", data[1])
            else:
                print("📦 DataEntry.json is not a list or is empty.")
        except Exception as e:
            print(f"❌ Failed to parse DataEntry.json as JSON: {e}")
    else:
        print("❌ DataEntry.json not found in inputs.")

    # ==============================================================================
    # 📝 EDIT OUTPUT FILE (REQUIRED FOR MOST PARSERS)
    # This is an example of writing to a declared writable output.
    # You must declare this file in your manifest as mode: "write" or "readwrite".
    # ==============================================================================
    input_path = output_dir / "CFU Calculations.csv"
    if not input_path.exists():
        print(f"❌ CFU Calculations.csv does not exist in output dir: {input_path}")
        return

    # This shows how to append a simple change to the file
    input_path.write_text(input_path.read_text() + "\n?")

    print(f"📄 Read from {input_path}:")
    content = input_path.read_text()
    print(content)

    # ==============================================================================
    # 🔐 ATTEMPT TO WRITE TO READ-ONLY INPUT (DEMONSTRATION OF FAILURE)
    # This proves that read-only mounts are enforced.
    # Any attempt to write to input files like "PCR Output" should fail.
    # ==============================================================================
    pcr_dir = inputs.get("PCR Output")
    if not pcr_dir or not pcr_dir.exists():
        print("❌ PCR Output directory is missing.")
        return

    if not pcr_dir.is_dir():
        print("❌ PCR Output path is not a directory.")
        return

    try:
        # Attempt to write to a file inside PCR Output (should raise an error)
        target_file = next(pcr_dir.iterdir())
        print(f"📤 Writing to PCR Output: {target_file}")
        target_file.write_text(content)
        print("❗️ Unexpectedly succeeded in writing to read-only PCR Output.")
    except Exception as e:
        print(f"✅ Expected failure occurred when writing to read-only PCR Output:\n   {e}")


# ==============================================================================
# 🧬 PARSER METADATA (REQUIRED)
# This tells the Docker runner how to install dependencies and match your parser
# to a verb and entrypoint.
# ------------------------------------------------------------------------------
# - name: just a friendly label
# - entrypoint: must match the filename
# - verb: must match a registered verb name in GIMS
# - dependencies: any pip packages to install inside Docker
# ==============================================================================
def get_metadata():
    return {
        "name": "retention_time_validator",
        "entrypoint": "hello_world.py",
        "verb": "Micro_Test",
        "dependencies": ["pandas", "numpy", "openpyxl"]  # ← install only what you use
    }


# ==============================================================================
# 📦 IO MANIFEST (REQUIRED)
# This controls which files GIMS will mount into the container.
# ------------------------------------------------------------------------------
# Each entry must specify:
# - mode: one of "read", "write", or "readwrite"
# - type: optional (e.g., "csv" or "json") – for documentation or future use
# ==============================================================================
def get_io_manifest():
    return {
        "PCR Output": {"mode": "read"},
        "data_entry": {"mode": "read"},
        "adverbs": {"mode": "read"},
        "CFU Calculations.csv": {
            "mode": "readwrite",
            "type": "csv"
        },
    }


# ==============================================================================
# 🚀 LOCAL ENTRYPOINT (REQUIRED)
# This gets called when the parser is launched inside the container.
# It constructs the `inputs` and `output_dir` using the declared manifest.
# ==============================================================================
if __name__ == "__main__":
    manifest = get_io_manifest()
    inputs = {
        alias: Path("/app/inputs") / alias
        for alias, spec in manifest.items()
        if spec["mode"] == "read"
    }
    output_dir = Path("/app/output") if any(
        spec["mode"] in ("write", "readwrite") for spec in manifest.values()
    ) else None
    run_parser(inputs, output_dir)