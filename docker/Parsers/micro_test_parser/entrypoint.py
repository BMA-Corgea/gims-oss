import os
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