"""Behaviour-golden harness for ``core.run_custom.runner.run_custom_tool``.

The live custom-tool orchestrator has NO standard-suite coverage — ``test_run_customs_gui``
mocks ``run_custom_tool`` outright and ``test_r15_execution`` launches no container — so the
426-line orchestration body (context-required gate, pphrase run-id collection + injection, mount
resolution, raw-folder pre-digest, the per-tool execution context, backend dispatch, the pphrase
scratch->canonical sync, and the AppError-vs-generic error contract) was unpinned.

This harness drives the PUBLIC ``run_custom_tool`` with injected fakes — a fake executor,
layout_resolver, backend, and tool module — so every orchestration branch is exercised WITHOUT a
real container. It is the safety net for the Phase 4 ExecutionService extraction: the public
contract pinned here must not move when the monolith is restructured. (The container/wasm/in-proc
backends themselves are unchanged by that refactor; they are reached only through the injected
``backend`` callable, whose contract — ``backend(entry, env) -> result`` — is exercised here.)
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from core.run_custom.runner import run_custom_tool
from core.run_custom.schema import IoSpec, ExecutableBase
from core.run_custom._types import ContextError
from core.errors import AppError


# ── fakes ────────────────────────────────────────────────────────────────────────────────────
class FakeExecutor(ExecutableBase):
    """Executor whose validate is a no-op and whose logical mount plan is supplied verbatim."""

    def __init__(self, logical):
        self._logical = logical

    def validate(self, spec, schema, **kwargs):  # skip shape/schema checks; tested elsewhere
        return None

    def resolve_mounts(self, spec, schema, **kwargs):
        return self._logical


def make_tool_module(tmp_path, *, prephrase_settings=None):
    """A stand-in user tool module: ``run(ctx)`` drops a marker file into each output dir/file."""
    mod = types.ModuleType("fake_tool")
    mod.__file__ = str(tmp_path / "fake_tool.py")
    Path(mod.__file__).write_text("# fake tool\n")

    def run(ctx):
        for alias, out in ctx.outputs.items():
            p = Path(out)
            if p.suffix:  # a file output
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"out:{alias}")
            else:  # a folder output (pphrase OUTPUT_FOLDER scratch)
                p.mkdir(parents=True, exist_ok=True)
                (p / "result.txt").write_text(f"out:{alias}")

    mod.run = run
    if prephrase_settings is not None:
        mod.PREPHRASE_SETTINGS = prephrase_settings
    return mod


def recording_backend(captured, *, result=None, raises=None):
    """A backend matching the ``backend(entry, env) -> dict`` contract. Records env, runs the
    in-process entry (so the tool's run() fires and writes outputs), then returns ``result``."""
    def backend(entry, env):
        captured["env"] = env
        if raises is not None:
            raise raises
        entry()  # fire tool_module.run(ctx) — the in-process callback the runner builds
        return result if result is not None else {"ok": True, "logs": ["ran"]}
    return backend


# ── 1) parser happy path ───────────────────────────────────────────────────────────────────────
def test_parser_happy_path_predigests_assembles_ctx_and_dispatches(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "data.csv").write_text("a,b\n1,2\n")  # exactly one file (raw-folder policy)
    cfg = tmp_path / "cfg.json"
    cfg.write_text("{}")
    out_file = tmp_path / "out" / "Result.csv"

    logical = {
        "inputs": {
            "RAW": {"slot": {"kind": "raw_folder"}, "mode": "ro"},
            "CFG": {"slot": {"kind": "file"}, "mode": "ro"},
        },
        "outputs": {"OUT": {"slot": {"kind": "file"}, "mode": "rw"}},
    }

    def layout_resolver(_logical):
        return {
            "inputs": {"RAW": {"paths": [raw_dir]}, "CFG": {"path": cfg}},
            "outputs": {"OUT": {"path": out_file}},
        }

    captured = {}
    mod = make_tool_module(tmp_path)
    spec = IoSpec(kind="parser", raw_folders=["RAW"], file_inputs=["CFG"],
                  outputs={"files": ["Result.csv"]})

    result = run_custom_tool(
        tool_module=mod, iospec=spec, verb_schema={}, db_map=None,
        context={"verb_group": "G", "run_id": "R1", "params": {"k": "v"}},
        layout_resolver=layout_resolver, work_dir=tmp_path,
        executor=FakeExecutor(logical), backend=recording_backend(captured),
    )

    assert result["ok"] is True
    assert result["logs"] == ["ran"]
    assert result["produced"] == [str(out_file)]
    # the tool actually ran in-process and wrote the output
    assert out_file.read_text() == "out:OUT"
    # raw folder was pre-digested into work_dir/predigest/<alias>/<file>
    digested = tmp_path / "predigest" / "RAW" / "data.csv"
    assert digested.exists() and digested.read_text() == "a,b\n1,2\n"
    # the execution env the backend received
    env = captured["env"]
    assert env["kind"] == "parser"
    assert env["work_dir"] == str(tmp_path)
    assert env["tool_module_path"] == mod.__file__
    # per-tool context: raw input points at the digested file, file input passes through
    assert env["ctx"].inputs["RAW"] == str(digested)
    assert env["ctx"].inputs["CFG"] == str(cfg)
    assert env["ctx"].outputs["OUT"] == str(out_file)
    assert env["ctx"].params == {"k": "v"}


def test_parser_requires_verb_group_and_run_id(tmp_path):
    spec = IoSpec(kind="parser", outputs={"files": ["x.csv"]})
    with pytest.raises(ContextError):
        run_custom_tool(
            tool_module=make_tool_module(tmp_path), iospec=spec, verb_schema={}, db_map=None,
            context={"params": {}},  # no verb_group / run_id
            layout_resolver=lambda lg: {"inputs": {}, "outputs": {}},
            work_dir=tmp_path, executor=FakeExecutor({"inputs": {}, "outputs": {}}),
            backend=recording_backend({}),
        )


def test_raw_folder_with_not_exactly_one_file_is_rejected(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "a.csv").write_text("x")
    (raw_dir / "b.csv").write_text("y")  # two files -> policy violation
    logical = {"inputs": {"RAW": {"slot": {"kind": "raw_folder"}, "mode": "ro"}}, "outputs": {}}
    spec = IoSpec(kind="parser", raw_folders=["RAW"], outputs={"files": ["o.csv"]})
    from core.run_custom._types import RunError
    with pytest.raises(RunError):
        run_custom_tool(
            tool_module=make_tool_module(tmp_path), iospec=spec, verb_schema={}, db_map=None,
            context={"verb_group": "G", "run_id": "R1"},
            layout_resolver=lambda lg: {"inputs": {"RAW": {"paths": [raw_dir]}}, "outputs": {}},
            work_dir=tmp_path, executor=FakeExecutor(logical), backend=recording_backend({}),
        )


# ── 2) pphrase run-id collection + injection ─────────────────────────────────────────────────────
def _pphrase_dbinputs_spec():
    return IoSpec(kind="pphrase", outputs={"folder": None},
                  extra={"db_inputs": [{"endpoint": "data_dump_dir", "params": {}}]})


def test_pphrase_injects_run_ids_from_direct_params(tmp_path):
    spec = _pphrase_dbinputs_spec()
    run_custom_tool(
        tool_module=make_tool_module(tmp_path), iospec=spec, verb_schema={}, db_map=None,
        context={"verb_group": "G", "params": {"run_ids": ["R2", "R1"]}},
        layout_resolver=lambda lg: {"inputs": {}, "outputs": {}},
        work_dir=tmp_path, executor=FakeExecutor({"inputs": {}, "outputs": {}}),
        backend=recording_backend({}),
    )
    injected = spec.extra["db_inputs"][0]["params"]["run_id"]
    assert injected == ["R1", "R2"]  # collected, deduped, sorted into the data_dump_dir endpoint


def test_pphrase_derives_run_ids_from_prephrase_settings(tmp_path):
    spec = _pphrase_dbinputs_spec()
    mod = make_tool_module(
        tmp_path,
        prephrase_settings=[{"id": "sample", "options": {"source": "noun:Sample"}}],
    )

    def fetch(noun_type):
        assert noun_type == "Sample"
        return [{"Sample ID": "S1", "_runID": "RUN9"}]

    run_custom_tool(
        tool_module=mod, iospec=spec, verb_schema={}, db_map=None,
        context={"params": {"sample": ["S1"]}, "fetch_noun_items": fetch},
        layout_resolver=lambda lg: {"inputs": {}, "outputs": {}},
        work_dir=tmp_path, executor=FakeExecutor({"inputs": {}, "outputs": {}}),
        backend=recording_backend({}),
    )
    assert spec.extra["db_inputs"][0]["params"]["run_id"] == ["RUN9"]


def test_pphrase_no_runids_leaves_db_inputs_untouched(tmp_path):
    spec = _pphrase_dbinputs_spec()
    run_custom_tool(
        tool_module=make_tool_module(tmp_path), iospec=spec, verb_schema={}, db_map=None,
        context={"params": {}},
        layout_resolver=lambda lg: {"inputs": {}, "outputs": {}},
        work_dir=tmp_path, executor=FakeExecutor({"inputs": {}, "outputs": {}}),
        backend=recording_backend({}),
    )
    assert "run_id" not in spec.extra["db_inputs"][0]["params"]


# ── 3) pphrase OUTPUT_FOLDER scratch -> canonical sync ───────────────────────────────────────────
def test_pphrase_output_folder_syncs_scratch_to_canonical(tmp_path):
    canonical = tmp_path / "canonical_out"
    logical = {"inputs": {}, "outputs": {"OUTPUT_FOLDER": {"slot": {"kind": "folder"}, "mode": "rw"}}}

    def layout_resolver(_lg):
        return {"inputs": {}, "outputs": {"OUTPUT_FOLDER": {"path": canonical}}}

    captured = {}

    def backend(entry, env):
        captured["env"] = env
        scratch = Path(env["ctx"].outputs["OUTPUT_FOLDER"])
        scratch.mkdir(parents=True, exist_ok=True)
        (scratch / "report.csv").write_text("ok")
        (scratch / "_internal").mkdir()  # underscore dirs must be skipped by the sync
        (scratch / "_internal" / "skip.txt").write_text("nope")
        return {"ok": True}

    spec = IoSpec(kind="pphrase", outputs={"folder": None})
    result = run_custom_tool(
        tool_module=make_tool_module(tmp_path), iospec=spec, verb_schema={}, db_map=None,
        context={"pphrase_name": "phraseX", "params": {}},
        layout_resolver=layout_resolver, work_dir=tmp_path,
        executor=FakeExecutor(logical), backend=backend,
    )
    assert result["ok"] is True
    # scratch lives under work_dir/pphrase_out/<phrase>, NOT the canonical root
    assert captured["env"]["ctx"].outputs["OUTPUT_FOLDER"] == str(tmp_path / "pphrase_out" / "phraseX")
    # real outputs synced to canonical; underscore-prefixed internal dirs skipped
    assert (canonical / "report.csv").read_text() == "ok"
    assert not (canonical / "_internal").exists()


# ── 4) error contract ────────────────────────────────────────────────────────────────────────────
def test_backend_apperror_is_reraised_not_downgraded(tmp_path):
    spec = IoSpec(kind="parser", outputs={"files": ["x.csv"]})
    err = AppError("CONTAINER_RUNTIME_NOT_FOUND", "no runtime", status=503)
    with pytest.raises(AppError) as ei:
        run_custom_tool(
            tool_module=make_tool_module(tmp_path), iospec=spec, verb_schema={}, db_map=None,
            context={"verb_group": "G", "run_id": "R1"},
            layout_resolver=lambda lg: {"inputs": {}, "outputs": {}},
            work_dir=tmp_path, executor=FakeExecutor({"inputs": {}, "outputs": {}}),
            backend=recording_backend({}, raises=err),
        )
    assert ei.value.status == 503  # typed deployment error preserved, not turned into ok=False


def test_backend_generic_exception_becomes_ok_false(tmp_path):
    spec = IoSpec(kind="parser", outputs={"files": ["x.csv"]})
    result = run_custom_tool(
        tool_module=make_tool_module(tmp_path), iospec=spec, verb_schema={}, db_map=None,
        context={"verb_group": "G", "run_id": "R1"},
        layout_resolver=lambda lg: {"inputs": {}, "outputs": {}},
        work_dir=tmp_path, executor=FakeExecutor({"inputs": {}, "outputs": {}}),
        backend=recording_backend({}, raises=RuntimeError("boom")),
    )
    assert result["ok"] is False
    assert "boom" in result["error"]
