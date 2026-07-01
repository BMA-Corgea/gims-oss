# tests/test_runner_env.py

import pytest
import json
import subprocess
from pathlib import Path
from utils import runner_env
from utils.file_ops import _default_dockerfile, _default_entrypoint_py

@pytest.fixture
def mock_runner_folder(tmp_path):
    folder = tmp_path / "parser"
    folder.mkdir()

    # Create dummy parser_meta.json
    meta = {
        "name": "testparser",
        "version": "0.1",
        "dependencies": ["pandas", "numpy"]
    }
    (folder / "parser_meta.json").write_text(json.dumps(meta))

    # Create dummy entrypoint.py
    (folder / "entrypoint.py").write_text(_default_entrypoint_py())

    # Create dummy parser script required by load_runner_metadata
    parser_code = """
def get_metadata():
    return {"name": "testparser", "version": "0.1", "verb": "testverb"}
def get_io_manifest():
    return {"data_entry": {"mode": "read"}}
def run_parser(inputs, output_dir):
    print("Dummy parser ran.")
"""
    (folder / "dummy_parser.py").write_text(parser_code)

    # Create dummy Dockerfile
    (folder / "Dockerfile").write_text(_default_dockerfile())

    # Images folder
    (folder / "images").mkdir()

    return folder

def test_compute_runner_hash_creates_expected_hash(mock_runner_folder):
    meta = json.loads((mock_runner_folder / "parser_meta.json").read_text())
    hash_val = runner_env.compute_runner_hash(meta, mock_runner_folder)

    assert isinstance(hash_val, str)
    assert len(hash_val) == 12

def test_image_exists_detects_tar(mock_runner_folder):
    hash_val = "abc123456789"
    tar_path = mock_runner_folder / "images" / f"{hash_val}.tar"
    tar_path.write_text("dummy tar contents")

    exists = runner_env.image_exists(hash_val, mock_runner_folder)
    assert exists

def test_image_exists_returns_false_when_missing(mock_runner_folder):
    hash_val = "nonexistent"
    assert not runner_env.image_exists(hash_val, mock_runner_folder)

def test_load_runner_metadata_success(mock_runner_folder):
    meta = runner_env.load_runner_metadata(mock_runner_folder)
    assert isinstance(meta, dict)
    assert meta["name"] == "testparser"

def test_build_runner_image_invokes_subprocess(monkeypatch, mock_runner_folder):
    meta = json.loads((mock_runner_folder / "parser_meta.json").read_text())

    called = {}

    def mock_subprocess_run(cmd, check=None, capture_output=False, text=False):
        called["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(runner_env, "subprocess", subprocess)
    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    image_tag = runner_env.build_runner_image(meta, mock_runner_folder)
    assert image_tag.startswith("gims_runner_testparser:")
    assert "build" in " ".join(called["cmd"])

def test_run_parser_container_basic(monkeypatch, mock_runner_folder, tmp_path):
    meta = json.loads((mock_runner_folder / "parser_meta.json").read_text())

    # Mock subprocess.run to always succeed and return dummy stdout.
    # NB: run_parser_container now routes the actual run through container_run.run_container,
    # which passes a `timeout=` kwarg — so the mock must accept it (and any future kwargs).
    captured = {}

    def mock_subprocess_run(cmd, check=None, capture_output=False, text=False, timeout=None, **kwargs):
        if "run" in cmd:
            captured["run_cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    manifest = {
        "data_entry": {"mode": "read"}
    }
    mounted_inputs = {
        "data_entry": tmp_path
    }

    tmp_file = tmp_path / "DataEntry.json"
    tmp_file.write_text("{}")

    success = runner_env.run_parser_container(
        meta=meta,
        verb_config={},
        run_id="run001",
        runner_folder=mock_runner_folder,
        entrypoint="entrypoint.py",
        manifest=manifest,
        mounted_inputs=mounted_inputs
    )
    assert success

    # R15: the actual run command must carry the hardening flag-set.
    run_cmd = " ".join(captured["run_cmd"])
    for flag in ("--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only",
                 "--pids-limit=", "--memory=", "--cpus=", "--network=none"):
        assert flag in run_cmd, f"missing hardening flag {flag!r} in: {run_cmd}"

def test_validate_io_manifest_accepts_correct_manifest():
    manifest = {
        "data_entry": {"mode": "read"}
    }
    expected_paths = {
        "data_entry": {"path": Path("/some/path"), "must_be": "read"}
    }

    runner_env.validate_io_manifest(manifest, expected_paths)

def test_validate_io_manifest_raises_on_wrong_mode():
    manifest = {
        "data_entry": {"mode": "write"}
    }
    expected_paths = {
        "data_entry": {"path": Path("/some/path"), "must_be": "read"}
    }

    with pytest.raises(RuntimeError):
        runner_env.validate_io_manifest(manifest, expected_paths)

def test_run_custom_parser(monkeypatch, tmp_path):
    # Setup minimal project structure
    project_path = tmp_path / "project"
    project_path.mkdir()

    verb_types = {
        "testverb": {
            "verb_group": "Tests",
            "data_entry_schema": {
                "raw_data_inputs": {},
                "interpretation": {
                    "tabs": []
                }
            }
        }
    }
    (project_path / "verb_types.json").write_text(json.dumps(verb_types))

    run_id = "run001"
    dump_root = project_path / "verbs" / "Tests" / "data_dumps" / run_id
    dump_root.mkdir(parents=True)

    # Setup parser folder
    runner_folder = tmp_path / "parser"
    runner_folder.mkdir()

    parser_code = """
def get_metadata():
    return {"name": "testparser", "version": "0.1", "verb": "testverb"}
def get_io_manifest():
    return {"data_entry": {"mode": "read"}}
def run_parser(inputs, output_dir):
    print("Parser ran successfully.")
"""
    parser_file = runner_folder / "test_parser.py"
    parser_file.write_text(parser_code)

    # Create required entrypoint.py and Dockerfile
    (runner_folder / "entrypoint.py").write_text(_default_entrypoint_py())
    (runner_folder / "Dockerfile").write_text(_default_dockerfile())

    (dump_root / "DataEntry.json").write_text("{}")

    # Mock subprocess.run to always succeed and return dummy stdout (accept the new timeout kwarg).
    def mock_subprocess_run(cmd, check=None, capture_output=False, text=False, timeout=None, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)
    # Force docker so the test doesn't depend on which runtime is installed (DOCKER_CLI constant
    # was replaced by lazy config resolution).
    monkeypatch.setattr(runner_env.config, "container_runtime_binary", lambda: "docker")

    success = runner_env.run_custom_parser(
        project_path=project_path,
        run_id=run_id,
        runner_folder=runner_folder,
        entrypoint="test_parser.py"
    )
    assert success
