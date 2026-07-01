import json
import hashlib
import shutil
import subprocess
import tempfile
import importlib.util
from pathlib import Path

from utils import config, container_run


def _docker_cli() -> str:
    """Resolve the container runtime lazily (auto: rootless podman → docker).

    Replaces the old module-level ``DOCKER_CLI`` constant so a host with no runtime can still
    import this module; image-management calls fall back to ``"docker"`` (best effort), while the
    actual untrusted run goes through :func:`utils.container_run.run_container` which raises a
    clear ``AppError`` if the runtime is missing."""
    return config.container_runtime_binary() or "docker"


# NOTE: a second `load_runner_metadata` (reading parser_meta.json) used to be defined
# here, but it was dead — the .py/get_metadata() definition below shadowed it, so every
# call already resolved to that one. Removed (behavior-preserving) to clear F811.

def compute_runner_hash(meta: dict, runner_folder: Path) -> str:
    """
    Compute a deterministic hash based on parser metadata and all relevant source files.
    Deletes any previously cached Docker images and .tar files for this parser name with a different hash.
    """
    h = hashlib.sha256()

    # 1. Hash metadata fields
    h.update(meta.get('name', '').encode())
    h.update(meta.get('version', '').encode())
    for dep in sorted(meta.get('dependencies', [])):
        h.update(dep.encode())

    # 2. Hash contents of entrypoint.py only
    entrypoint_path = runner_folder / "entrypoint.py"
    if entrypoint_path.exists():
        h.update(b"entrypoint.py")
        h.update(entrypoint_path.read_bytes())

    # 3. Compute final hash and tag
    image_hash = h.hexdigest()[:12]
    image_prefix = f"gims_runner_{meta['name']}:"
    current_tag = f"{image_prefix}{image_hash}"

    # 4. Delete older Docker images with same name but different hash
    cli = _docker_cli()
    result = subprocess.run(
        [cli, "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True, text=True
    )
    image_tags = result.stdout.strip().splitlines()
    for tag in image_tags:
        if tag.startswith(image_prefix) and tag != current_tag:
            print(f"🧹 Removing obsolete image: {tag}")
            subprocess.run([cli, "rmi", "-f", tag])

    # 5. Delete stale .tar files in images/ folder
    images_folder = runner_folder / "images"
    images_folder.mkdir(exist_ok=True)

    for tar_file in images_folder.glob("*.tar"):
        if tar_file.stem != image_hash:
            print(f"🧹 Removing stale image archive: {tar_file.name}")
            try:
                tar_file.unlink()
            except Exception as e:
                print(f"⚠️ Failed to delete {tar_file.name}: {e}")

    return image_hash

def image_exists(image_hash: str, runner_folder: Path) -> bool:
    """
    Check if a cached Docker image tarball exists for the given hash.
    """
    tar_path = runner_folder / 'images' / f'{image_hash}.tar'
    return tar_path.exists()


def load_runner_image(image_hash: str, runner_folder: Path) -> None:
    """
    Load a Docker image from a tarball in the runner's images folder.
    """
    tar_path = runner_folder / 'images' / f'{image_hash}.tar'
    if not tar_path.exists():
        raise FileNotFoundError(f'Image tar not found: {tar_path}')
    subprocess.run([_docker_cli(), 'load', '-i', str(tar_path)], check=True)

def build_runner_image(meta: dict, runner_folder: Path) -> str:
    """
    Build a Docker image for the runner using its Dockerfile.
    Passes dependencies as build-arg.
    Returns the image tag.
    """
    hash_tag = compute_runner_hash(meta, runner_folder)
    image_tag = f"gims_runner_{meta['name']}:{hash_tag}"

    # Combine dependencies into a single string
    dependencies = meta.get("dependencies", [])
    dep_str = " ".join(dependencies)
    print("this next line is the dep_str")
    print(dep_str)

    subprocess.run([
        _docker_cli(), 'build',
        '-t', image_tag,
        '--build-arg', f'DEPENDENCIES={dep_str}',
        str(runner_folder)
    ], check=True)

    return image_tag

def save_runner_image(image_tag: str, image_hash: str, runner_folder: Path) -> None:
    """
    Save the built Docker image to a tarball in runner_folder/images.
    """
    images_dir = runner_folder / 'images'
    images_dir.mkdir(exist_ok=True)
    tar_path = images_dir / f'{image_hash}.tar'
    subprocess.run([_docker_cli(), 'save', '-o', str(tar_path), image_tag], check=True)

def run_parser_container(
    meta: dict,
    verb_config: dict,
    run_id: str,
    runner_folder: Path,
    entrypoint: str,
    manifest: dict[str, dict],
    mounted_inputs: dict[str, Path],
    network: str = 'none'
) -> bool:
    """
    Run the container for the parser with mounts driven purely by the manifest.
    Reads go to /app/inputs/{alias}, writes to /app/output/{alias}.{type}.
    """
    # Hardened invocation (R15): non-root, --read-only rootfs + /tmp tmpfs, --cap-drop=ALL,
    # --security-opt=no-new-privileges, --pids/--memory/--cpus caps, --network=none. The bind
    # mounts below stay writable where declared (read-only affects only the rootfs).
    runtime = container_run.runtime_binary_or_raise()
    cmd = [runtime, 'run', *container_run.hardening_flags(runtime, network=network)]
    cmd += container_run.env_flags({'PARSER_ENTRYPOINT': entrypoint})

    # 1) Mount your parser code
    cmd += ['-v', f'{runner_folder.resolve()}:/app/parser:ro']

    # Build reverse mapping from stemmed alias → full alias
    norm_to_full = {}
    for full_alias in manifest:
        stem = Path(full_alias).stem
        if stem in norm_to_full:
            raise RuntimeError(f"❌ Duplicate stem '{stem}' in manifest (from '{full_alias}')")
        norm_to_full[stem] = full_alias

    print("📎 Mounted Inputs/Outputs:")
    for alias, host_path in mounted_inputs.items():
        full_alias = norm_to_full.get(alias, alias)
        host_path = host_path.resolve()

        if not host_path.exists():
            raise RuntimeError(f"❌ Cannot mount '{full_alias}' → '{host_path}': does not exist")

        mode = manifest[full_alias]['mode']

        if mode == 'read':
            # If it's a folder, pick the first file inside
            if host_path.is_dir():
                files = list(host_path.glob("*"))
                if not files:
                    raise RuntimeError(
                        f"❌ No files found in folder '{host_path}' for alias '{alias}'"
                    )
                host_file = files[0]
                print(f"🔎 Mounting file '{host_file.name}' for alias '{alias}'")
            else:
                host_file = host_path

            container_path = f"/app/inputs/{alias}"
            mount_arg = f"{host_file}:{container_path}:ro"
            cmd += ['-v', mount_arg]
            print(f" - {alias} (read): {host_file} → {container_path}")

        elif mode == 'write':
            container_path = f"/app/output/{full_alias}"
            mount_arg = f"{host_path}:{container_path}"
            cmd += ['-v', mount_arg]
            print(f" - {alias} (write): {host_path} → {container_path}")

        elif mode == 'readwrite':
            input_path = f"/app/inputs/{alias}"
            output_path = f"/app/output/{full_alias}"

            # Mount same host_path for both read and write
            cmd += ['-v', f"{host_path}:{input_path}:ro"]
            cmd += ['-v', f"{host_path}:{output_path}"]
            print(f" - {alias} (readwrite):")
            print(f"     ↳ read from  {host_path} → {input_path}")
            print(f"     ↳ write to   {host_path} → {output_path}")

        else:
            raise RuntimeError(f"❌ Unknown mode '{mode}' for alias '{alias}'")

    # 1.5) Mount a general output dir to ensure /app/output exists. Use a PER-RUN isolated
    #      temp dir (not a shared /tmp/gims_output) so concurrent runs can't see each other's
    #      scratch and an untrusted tool only ever gets a fresh, empty writable surface.
    output_root = Path(tempfile.mkdtemp(prefix="gims_output_"))
    cmd += ['-v', f'{output_root}:/app/output']
    print(f" - general output dir mounted: {output_root} → /app/output")

    # 2) Build or load Docker image
    image_hash = compute_runner_hash(meta, runner_folder)
    image_tag = f"gims_runner_{meta['name']}:{image_hash}"
    if image_exists(image_hash, runner_folder):
        print("📦 Using cached image.")
        load_runner_image(image_hash, runner_folder)
    else:
        print("🔨 Building new image...")
        image_tag = build_runner_image(meta, runner_folder)
        save_runner_image(image_tag, image_hash, runner_folder)

    # 3) Run it (timeout + runtime errors surface as AppError via run_container). Always remove
    #    the per-run scratch output dir afterward — real parser outputs go to the write-mode
    #    project mounts, /app/output is only a transient mount point, so it must not accumulate.
    cmd.append(image_tag)
    try:
        result = container_run.run_container(cmd)
        return result.returncode == 0
    finally:
        shutil.rmtree(output_root, ignore_errors=True)

def run_custom_parser(
    project_path: Path,
    run_id: str,
    runner_folder: Path,
    entrypoint: str,
    network: str = 'none'
) -> bool:
    """
    1) Load metadata & verb config
    2) Get manifest & expected_paths
    3) Build host paths for all aliases (read+write)
    4) Call run_parser_container() with manifest+mounts
    """
    # --- load parser ---
    spec = importlib.util.spec_from_file_location(
        "parser_module", runner_folder / entrypoint
    )
    parser_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser_module)
    meta = parser_module.get_metadata()
    verb_key = meta.get("verb") or RuntimeError("❌ metadata.verb missing")

    # --- verb config & dump root ---
    verb_config = json.loads((project_path/"verb_types.json").read_text())[verb_key]
    dump_root = project_path/"verbs"/verb_config["verb_group"]/"data_dumps"/run_id

    # --- manifest & validation ---
    manifest = parser_module.get_io_manifest()
    expected = extract_expected_paths(verb_config, dump_root, manifest)
    validate_io_manifest(manifest, expected)

    # --- resolve ALL mounts (read & write) ---
    mounted_inputs: dict[str,Path] = {}
    for alias, spec in expected.items():
        mounted_inputs[alias] = spec["path"]

    # --- fire it off ---
    return run_parser_container(
        meta=meta,
        verb_config=verb_config,
        run_id=run_id,
        runner_folder=runner_folder,
        entrypoint=entrypoint,
        manifest=manifest,
        mounted_inputs=mounted_inputs,
        network=network
    )

def build_resolved_inputs(manifest: dict, expected_paths: dict, verb_config: dict, dump_root: Path) -> dict:
    resolved_inputs = {}

    raw_data = verb_config.get("data_entry_schema", {}).get("raw_data_inputs", {})
    raw_aliases = set(raw_data) if isinstance(raw_data, list) else set(raw_data.keys())

    for alias in manifest:
        # Use the expected_paths directly (it's already built from dump_root + alias)
        path = expected_paths[alias]["path"]
        if alias in raw_aliases:
            # Validate that it's a directory with one file
            if not path.exists():
                raise RuntimeError(f"❌ Raw zone folder '{alias}' does not exist at {path}")
            if not path.is_dir():
                raise RuntimeError(f"❌ Expected raw input '{alias}' to be a directory, got file: {path}")

            files = [f for f in path.iterdir() if f.is_file()]
            if len(files) != 1:
                raise RuntimeError(f"❌ Raw zone '{alias}' must contain exactly one file, found: {len(files)}")

        else:
            # Validate that it's a file
            if not path.exists():
                raise RuntimeError(f"❌ File input '{alias}' does not exist at {path}")
            if not path.is_file():
                raise RuntimeError(f"❌ Expected file input '{alias}' to be a file, got directory: {path}")

        resolved_inputs[alias] = path.resolve()

    return resolved_inputs

def load_runner_metadata(runner_folder: Path) -> dict:
    """
    Loads metadata from the first .py file in runner_folder by calling get_metadata().
    Automatically includes the filename as 'entrypoint'.
    """
    # Only count real parser scripts, not entrypoint glue
    py_files = [f for f in runner_folder.glob("*.py") if f.name != "entrypoint.py"]

    if not py_files:
        raise FileNotFoundError(f"No Python parser script found in {runner_folder} (excluding entrypoint.py)")

    if len(py_files) > 1:
        raise ValueError(f"Multiple Python parser scripts found in {runner_folder}. Please keep only one (excluding entrypoint.py)")

    _parser_script = py_files[0].name

    entrypoint_path = py_files[0]
    spec = importlib.util.spec_from_file_location("custom_parser", entrypoint_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "get_metadata"):
        raise AttributeError(f"{entrypoint_path.name} does not define get_metadata()")

    metadata = module.get_metadata()

    if not isinstance(metadata, dict):
        raise TypeError("get_metadata() must return a dictionary")

    metadata.setdefault("entrypoint", entrypoint_path.name)

    return metadata

def extract_expected_paths(verb_config: dict, run_path: Path, parser_manifest: dict = None) -> dict:
    expected = {}

    # --- Raw zones → fully declared relative paths or legacy list of folders
    raw_inputs = verb_config.get("data_entry_schema", {}).get("raw_data_inputs", {})

    if isinstance(raw_inputs, dict):
        for alias, spec in raw_inputs.items():
            if not parser_manifest or alias in parser_manifest:
                expected[alias] = {
                    "path": run_path / spec["path"],
                    "must_be": spec.get("mode", "read")
                }

    elif isinstance(raw_inputs, list):
        for alias in raw_inputs:
            expected[alias] = {
                "path": run_path / alias,
                "must_be": "read"
            }

    else:
        raise TypeError("❌ raw_data_inputs must be a dict or list")

    if not parser_manifest or "data_entry" in parser_manifest:
        expected["data_entry"] = {
            "path": run_path / "DataEntry.json",
            "must_be": "read"
        }

    # Adverbs if schema exists AND parser declares it
    if "adverb_schema" in verb_config:
        if parser_manifest and "adverbs" in parser_manifest:
            expected["adverbs"] = {
                "path": run_path / "adverbs.json",
                "must_be": "read"
            }

    # Interpretation outputs (manual or parser-generated)
    for tab in verb_config.get("data_entry_schema", {}).get("interpretation", {}).get("tabs", []):
        # Only include if the parser manifest declares it (full key with .csv)
        matching_alias = None
        for manifest_alias in parser_manifest:
            if Path(manifest_alias).stem == tab:
                matching_alias = manifest_alias
                break

        if matching_alias:
            expected[matching_alias] = {
                "path": run_path / matching_alias,
                "must_be": None  # writable
            }

    return expected

def serialize_for_debug(obj):
    if isinstance(obj, dict):
        return {k: serialize_for_debug(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_for_debug(v) for v in obj]
    elif isinstance(obj, Path):
        return str(obj)
    else:
        return obj

def validate_io_manifest(manifest: dict, expected_paths: dict):
    try:
        # --- DEBUG: Print manifest and expected paths for context ---
        print("🔎 DEBUG: Manifest:")
        print(json.dumps(manifest, indent=2))
        print("🔎 DEBUG: Expected paths:")
        print(json.dumps(serialize_for_debug(expected_paths), indent=2))

        # --- 1) Normalize manifest aliases (strip extension for validation only) ---
        normalized_manifest = {}
        for alias, entry in manifest.items():
            base_name = Path(alias).stem  # 'CFU Calculations.csv' → 'CFU Calculations'
            if base_name in normalized_manifest:
                raise RuntimeError(f"❌ Duplicate base alias '{base_name}' in manifest (from '{alias}')")
            normalized_manifest[base_name] = entry

        # --- 2) Check for unexpected aliases ---
        for norm_alias in normalized_manifest:
            if norm_alias not in expected_paths:
                declared_mode = normalized_manifest[norm_alias].get("mode")
                if declared_mode in ("write", "readwrite"):
                    # Outputs are allowed even if not in expected_paths
                    continue
                raise RuntimeError(
                    f"❌ Manifest declares illegal alias '{norm_alias}' (not in expected data dump)"
                )

        # --- 3) Validate access modes ---
        for alias, spec in expected_paths.items():
            expected_mode = spec.get("must_be")  # e.g., "read", "write", or None
            declared_entry = normalized_manifest.get(alias)
            if declared_entry:
                declared_mode = declared_entry.get("mode")
            else:
                declared_mode = None
            if expected_mode:
                if declared_mode is None:
                    # The parser doesn't declare this alias. Skip enforcing mode.
                    continue
                if declared_mode != expected_mode:
                    raise RuntimeError(
                        f"❌ Alias '{alias}' has mode '{declared_mode}', expected '{expected_mode}'"
                    )

        # --- 4) Enforce file extension and declared type for write-mode entries ---
        for full_alias, entry in manifest.items():
            mode = entry.get("mode")
            if mode in ("write", "readwrite"):
                if "." not in full_alias:
                    raise RuntimeError(
                        f"❌ Output alias '{full_alias}' must include file extension (e.g., 'Results.csv')"
                    )
                if "type" not in entry:
                    raise RuntimeError(
                        f"❌ Output alias '{full_alias}' must declare a 'type' (e.g., 'csv', 'json')"
                    )

    except KeyError as e:
        raise RuntimeError(f"❌ Missing expected alias in manifest: {e}") from e
    except Exception as e:
        raise RuntimeError(f"❌ validate_io_manifest failed: {type(e).__name__}: {e}") from e

def execute_parser_runner(project_path: Path, verb_key: str, run_id: str) -> bool:
    """
    Locate and run all declared parser(s) for the given run using the explicit verb_key.
    """
    # 1. Locate run path
    run_path = next(project_path.glob(f"verbs/*/data_dumps/{run_id}"), None)
    if not run_path or not run_path.is_dir():
        raise FileNotFoundError(f"❌ Could not find data dump for run ID '{run_id}'.")

    print(f"🔍 Running parser(s) for verb: {verb_key}, run ID: {run_id}")

    # 2. Load verb config to find declared parser(s)
    vt_path = project_path / "verb_types.json"
    verb_types = json.loads(vt_path.read_text())
    if verb_key not in verb_types:
        raise KeyError(f"❌ Verb '{verb_key}' not found in {vt_path}")
    schema = verb_types[verb_key]["data_entry_schema"]
    parser_names = schema.get("interpretation", {}).get("parsers", [])

    if not parser_names:
        raise RuntimeError(f"❌ No parser(s) declared for verb '{verb_key}' in verb_types.json")

    # 3. Run each parser in order
    all_success = True
    for parser_name in parser_names:
        runner_folder = Path("docker") / "Parsers" / parser_name

        # find the actual parser script (not the wrapper)
        parser_script = next(
            (p for p in runner_folder.glob("*.py") if p.name != "entrypoint.py"),
            None
        )
        if not (runner_folder.exists() and parser_script):
            raise RuntimeError(f"❌ Parser folder '{parser_name}' missing required files.")

        try:
            # **Pass parser_script.name**, not "entrypoint.py"**
            success = run_custom_parser(
                project_path=project_path,
                run_id=run_id,
                runner_folder=runner_folder,
                entrypoint=parser_script.name
            )
            if not success:
                print(f"❌ Parser '{parser_name}' failed.")
                all_success = False
        except Exception as e:
            print(f"❌ Exception during parser '{parser_name}': {e}")
            all_success = False

    return all_success