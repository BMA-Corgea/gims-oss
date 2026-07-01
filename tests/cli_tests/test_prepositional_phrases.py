# tests/test_prepositional_phrase.py

import pytest
import json
import subprocess
from pathlib import Path
from utils.handlers import prepositional_phrase as pp

@pytest.fixture
def mock_runner_folder(tmp_path):
    folder = tmp_path / "coa_generator"
    folder.mkdir()

    # Dummy metadata
    meta = {
        "name": "coa_generator",
        "entrypoint": "coa_generator.py",
        "dependencies": ["python-docx"]
    }
    (folder / "parser_meta.json").write_text(json.dumps(meta))

    # Dummy coa_generator.py script
    parser_code = """
def get_metadata():
    return {"name": "coa_generator", "version": "0.1", "dependencies": ["python-docx"]}

def get_io_manifest():
    return {
        "Potency_Test": {"type": "verb"},
        "Terpene_Test": {"type": "verb"},
        "COA Name Map": {"type": "noun"},
        "Primary Aromas": {"type": "noun"},
        "Submission": {"type": "noun"},
    }

def run_pphrase(output_dir, payload):
    print("Ran COA generator successfully.")
"""
    (folder / "coa_generator.py").write_text(parser_code)

    # Dummy entrypoint.py
    (folder / "entrypoint.py").write_text("""
import os
import importlib.util
from pathlib import Path

entry_name = os.environ.get("PARSER_ENTRYPOINT")
parser_path = Path("/app/parser") / entry_name
spec = importlib.util.spec_from_file_location("custom_parser", str(parser_path))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
manifest = mod.get_io_manifest()
print(f" Manifest declared: {manifest}")
output_dir = Path("/app/output")
if hasattr(mod, "run_pphrase"):
    mod.run_pphrase(output_dir, None)
""")

    # Dummy Dockerfile
    (folder / "Dockerfile").write_text("""
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
ARG DEPENDENCIES=""
RUN if [ ! -z "$DEPENDENCIES" ]; then pip install --no-cache-dir $DEPENDENCIES; fi
COPY entrypoint.py /app/
ENTRYPOINT ["python","entrypoint.py"]
""")

    # Images folder
    (folder / "images").mkdir()

    return folder

def test_compute_pphrase_hash_returns_hash(monkeypatch, mock_runner_folder, tmp_path):
    meta = json.loads((mock_runner_folder / "parser_meta.json").read_text())

    # Dummy utils
    utils_path = tmp_path / "utils"
    utils_path.mkdir()
    (utils_path / "dummy.py").write_text("print('utils')")

    # Dummy active_project
    active_project = tmp_path / "active_project"
    active_project.mkdir()
    for fname in ["noun_types.json", "verb_types.json", "adverb_types.json"]:
        (active_project / fname).write_text("{}")

    # Dummy verb group log
    verbs_dir = active_project / "verbs" / "Potency"
    verbs_dir.mkdir(parents=True)
    (verbs_dir / "Potency_log.jsonl").write_text("{}")

    # Mock subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout=""))

    h = pp.compute_pphrase_hash(meta, mock_runner_folder, tmp_path, active_project)
    assert isinstance(h, str)
    assert len(h) == 12

def test_validate_io_manifest_pphrase_valid(monkeypatch, tmp_path):
    project_path = tmp_path
    phrase_name = "coa_generator"

    manifest = {
        "Potency_Test": {"type": "verb"},
        "Terpene_Test": {"type": "verb"},
        "COA Name Map": {"type": "noun"},
        "Primary Aromas": {"type": "noun"},
        "Submission": {"type": "noun"},
    }

    # Setup allowed directories
    (project_path / "projects" / project_path.name).mkdir(parents=True, exist_ok=True)
    (project_path / "docker" / "Prepositional Phrases" / project_path.name / "inputs").mkdir(parents=True, exist_ok=True)
    (project_path / "projects" / project_path.name / "prepositional phrases" / phrase_name).mkdir(parents=True, exist_ok=True)

    pp.validate_io_manifest_pphrase(manifest, project_path, phrase_name)

def test_run_custom_prepositional_phrase(monkeypatch, tmp_path):
    import json
    from utils.handlers import prepositional_phrase as pp

    # Setup minimal project
    project_path = tmp_path / "project"
    project_path.mkdir()

    # Add minimal verb_types.json mapping required by load_full_verb_def
    verb_types_content = {
        "Potency": {
            "verb_group": "Potency",
            "data_entry_schema": {}
        },
        "Terpene": {
            "verb_group": "Terpene",
            "data_entry_schema": {}
        },
        "Potency_Test": {
            "verb_group": "Potency",
            "data_entry_schema": {}
        },
        "Terpene_Test": {
            "verb_group": "Terpene",
            "data_entry_schema": {}
        }
    }
    (project_path / "verb_types.json").write_text(json.dumps(verb_types_content))

    # DEBUG: Print verb_types.json path and contents
    print("DEBUG verb_types.json path:", project_path / "verb_types.json")
    print("DEBUG verb_types.json contents:", (project_path / "verb_types.json").read_text())

    # Create required *_types.json in project_path
    for fname in ["noun_types.json", "adverb_types.json"]:
        (project_path / fname).write_text("{}")

    active_project = tmp_path / "active_project"
    active_project.mkdir()
    for fname in ["noun_types.json", "verb_types.json", "adverb_types.json"]:
        (active_project / fname).write_text("{}")

    # Dummy nouns structure
    for noun in ["COA Name Map", "Primary Aromas", "Submission"]:
        noun_dir = project_path / "nouns" / noun
        noun_dir.mkdir(parents=True)
        (noun_dir / "items.jsonl").write_text("[]")

    # Dummy verb groups matching verb_group in verb_types.json
    for group in ["Potency", "Terpene"]:
        group_dir = project_path / "verbs" / group
        group_dir.mkdir(parents=True)
        (group_dir / "data_dumps").mkdir()
        (group_dir / f"{group}_log.jsonl").write_text("{}")

    # Setup runner folder
    runner_folder = tmp_path / "coa_generator"
    runner_folder.mkdir()

    parser_code = """
def get_metadata():
    return {"name": "coa_generator", "version": "0.1", "dependencies": ["python-docx"]}
def get_io_manifest():
    return {
        "Potency_Test": {"type": "verb"},
        "Terpene_Test": {"type": "verb"},
        "COA Name Map": {"type": "noun"},
        "Primary Aromas": {"type": "noun"},
        "Submission": {"type": "noun"},
    }
def run_pphrase(output_dir, payload):
    print("COA Generator ran.")
"""
    (runner_folder / "coa_generator.py").write_text(parser_code)

    # Dummy entrypoint.py
    (runner_folder / "entrypoint.py").write_text("""
import importlib.util
spec = importlib.util.spec_from_file_location("custom_parser", "coa_generator.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.run_pphrase(None, None)
""")

    # Dummy Dockerfile
    (runner_folder / "Dockerfile").write_text("""
FROM python:3.12-slim
WORKDIR /app
COPY entrypoint.py /app/
ENTRYPOINT ["python", "entrypoint.py"]
""")

    # DEBUG: Print runner folder structure
    print("DEBUG runner_folder contents:", list(runner_folder.iterdir()))

    # Monkeypatch subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout=""))
    # DOCKER_CLI constant was replaced by lazy config resolution; force docker for the test.
    monkeypatch.setattr(pp.config, "container_runtime_binary", lambda: "docker")
    # Post-cutover (R15): noun inputs are read from the unified store, not nouns/items.jsonl.
    import api.i_o as i_o_mod
    monkeypatch.setattr(i_o_mod, "get_noun_items", lambda pp_, noun: [{"id": f"{noun}-1"}])

    # Monkeypatch load_full_verb_def to print calls and return normal result
    import utils.handlers.verb as verb_handler

    original_load_full_verb_def = verb_handler.load_full_verb_def

    def debug_load_full_verb_def(project_path_arg, verb_key_arg):
        print("DEBUG load_full_verb_def called with:")
        print(" - project_path:", project_path_arg)
        print(" - verb_key:", verb_key_arg)
        result = original_load_full_verb_def(project_path_arg, verb_key_arg)
        print("DEBUG load_full_verb_def result:", result)
        return result

    monkeypatch.setattr(pp, "load_full_verb_def", debug_load_full_verb_def)

    # Run test
    success = pp.run_custom_prepositional_phrase(
        project_path=project_path,
        phrase_name="coa_generator",
        runner_folder=runner_folder,
        entrypoint="coa_generator.py",
        active_project=active_project
    )

    print("DEBUG run_custom_prepositional_phrase returned:", success)
    assert success
