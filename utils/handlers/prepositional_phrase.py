import sys
from pathlib import Path

# Insert project root into sys.path
project_root = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(project_root))
from utils.runner_env import (
    save_runner_image,
    image_exists,
    load_runner_image
)
import json
import hashlib
import shutil
import subprocess
import importlib.util
from pathlib import Path
from utils.handlers.verb import load_full_verb_def
from utils import config, container_run


def _docker_cli() -> str:
    """Resolve the container runtime lazily (auto: rootless podman → docker). Replaces the old
    module-level ``DOCKER_CLI`` constant; image-management falls back to ``"docker"`` best-effort
    while the untrusted run goes through :func:`utils.container_run.run_container`."""
    return config.container_runtime_binary() or "docker"

def validate_io_manifest_pphrase(manifest: dict, project_path: Path, phrase_name: str):
    """
    Validates prepositional phrase manifest:
    - Inputs must come from allowed input folders, unless type=='verb' (which auto-resolves)
    - Outputs must go to prepositional phrases/(phrase)/(declared folder)/(new files)
    """
    try:
        print("🔎 DEBUG: Manifest:")
        print(json.dumps(manifest, indent=2))

        _allowed_input_roots = [
            project_path / "projects" / project_path.name,
            project_path / "docker" / "Prepositional Phrases" / project_path.name / "inputs"
        ]

        allowed_output_root = project_path / "projects" / project_path.name / "prepositional phrases" / phrase_name

        # --- 1) Validate inputs ---
        for alias, entry in manifest.items():
            entry_type = entry.get("type")

            # No path validation here; path resolution is handled downstream
            if entry_type not in ("verb", "noun"):
                raise RuntimeError(f"❌ Unsupported entry type '{entry_type}' for alias '{alias}'")

        # --- 2) Validate outputs ---
        for alias, entry in manifest.items():
            mode = entry.get("mode")
            path_str = entry.get("path")
            if not path_str:
                continue
            path = Path(path_str).resolve()

            if mode in ("write", "readwrite"):
                if not str(path).startswith(str(allowed_output_root.resolve())):
                    raise RuntimeError(
                        f"❌ Output alias '{alias}' path '{path}' is outside allowed output root '{allowed_output_root}'"
                    )
                if "." not in alias:
                    raise RuntimeError(
                        f"❌ Output alias '{alias}' must include file extension (e.g., 'Results.csv')"
                    )
                if "type" not in entry:
                    raise RuntimeError(
                        f"❌ Output alias '{alias}' must declare a 'type' (e.g., 'csv', 'json')"
                    )

        print("✅ validate_io_manifest_pphrase passed.")

    except Exception as e:
        raise RuntimeError(f"❌ validate_io_manifest_pphrase failed: {type(e).__name__}: {e}") from e

def run_prepositional_phrase_container(
    meta: dict,
    project_path: Path,
    phrase_name: str,
    runner_folder: Path,
    entrypoint: str,
    manifest: dict[str, dict],
    mounted_inputs: dict[str, Path],
    active_project: Path,
    network: str = 'none'
) -> bool:
    """
    Run the container for a prepositional phrase.
    Inputs: allowed input folders
    Outputs: projects/(project)/prepositional phrases/(phrase)/(user folders)
    """
    # Hardened invocation (R15): non-root, --read-only rootfs + /tmp tmpfs, --cap-drop=ALL,
    # --security-opt=no-new-privileges, --pids/--memory/--cpus caps, --network=none. The stray
    # '-it' (interactive TTY) is dropped — it breaks every non-interactive/server-triggered run.
    runtime = container_run.runtime_binary_or_raise()
    cmd = [runtime, 'run', *container_run.hardening_flags(runtime, network=network)]
    cmd += container_run.env_flags({'PARSER_ENTRYPOINT': entrypoint})

    # 1) Mount phrase code
    cmd += ['-v', f'{runner_folder.resolve()}:/app/parser:ro']

    print("📎 Mounted Inputs/Outputs:")
    for alias, host_path in mounted_inputs.items():
        host_path = host_path.resolve()
        if not host_path.exists():
            raise RuntimeError(f"❌ Cannot mount '{alias}' → '{host_path}': does not exist")

        # Determine container path
        container_path = f"/app/inputs/{alias}"

        # 🔥 Adjust if this is a noun mounted as a file
        # We detect that by checking if it's a file and endswith items.jsonl
        if host_path.is_file() and host_path.name == "items.jsonl":
            # Instead of mounting the file as alias, mount its parent folder
            # and keep container_path as folder for consistency
            host_path = host_path.parent
            container_path = f"/app/inputs/{alias}"

        # 🔥 Always mount as read-only input
        cmd += ['-v', f'{host_path}:{container_path}:ro']
        print(f" - {alias} (read): {host_path} → {container_path}")


    # 1.5) Mount part-of-speech types
    types = ["noun_types.json", "verb_types.json", "adverb_types.json"]
    for tfile in types:
        tpath = project_path / tfile
        if not tpath.exists():
            raise RuntimeError(f"❌ Required type file missing: {tpath}")
        cmd += ['-v', f'{tpath.resolve()}:/app/types/{tfile}:ro']
        print(f" - {tfile} mounted: {tpath} → /app/types/{tfile}")

    # 1.75) Mount Prepositional Phrase specific inputs
    pphrase_inputs_dir = runner_folder / "inputs"
    if pphrase_inputs_dir.exists():
        for file in sorted(pphrase_inputs_dir.iterdir()):
            if file.is_file():
                container_input_path = f"/app/docker_inputs/{file.name}"
                cmd += ['-v', f'{file.resolve()}:{container_input_path}:ro']
                print(f" - pphrase input mounted: {file} → {container_input_path}")

    # 🔥 ADDITION: Mount all verb group logs
    verbs_dir = project_path / "verbs"
    if verbs_dir.exists():
        for verb_group in sorted(verbs_dir.iterdir()):
            if verb_group.is_dir():
                log_file = verb_group / f"{verb_group.name}_log.jsonl"
                if log_file.exists():
                    container_log_path = f"/app/types/{verb_group.name}_log.jsonl"
                    cmd += ['-v', f'{log_file.resolve()}:{container_log_path}:ro']
                    print(f" - {verb_group.name}_log.jsonl mounted: {log_file} → {container_log_path}")

    # 2) Mount general output dir
    output_root = project_path / "prepositional phrases" / phrase_name
    output_root.mkdir(parents=True, exist_ok=True)
    cmd += ['-v', f'{output_root}:/app/output']
    print(f" - general output dir mounted: {output_root} → /app/output")

    # 3) Build or load image
    # compute_runner_hash(...) ➡️ compute_pphrase_hash(...)
    repo_root = project_path.parent.parent   # or your appropriate root for utils/
    image_hash = compute_pphrase_hash(meta, runner_folder, repo_root, active_project)
    base_tag   = f"localhost/gims_runner_{meta['name']}"
    image_tag  = f"{base_tag}:{image_hash}"

    if image_exists(image_hash, runner_folder):
        print("📦 Using cached image.")
        load_runner_image(image_hash, runner_folder)
    else:
        print("🔨 Building new image...")
        # derive repo root for build context
        repo_root = project_path.parent.parent  # e.g. GIMS-Project
        image_tag = build_pphrase_runner_image(meta, runner_folder, repo_root, active_project)
        save_runner_image(image_tag, image_hash, runner_folder)

    # 4) Run it (timeout + runtime errors surface as AppError via run_container)
    cmd.append(image_tag)
    result = container_run.run_container(cmd)
    return result.returncode == 0

def run_custom_prepositional_phrase(
    project_path: Path,
    phrase_name: str,
    runner_folder: Path,
    entrypoint: str,
    active_project: Path,
    network: str = 'none'
) -> bool:
    """
    1) Load metadata
    2) Get manifest
    3) Validate manifest (inputs allowed from project or docker inputs; outputs in phrase folder)
    4) Build host paths for all aliases (read+write)
    5) Call run_prepositional_phrase_container() with manifest+mounts
    """
    import json
    import re
    import tempfile
    from api import i_o  # lazy: unified-store reader (instances -> RDS -> SQLite -> JSONL)

    # --- load phrase module ---
    spec = importlib.util.spec_from_file_location(
        "phrase_module", runner_folder / entrypoint
    )
    phrase_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(phrase_module)

    meta = phrase_module.get_metadata()
    manifest = phrase_module.get_io_manifest()

    # --- validate manifest ---
    validate_io_manifest_pphrase(manifest, project_path, phrase_name)

    # --- resolve ALL mounts (read & write) ---
    mounted_inputs: dict[str, Path] = {}
    _noun_tmp_dirs: list[Path] = []  # per-noun temp dirs holding a materialised items.jsonl

    for alias, entry in manifest.items():
        entry_type = entry.get("type")
        path_str = entry.get("path")

        if not path_str:
            if entry_type == "verb":
                # derive full data dump folder path for this verb

                verb_def = load_full_verb_def(project_path, alias)
                verb_group = verb_def.get("verb_group")
                if not verb_group:
                    raise RuntimeError(f"❌ Could not find verb_group for '{alias}'")

                verb_data_dump = project_path / "verbs" / verb_group / "data_dumps"
                if not verb_data_dump.exists():
                    raise RuntimeError(f"❌ Data dumps folder does not exist for verb '{alias}': {verb_data_dump}")

                # 🔥 ADDITION: build log path: projects/(project)/verbs/(verb_group)/(verb_group)_log.jsonl
                verb_log_path = project_path / "verbs" / verb_group / f"{verb_group}_log.jsonl"
                if not verb_log_path.exists():
                    raise RuntimeError(f"❌ Verb group log does not exist: {verb_log_path}")

                # mount the verb group log under its own key
                log_alias = f"{alias}_log"
                mounted_inputs[log_alias] = verb_log_path.resolve()

                # for this alias, mount each run folder inside data_dumps
                for run_dir in verb_data_dump.iterdir():
                    if run_dir.is_dir():
                        run_alias = f"{alias}_{run_dir.name}"
                        mounted_inputs[run_alias] = run_dir.resolve()

            elif entry_type == "noun":
                # POST-CUTOVER (R15/Phase 5): nouns no longer live in projects/<p>/nouns/<noun>/
                # items.jsonl — that folder store was retired and the data is now in the unified
                # `instances` SQL store. Read it via i_o.get_noun_items and materialise it to a
                # throwaway items.jsonl (preserving the container's existing items.jsonl input
                # contract, so coa_generator & friends need no change). The old filesystem lookup
                # raised RuntimeError for ANY noun input post-cutover — this is the fix.
                items = i_o.get_noun_items(project_path, alias) or []
                safe = re.sub(r"\W+", "_", alias) or "noun"
                tmp_dir = Path(tempfile.mkdtemp(prefix=f"gims_noun_{safe}_"))
                _noun_tmp_dirs.append(tmp_dir)
                noun_items = tmp_dir / "items.jsonl"
                # Proper JSONL (one record per line) — also fixes the old "[]"-as-items bug.
                noun_items.write_text(
                    "".join(json.dumps(r) + "\n" for r in items), encoding="utf-8"
                )
                if not items:
                    print(f"ℹ️ noun '{alias}' has no instances; mounting an empty items.jsonl")
                mounted_inputs[alias] = noun_items.resolve()

            else:
                raise RuntimeError(f"❌ Alias '{alias}' missing 'path' declaration for unsupported type '{entry_type}'")

    # --- fire it off (always clean up the materialised noun temp dirs) ---
    try:
        return run_prepositional_phrase_container(
            meta=meta,
            project_path=project_path,
            phrase_name=phrase_name,
            runner_folder=runner_folder,
            entrypoint=entrypoint,
            manifest=manifest,
            mounted_inputs=mounted_inputs,
            network=network,
            active_project=active_project
        )
    finally:
        for d in _noun_tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)

def build_pphrase_runner_image(meta: dict, runner_folder: Path, project_root: Path, active_project: Path) -> str:
    """
    Build a Docker image for a prepositional phrase using its Dockerfile.
    Uses project_root as build context to access utils and other shared modules.
    Returns the image tag.
    """

    # Use new compute_pphrase_hash instead of compute_runner_hash
    hash_tag = compute_pphrase_hash(meta, runner_folder, project_root, active_project)
    image_tag = f"localhost/gims_runner_{meta['name']}:{hash_tag}"

    dependencies = meta.get("dependencies", [])
    dep_str = " ".join(dependencies)
    print("this next line is the dep_str")
    print(dep_str)

    subprocess.run([
        _docker_cli(), 'build',
        '-t', image_tag,
        '--build-arg', f'DEPENDENCIES={dep_str}',
        '-f', str(runner_folder / 'Dockerfile'),
        str(project_root)  # build context = project root
    ], check=True)

    return image_tag

def compute_utils_hash(utils_path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    for file in sorted(utils_path.rglob("*")):
        if file.is_file():
            h.update(file.read_bytes())
    return h.hexdigest()[:12]

def compute_part_types_hash(types_path: Path) -> str:
    """
    Compute a deterministic hash of all *_types.json files in the provided path,
    plus each verb group log within verbs/.
    Useful for triggering rebuilds when noun_types, verb_types, etc. change.
    """
    import hashlib
    h = hashlib.sha256()

    # --- 1. Hash *_types.json files ---
    for file in sorted(types_path.glob("*_types.json")):
        if file.is_file():
            h.update(file.name.encode())  # include filename for safety
            h.update(file.read_bytes())

    # --- 2. Hash each (verb_group)_log.jsonl in verbs/ ---
    verbs_dir = types_path / "verbs"
    if verbs_dir.exists():
        for verb_group in sorted(verbs_dir.iterdir()):
            if verb_group.is_dir():
                log_file = verb_group / f"{verb_group.name}_log.jsonl"
                if log_file.exists():
                    h.update(log_file.name.encode())  # include filename for safety
                    h.update(log_file.read_bytes())

    return h.hexdigest()[:12]

def compute_pphrase_hash(meta: dict, runner_folder: Path, project_root: Path, active_project: Path) -> str:
    """
    Compute a deterministic hash for a prepositional phrase image.
    Includes:
    - metadata fields (name, version, dependencies)
    - entrypoint.py contents
    - utils folder hash (so utils changes trigger rebuilds)
    """
    h = hashlib.sha256()

    # 1. Hash meta fields
    h.update(meta.get('name', '').encode())
    h.update(meta.get('version', '').encode())
    for dep in sorted(meta.get('dependencies', [])):
        h.update(dep.encode())

    # 2. Hash entrypoint.py
    entrypoint_path = runner_folder / "entrypoint.py"
    if entrypoint_path.exists():
        h.update(b"entrypoint.py")
        h.update(entrypoint_path.read_bytes())

    # 2.5) Hash inputs in runner folder
    inputs_dir = runner_folder / "inputs"
    if inputs_dir.exists():
        for file in sorted(inputs_dir.iterdir()):
            if file.is_file():
                h.update(file.name.encode())
                h.update(file.read_bytes())

    # 3. Hash utils
    utils_hash = compute_utils_hash(project_root / "utils")
    h.update(utils_hash.encode())

    # 3.5. Hash parts (optionally others)
    part_hash = compute_part_types_hash(active_project)
    h.update(part_hash.encode())

    # 4. Compute final
    image_hash = h.hexdigest()[:12]

    # 5. Clean up stale images
    image_prefix = f"gims_runner_{meta['name']}:"
    current_tag = f"{image_prefix}{image_hash}"

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

    # Delete stale .tar files
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