"""Phase 6 / R15 — execution-backend + sandbox-hardening tests.

Covers the config accessors that drive container runtime selection, resource caps, and the
artifact-validation policy. Backend-selection, shared command-builder flag, artifact-broker,
and (opt-in) real-container tests are added alongside their commits.

These are pure unit tests — no container is launched here (see ``GIMS_RUN_CONTAINER_TESTS``
in the integration section for the opt-in real-container path).
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

import utils.config as config


# --------------------------------------------------------------------- runtime selection

def test_container_runtime_default_is_auto(monkeypatch):
    monkeypatch.delenv("GIMS_CONTAINER_RUNTIME", raising=False)
    assert config.container_runtime() == "auto"
    monkeypatch.setenv("GIMS_CONTAINER_RUNTIME", "Podman")
    assert config.container_runtime() == "podman"


def test_container_runtime_binary_auto_prefers_podman(monkeypatch):
    """auto: prefer rootless podman, fall back to docker."""
    monkeypatch.delenv("GIMS_CONTAINER_RUNTIME", raising=False)

    calls = []

    def fake_which(name):
        calls.append(name)
        return {"podman": "/usr/bin/podman", "docker": "/usr/bin/docker"}.get(name)

    monkeypatch.setattr(config.shutil, "which", fake_which)
    assert config.container_runtime_binary() == "/usr/bin/podman"
    assert calls[0] == "podman"  # podman probed first


def test_container_runtime_binary_auto_falls_back_to_docker(monkeypatch):
    monkeypatch.delenv("GIMS_CONTAINER_RUNTIME", raising=False)
    monkeypatch.setattr(config.shutil, "which", lambda n: "/usr/bin/docker" if n == "docker" else None)
    assert config.container_runtime_binary() == "/usr/bin/docker"


def test_container_runtime_binary_none_when_absent(monkeypatch):
    """Lazy + tolerant: no runtime present -> None (caller raises AppError, import never crashes)."""
    monkeypatch.delenv("GIMS_CONTAINER_RUNTIME", raising=False)
    monkeypatch.setattr(config.shutil, "which", lambda n: None)
    assert config.container_runtime_binary() is None


def test_container_runtime_binary_honours_explicit_choice(monkeypatch):
    monkeypatch.setenv("GIMS_CONTAINER_RUNTIME", "docker")
    monkeypatch.setattr(config.shutil, "which", lambda n: f"/usr/bin/{n}")
    assert config.container_runtime_binary() == "/usr/bin/docker"


# --------------------------------------------------------------------- resource caps

def test_resource_cap_defaults(monkeypatch):
    for var in ("GIMS_CONTAINER_NETWORK", "GIMS_CONTAINER_MEMORY", "GIMS_CONTAINER_CPUS",
                "GIMS_CONTAINER_PIDS", "GIMS_CONTAINER_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)
    assert config.container_network() == "none"
    assert config.container_memory_limit() == "2g"
    assert config.container_cpu_limit() == "2"
    assert config.container_pids_limit() == 256
    assert config.container_run_timeout() == 300


def test_resource_caps_are_env_overridable(monkeypatch):
    monkeypatch.setenv("GIMS_CONTAINER_MEMORY", "512m")
    monkeypatch.setenv("GIMS_CONTAINER_PIDS", "64")
    assert config.container_memory_limit() == "512m"
    assert config.container_pids_limit() == 64


def test_int_env_rejects_garbage_and_nonpositive(monkeypatch):
    monkeypatch.setenv("GIMS_CONTAINER_PIDS", "not-a-number")
    assert config.container_pids_limit() == 256  # falls back to default
    monkeypatch.setenv("GIMS_CONTAINER_PIDS", "0")
    assert config.container_pids_limit() == 256  # non-positive rejected
    monkeypatch.setenv("GIMS_CONTAINER_PIDS", "-5")
    assert config.container_pids_limit() == 256


# --------------------------------------------------------------------- artifact policy

def test_allowed_artifact_types_default_set(monkeypatch):
    monkeypatch.delenv("GIMS_ALLOWED_ARTIFACT_TYPES", raising=False)
    types = config.allowed_artifact_types()
    # owner-approved set, incl. html (writes reports in it) and docx (coa_generator output)
    assert {"csv", "json", "pdf", "docx", "xlsx", "png", "txt", "html"} == types


def test_allowed_artifact_types_override_normalises(monkeypatch):
    monkeypatch.setenv("GIMS_ALLOWED_ARTIFACT_TYPES", ".CSV, Pdf ,, .json")
    assert config.allowed_artifact_types() == {"csv", "pdf", "json"}


def test_artifact_caps_defaults(monkeypatch):
    monkeypatch.delenv("GIMS_ARTIFACT_MAX_BYTES", raising=False)
    monkeypatch.delenv("GIMS_ARTIFACT_MAX_COUNT", raising=False)
    assert config.artifact_max_bytes() == 100 * 1024 * 1024
    assert config.artifact_max_count() == 500


def test_inprocess_tools_gated_off_by_default(monkeypatch):
    monkeypatch.delenv("GIMS_ALLOW_INPROCESS_TOOLS", raising=False)
    assert config.allow_inprocess_tools() is False
    monkeypatch.setenv("GIMS_ALLOW_INPROCESS_TOOLS", "true")
    assert config.allow_inprocess_tools() is True


# --------------------------------------------------------------------- container_run builder

from utils import container_run  # noqa: E402
from core.errors import AppError  # noqa: E402


def test_runtime_binary_or_raise_raises_when_absent(monkeypatch):
    monkeypatch.setattr(config, "container_runtime_binary", lambda: None)
    with pytest.raises(AppError) as ei:
        container_run.runtime_binary_or_raise()
    assert ei.value.code == "CONTAINER_RUNTIME_NOT_FOUND"
    assert ei.value.status == 503


def test_runtime_binary_or_raise_returns_path(monkeypatch):
    monkeypatch.setattr(config, "container_runtime_binary", lambda: "/usr/bin/podman")
    assert container_run.runtime_binary_or_raise() == "/usr/bin/podman"


def test_hardening_flags_present_for_docker(monkeypatch):
    monkeypatch.delenv("GIMS_CONTAINER_NETWORK", raising=False)
    flags = " ".join(container_run.hardening_flags("/usr/bin/docker"))
    for f in ("--rm", "--network=none", "--cap-drop=ALL", "--security-opt=no-new-privileges",
              "--pids-limit=", "--memory=", "--cpus=", "--read-only", "--tmpfs=/tmp"):
        assert f in flags, f"missing {f!r}"
    # docker runs as the host uid:gid (non-root)
    assert "--user=" in flags


def test_hardening_flags_tmpfs_has_size_cap(monkeypatch):
    monkeypatch.setenv("GIMS_CONTAINER_TMPFS_SIZE", "256m")
    flags = " ".join(container_run.hardening_flags("/usr/bin/docker"))
    assert "--tmpfs=/tmp:rw,nosuid,nodev,mode=1777,size=256m" in flags


def test_hardening_flags_podman_uses_keep_id():
    flags = container_run.hardening_flags("/usr/bin/podman")
    assert "--userns=keep-id" in flags
    assert not any(x.startswith("--user=") for x in flags)  # keep-id handles the user


def test_hardening_never_runs_as_root_user_flag(monkeypatch):
    """If the server itself runs as root, the container must still drop to nobody, never uid 0."""
    monkeypatch.setattr(container_run.os, "getuid", lambda: 0, raising=False)
    monkeypatch.setattr(container_run.os, "getgid", lambda: 0, raising=False)
    flags = container_run.hardening_flags("/usr/bin/docker")
    assert "--user=65534:65534" in flags


def test_build_hardened_run_cmd_orders_image_last_and_mounts(monkeypatch):
    monkeypatch.setattr(config, "container_network", lambda: "none")
    cmd = container_run.build_hardened_run_cmd(
        runtime_binary="/usr/bin/docker",
        image="img:tag",
        mounts=[container_run.Mount("/h/in", "/app/inputs/x", "ro"),
                container_run.Mount("/h/out", "/app/output", "rw")],
        env={"FOO": "bar"},
        workdir="/app/output",
    )
    assert cmd[0] == "/usr/bin/docker" and cmd[1] == "run"
    assert cmd[-1] == "img:tag"
    joined = " ".join(cmd)
    assert "/h/in:/app/inputs/x:ro" in joined      # ro suffix
    assert "/h/out:/app/output" in joined and "/h/out:/app/output:ro" not in joined  # rw, no :ro
    assert "-e FOO=bar" in joined
    assert "--workdir /app/output" in joined
    # safe defaults injected
    assert "PYTHONDONTWRITEBYTECODE=1" in joined


def test_run_container_translates_missing_runtime(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("no such binary")
    monkeypatch.setattr(container_run.subprocess, "run", boom)
    with pytest.raises(AppError) as ei:
        container_run.run_container(["/nope", "run", "img"])
    assert ei.value.code == "CONTAINER_RUNTIME_NOT_FOUND"


def test_run_container_translates_timeout(monkeypatch):
    import subprocess as _sp

    def boom(*a, **k):
        raise _sp.TimeoutExpired(cmd="x", timeout=1)
    monkeypatch.setattr(container_run.subprocess, "run", boom)
    with pytest.raises(AppError) as ei:
        container_run.run_container(["docker", "run", "img"], timeout=1)
    assert ei.value.code == "CONTAINER_RUN_TIMEOUT"
    assert ei.value.status == 504


# --------------------------------------------------------------------- regression: noun input (R15 / post-cutover)

def test_pphrase_noun_input_reads_unified_store_not_nouns_folder(tmp_path, monkeypatch):
    """Post-cutover regression: a prepositional-phrase noun input must be read from the unified
    `instances` store (via i_o.get_noun_items) and materialised to a temp items.jsonl — NOT from
    the retired projects/<p>/nouns/<noun>/items.jsonl path (which raised RuntimeError for every
    noun input after the Phase-5 cutover)."""
    from utils.handlers import prepositional_phrase as pp
    import api.i_o as i_o_mod

    project_path = tmp_path / "project"
    project_path.mkdir()
    # Deliberately NO nouns/ folder — proves the code never depends on it.
    for fname in ("noun_types.json", "verb_types.json", "adverb_types.json"):
        (project_path / fname).write_text("{}")

    runner_folder = tmp_path / "phrase"
    runner_folder.mkdir()
    (runner_folder / "phrase.py").write_text(
        'def get_metadata(): return {"name": "p", "version": "1"}\n'
        'def get_io_manifest(): return {"Submission": {"type": "noun"}}\n'
        'def run(ctx=None): pass\n'
    )

    rows = [{"id": "S1", "x": 1}, {"id": "S2", "x": 2}]
    monkeypatch.setattr(i_o_mod, "get_noun_items", lambda pp_, noun: rows)

    captured = {}

    def fake_container(**kwargs):
        # read the materialised file while it still exists (cleanup runs in the finally after this)
        mi = Path(kwargs["mounted_inputs"]["Submission"])
        captured["name"] = mi.name
        captured["text"] = mi.read_text(encoding="utf-8")
        captured["parent"] = mi.parent
        return True

    monkeypatch.setattr(pp, "run_prepositional_phrase_container", fake_container)

    ok = pp.run_custom_prepositional_phrase(
        project_path=project_path, phrase_name="p",
        runner_folder=runner_folder, entrypoint="phrase.py",
        active_project=project_path,
    )

    assert ok is True
    assert captured["name"] == "items.jsonl"  # preserves the container's items.jsonl contract
    lines = [ln for ln in captured["text"].splitlines() if ln.strip()]
    assert [json.loads(ln) for ln in lines] == rows  # proper JSONL of the store's rows
    assert not (project_path / "nouns").exists()      # retired path never created/touched
    assert not captured["parent"].exists()            # temp dir cleaned up in finally


def test_pphrase_noun_input_empty_store_mounts_empty_file(tmp_path, monkeypatch):
    """An empty noun (no instances yet) must mount an empty items.jsonl, not hard-error."""
    from utils.handlers import prepositional_phrase as pp
    import api.i_o as i_o_mod

    project_path = tmp_path / "project"
    project_path.mkdir()
    for fname in ("noun_types.json", "verb_types.json", "adverb_types.json"):
        (project_path / fname).write_text("{}")
    runner_folder = tmp_path / "phrase"
    runner_folder.mkdir()
    (runner_folder / "phrase.py").write_text(
        'def get_metadata(): return {"name": "p", "version": "1"}\n'
        'def get_io_manifest(): return {"Sample": {"type": "noun"}}\n'
        'def run(ctx=None): pass\n'
    )
    monkeypatch.setattr(i_o_mod, "get_noun_items", lambda pp_, noun: [])

    captured = {}

    def fake_container(**kwargs):
        mi = Path(kwargs["mounted_inputs"]["Sample"])
        captured["text"] = mi.read_text(encoding="utf-8")
        return True

    monkeypatch.setattr(pp, "run_prepositional_phrase_container", fake_container)
    ok = pp.run_custom_prepositional_phrase(
        project_path=project_path, phrase_name="p",
        runner_folder=runner_folder, entrypoint="phrase.py", active_project=project_path,
    )
    assert ok is True
    assert captured["text"] == ""  # empty JSONL, no exception


# --------------------------------------------------------------------- artifact broker (host gateway)

from utils import artifact_broker  # noqa: E402


def _mk(src: Path, rel: str, data: bytes) -> Path:
    p = src / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_broker_commits_valid_csv_and_strips_exec_bit(tmp_path):
    src = tmp_path / "out"; src.mkdir()
    f = _mk(src, "Results.csv", b"a,b\n1,2\n")
    import os as _os
    _os.chmod(f, 0o777)  # tool made it executable
    dst = tmp_path / "dest"
    rep = artifact_broker.collect_artifacts(src, dst)
    assert rep.ok
    out = dst / "Results.csv"
    assert out.read_bytes() == b"a,b\n1,2\n"
    assert (out.stat().st_mode & 0o111) == 0  # no exec bits


def test_broker_allows_html(tmp_path):
    src = tmp_path / "out"; src.mkdir()
    _mk(src, "report.html", b"<!DOCTYPE html><html><body>hi</body></html>")
    dst = tmp_path / "dest"
    rep = artifact_broker.collect_artifacts(src, dst)
    assert rep.ok and (dst / "report.html").exists()


def test_broker_rejects_disallowed_extension(tmp_path):
    src = tmp_path / "out"; src.mkdir()
    _mk(src, "evil.sh", b"#!/bin/sh\nrm -rf /\n")
    dst = tmp_path / "dest"
    rep = artifact_broker.collect_artifacts(src, dst)
    assert not rep.ok
    assert any("extension" in r["reason"] for r in rep.rejected)
    assert not (dst / "evil.sh").exists()


def test_broker_rejects_symlink_escape(tmp_path):
    src = tmp_path / "out"; src.mkdir()
    secret = tmp_path / "secret.txt"; secret.write_text("top secret")
    link = src / "leak.txt"
    link.symlink_to(secret)  # symlink escaping the src root
    dst = tmp_path / "dest"
    rep = artifact_broker.collect_artifacts(src, dst)
    assert not rep.ok
    assert any("symlink" in r["reason"] for r in rep.rejected)
    assert not (dst / "leak.txt").exists()


def test_broker_rejects_oversized(tmp_path, monkeypatch):
    monkeypatch.setenv("GIMS_ARTIFACT_MAX_BYTES", "10")
    src = tmp_path / "out"; src.mkdir()
    _mk(src, "big.csv", b"x" * 100)
    dst = tmp_path / "dest"
    rep = artifact_broker.collect_artifacts(src, dst)
    assert not rep.ok and any("size cap" in r["reason"] for r in rep.rejected)


def test_broker_rejects_wrong_magic_pdf(tmp_path):
    src = tmp_path / "out"; src.mkdir()
    _mk(src, "fake.pdf", b"this is not a pdf")
    dst = tmp_path / "dest"
    rep = artifact_broker.collect_artifacts(src, dst)
    assert not rep.ok and any("does not match" in r["reason"] for r in rep.rejected)


def test_broker_accepts_real_png_and_pdf(tmp_path):
    src = tmp_path / "out"; src.mkdir()
    _mk(src, "img.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    _mk(src, "doc.pdf", b"%PDF-1.7\n%rest")
    dst = tmp_path / "dest"
    rep = artifact_broker.collect_artifacts(src, dst)
    assert rep.ok and (dst / "img.png").exists() and (dst / "doc.pdf").exists()


def test_broker_rejects_executable_disguised_as_csv(tmp_path):
    src = tmp_path / "out"; src.mkdir()
    _mk(src, "Results.csv", b"\x7fELF\x02\x01\x01" + b"\x00" * 20)
    dst = tmp_path / "dest"
    rep = artifact_broker.collect_artifacts(src, dst)
    assert not rep.ok and any("executable" in r["reason"] for r in rep.rejected)


def test_broker_enforces_count_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("GIMS_ARTIFACT_MAX_COUNT", "2")
    src = tmp_path / "out"; src.mkdir()
    for i in range(5):
        _mk(src, f"r{i}.csv", b"a,b\n")
    dst = tmp_path / "dest"
    rep = artifact_broker.collect_artifacts(src, dst)
    assert len(rep.committed) == 2
    assert any("count cap" in r["reason"] for r in rep.rejected)


# --------------------------------------------------------------------- execution backends

from core.orchestration import execution_backend as eb  # noqa: E402


def test_select_backend_defaults_to_container(monkeypatch):
    assert callable(eb.select_backend(None))
    assert callable(eb.select_backend("container"))
    # native maps to the (gated) in-process backend function itself
    assert eb.select_backend("native") is eb.inprocess_backend
    assert eb.select_backend("inprocess") is eb.inprocess_backend


def test_select_backend_unknown_mode_raises():
    with pytest.raises(AppError) as ei:
        eb.select_backend("rocket")
    assert ei.value.code == "UNKNOWN_EXEC_MODE"


def test_inprocess_backend_refused_unless_allowed(monkeypatch):
    monkeypatch.setattr(eb.config, "allow_inprocess_tools", lambda: False)
    called = {"n": 0}
    with pytest.raises(AppError) as ei:
        eb.inprocess_backend(lambda: called.__setitem__("n", called["n"] + 1), {"kind": "parser", "ctx": None})
    assert ei.value.code == "INPROCESS_TOOLS_DISABLED" and ei.value.status == 403
    assert called["n"] == 0  # tool was NOT run


def test_inprocess_backend_runs_when_allowed(monkeypatch):
    monkeypatch.setattr(eb.config, "allow_inprocess_tools", lambda: True)
    ran = {"n": 0}
    res = eb.inprocess_backend(lambda: ran.__setitem__("n", 1), {"kind": "parser", "ctx": None})
    assert res["ok"] is True and ran["n"] == 1


def test_extract_dependencies_from_various_forms(tmp_path):
    t1 = tmp_path / "a.py"; t1.write_text('DEPENDENCIES = ["pandas", "numpy"]\ndef run(c): pass\n')
    assert eb.extract_dependencies(t1) == ["pandas", "numpy"]
    t2 = tmp_path / "b.py"; t2.write_text('TOOL = {"kind": "parser", "dependencies": ["python-docx"]}\n')
    assert eb.extract_dependencies(t2) == ["python-docx"]
    t3 = tmp_path / "c.py"; t3.write_text('import os, csv\ndef run(c): pass\n')
    assert eb.extract_dependencies(t3) == []


def test_extract_dependencies_does_not_execute_tool(tmp_path):
    """AST-only: a tool with a side effect at import time must NOT run during dep extraction."""
    marker = tmp_path / "ran.flag"
    t = tmp_path / "evil.py"
    t.write_text(f'open({str(marker)!r}, "w").write("x")\nDEPENDENCIES = ["x"]\n')
    eb.extract_dependencies(t)
    assert not marker.exists()  # never executed


def test_stage_inputs_copies_files_and_rewrites_paths(tmp_path):
    staging = tmp_path / "in"; staging.mkdir()
    src = tmp_path / "DataEntry.json"; src.write_text("{}")
    out = eb._stage_inputs({"data": str(src), "missing": "/nope/x", "n": 5}, staging)
    assert out["data"] == "/app/inputs/DataEntry.json"
    assert (staging / "DataEntry.json").read_text() == "{}"
    assert out["missing"] == "/nope/x"  # non-file passthrough
    assert out["n"] == 5


def test_plan_outputs_parser_and_pphrase():
    s, plan = eb._plan_outputs({"Results.csv": "/proj/run/Results.csv"}, "parser")
    assert s["Results.csv"] == "/app/output/Results.csv"
    assert plan["Results.csv"] == "/proj/run/Results.csv"
    s2, plan2 = eb._plan_outputs({"OUTPUT_FOLDER": "/proj/scratch"}, "pphrase")
    assert s2["OUTPUT_FOLDER"] == "/app/output"
    assert plan2["__folder__"] == "/proj/scratch"


def _out_host_from_cmd(cmd):
    for i, a in enumerate(cmd):
        if a == "-v" and cmd[i + 1].endswith(":/app/output"):
            return cmd[i + 1].rsplit(":", 1)[0]
    return None


def test_container_executor_runs_and_brokers_parser(tmp_path, monkeypatch):
    tool = tmp_path / "tool.py"; tool.write_text("def run(ctx):\n    pass\n")
    work = tmp_path / "work"; work.mkdir()
    real_out = tmp_path / "dest" / "Results.csv"

    class Ctx:
        inputs = {}
        outputs = {"Results.csv": str(real_out)}
        params = {}

    env = {"kind": "parser", "ctx": Ctx(), "tool_module_path": str(tool), "work_dir": str(work)}
    monkeypatch.setattr(eb.container_run, "runtime_binary_or_raise", lambda: "/usr/bin/podman")
    monkeypatch.setattr(eb, "_ensure_image", lambda deps, rt: "img:test")

    def fake_run(cmd, *, timeout=None, capture=False):
        out_host = _out_host_from_cmd(cmd)
        assert out_host, f"no /app/output mount in {cmd}"
        (Path(out_host) / "Results.csv").write_text("a,b\n1,2\n")
        import subprocess as sp
        return sp.CompletedProcess(cmd, 0, stdout=eb._RESULT_MARKER + '{"ok": true}\n', stderr="")

    monkeypatch.setattr(eb.container_run, "run_container", fake_run)
    res = eb.make_container_backend()(None, env)
    assert res["ok"] is True
    assert str(real_out) in res["produced"]
    assert real_out.read_text() == "a,b\n1,2\n"


def test_container_executor_drops_disallowed_output(tmp_path, monkeypatch):
    tool = tmp_path / "tool.py"; tool.write_text("def run(ctx): pass\n")
    work = tmp_path / "work"; work.mkdir()

    class Ctx:
        inputs = {}
        outputs = {"OUTPUT_FOLDER": str(tmp_path / "scratch")}
        params = {}

    env = {"kind": "pphrase", "ctx": Ctx(), "tool_module_path": str(tool), "work_dir": str(work)}
    monkeypatch.setattr(eb.container_run, "runtime_binary_or_raise", lambda: "/usr/bin/podman")
    monkeypatch.setattr(eb, "_ensure_image", lambda deps, rt: "img:test")

    def fake_run(cmd, *, timeout=None, capture=False):
        out_host = Path(_out_host_from_cmd(cmd))
        (out_host / "report.html").write_text("<html>ok</html>")     # allowed
        (out_host / "evil.sh").write_text("#!/bin/sh\necho hi\n")     # disallowed ext
        import subprocess as sp
        return sp.CompletedProcess(cmd, 0, stdout=eb._RESULT_MARKER + '{"ok": true}', stderr="")

    monkeypatch.setattr(eb.container_run, "run_container", fake_run)
    res = eb.make_container_backend()(None, env)
    assert res["ok"] is True
    scratch = tmp_path / "scratch"
    assert (scratch / "report.html").exists()
    assert not (scratch / "evil.sh").exists()
    assert res.get("rejected_artifacts")


def test_container_executor_propagates_tool_error(tmp_path, monkeypatch):
    tool = tmp_path / "tool.py"; tool.write_text("def run(ctx): pass\n")
    work = tmp_path / "work"; work.mkdir()

    class Ctx:
        inputs = {}; outputs = {}; params = {}

    env = {"kind": "parser", "ctx": Ctx(), "tool_module_path": str(tool), "work_dir": str(work)}
    monkeypatch.setattr(eb.container_run, "runtime_binary_or_raise", lambda: "/usr/bin/podman")
    monkeypatch.setattr(eb, "_ensure_image", lambda deps, rt: "img:test")

    def fake_run(cmd, *, timeout=None, capture=False):
        import subprocess as sp
        return sp.CompletedProcess(cmd, 0, stdout=eb._RESULT_MARKER + '{"ok": false, "error": "boom"}', stderr="trace")

    monkeypatch.setattr(eb.container_run, "run_container", fake_run)
    res = eb.make_container_backend()(None, env)
    assert res["ok"] is False and "boom" in res["error"]


# --------------------------------------------------------------------- opt-in REAL-container integration
# Skipped by default (keeps the suite green + fast). Run with:
#   GIMS_RUN_CONTAINER_TESTS=1 .venv/bin/python -m pytest tests/test_r15_execution.py -k integration
# Requires a working podman/docker + the base image. Proves the ContainerBackend actually launches a
# hardened container, brokers artifacts, and enforces non-root / read-only rootfs / no-network.

_RUN_CONTAINER = os.environ.get("GIMS_RUN_CONTAINER_TESTS") == "1"
_HAVE_RUNTIME = config.container_runtime_binary() is not None
_pytestmark_reason = "set GIMS_RUN_CONTAINER_TESTS=1 (and have podman/docker) to run real-container tests"


@pytest.mark.skipif(not (_RUN_CONTAINER and _HAVE_RUNTIME), reason=_pytestmark_reason)
def test_integration_container_parser_produces_brokered_csv(tmp_path):
    tool = tmp_path / "parser.py"
    tool.write_text(
        "import json, csv\n"
        "def run(ctx):\n"
        "    rows = json.load(open(ctx.inputs['data']))\n"
        "    with open(ctx.outputs['Results.csv'],'w',newline='') as f:\n"
        "        w=csv.writer(f); w.writerow(['id','val'])\n"
        "        [w.writerow([r['id'],r['val']]) for r in rows]\n"
    )
    data = tmp_path / "data.json"; data.write_text(json.dumps([{"id": "A", "val": 1}, {"id": "B", "val": 2}]))
    dest = tmp_path / "dest" / "Results.csv"
    work = tmp_path / "work"; work.mkdir()

    class Ctx:
        inputs = {"data": str(data)}
        outputs = {"Results.csv": str(dest)}
        params = {}

    env = {"kind": "parser", "tool_module_path": str(tool), "work_dir": str(work), "ctx": Ctx()}
    res = eb.make_container_backend()(None, env)
    assert res["ok"], res
    assert dest.exists() and "A,1" in dest.read_text()
    assert (dest.stat().st_mode & 0o111) == 0  # broker stripped exec bits


@pytest.mark.skipif(not (_RUN_CONTAINER and _HAVE_RUNTIME), reason=_pytestmark_reason)
def test_integration_container_hardening_and_whitelist(tmp_path):
    # pphrase tool: write an allowed html + a disallowed .sh, and record uid/rootfs/network probes.
    tool = tmp_path / "phrase.py"
    tool.write_text(
        "import os, socket\n"
        "def run(ctx):\n"
        "    out = ctx.outputs['OUTPUT_FOLDER']\n"
        "    open(os.path.join(out,'report.html'),'w').write('<html>ok</html>')\n"
        "    open(os.path.join(out,'evil.sh'),'w').write('#!/bin/sh\\n')\n"
        "    probe=['uid=%d'%os.getuid()]\n"
        "    try:\n"
        "        open('/etc/escaped','w').write('x'); probe.append('rootfs=WRITABLE')\n"
        "    except Exception as e: probe.append('rootfs=blocked')\n"
        "    try:\n"
        "        socket.create_connection(('1.1.1.1',53),timeout=3); probe.append('net=REACHABLE')\n"
        "    except Exception as e: probe.append('net=blocked')\n"
        "    open(os.path.join(out,'probe.txt'),'w').write(' '.join(probe))\n"
    )
    scratch = tmp_path / "scratch"
    work = tmp_path / "work"; work.mkdir()

    class Ctx:
        inputs = {}
        outputs = {"OUTPUT_FOLDER": str(scratch)}
        params = {}

    env = {"kind": "pphrase", "tool_module_path": str(tool), "work_dir": str(work), "ctx": Ctx()}
    res = eb.make_container_backend()(None, env)
    assert res["ok"], res
    assert (scratch / "report.html").exists()       # allowed type committed
    assert not (scratch / "evil.sh").exists()        # disallowed type dropped by broker
    probe = (scratch / "probe.txt").read_text()
    assert "uid=0" not in probe                       # non-root
    assert "rootfs=blocked" in probe                  # read-only rootfs
    assert "net=blocked" in probe                     # --network=none
