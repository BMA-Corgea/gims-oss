# docker/parser_runner/hello_world.py

from pathlib import Path
import json

def run_parser(inputs: dict[str, Path], output_dir: Path | None = None):
    print("👋 Hello from inside the Docker jail!")
    if not output_dir or not output_dir.exists():
        print("❌ No output_dir provided or it doesn't exist.")
        return

    # 1) Locate the target file
    cfucalc = output_dir / "CFU Calculations.csv"
    if not cfucalc.exists():
        print(f"❌ CFU Calculations.csv not found at: {cfucalc}")
        return

    # 2) Append a question mark
    text = cfucalc.read_text()
    cfucalc.write_text(text + "\n?")

    print(f"✅ Appended '?' to {cfucalc}")

def get_metadata():
    return {
        "name": "pcr_cfu_calculator",
        "entrypoint": "micro_test_parser.py",
        "verb": "Micro_Test",  # 🔥 This tells runner_env how to validate expectations
        "dependencies": [
            "pandas",
            "numpy",
            "openpyxl"
        ]
    }

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

if __name__ == "__main__":
    manifest = get_io_manifest()
    inputs = {alias: Path(spec["path"]) for alias, spec in manifest.items() if spec["mode"] == "read"}
    output_dir = Path("/app/output") if any(spec["mode"] == "write" for spec in manifest.values()) else None
    run_parser(inputs, output_dir)
