"""One selectable execution backend for custom (untrusted) tools (Phase 6 / R15).

Decision 4: container isolation is the **default** for untrusted tools; an in-process path is the
opt-in for trusted self-hosted installs. Today the live runner (``core.run_custom.run_custom_tool``)
runs tools **in-process by default** (``tool_module.run(ctx)`` with full host privileges) and only
sandboxes when a WASM ``sandbox_exec`` is explicitly passed — i.e. the safe default is inverted.

This module provides the three backends behind one seam and a :func:`select_backend` factory that
makes **container the default**:

  * :func:`make_container_backend` — runs the tool in a hardened container (``utils.container_run``),
    inputs copied into an isolated staging dir mounted read-only, the only writable mount an
    ephemeral output dir, and every produced file vetted by :mod:`utils.artifact_broker` before it
    reaches the real destination. THE default.
  * :func:`inprocess_backend` — the legacy in-process path, **gated** on
    :func:`utils.config.allow_inprocess_tools` (raises ``AppError('INPROCESS_TOOLS_DISABLED', 403)``
    otherwise). For trusted self-hosted installs only.
  * :func:`make_wasm_backend` — thin wrapper over the existing wasmtime sandbox.

Every backend is a callable with the same shape as the existing ``sandbox_exec`` parameter —
``backend(entry_point_fn, env) -> result_dict`` — so it drops straight into ``run_custom_tool``
without changing that function's contract. ``env`` carries ``kind``, ``ctx`` (``.inputs/.outputs/
.params``), ``tool_module_path`` and ``work_dir`` (the shape ``run_custom_tool`` already builds).
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils import config, container_run
from utils import artifact_broker
from utils.logger import get_logger
from core.errors import AppError

log = get_logger(__name__)

# Backend signature, matching the legacy ``sandbox_exec``.
Backend = Callable[[Callable[..., Any], Dict[str, Any]], Dict[str, Any]]


# ──────────────────────────────────────────────────────────────────────────────
# In-container bootstrap: loads /app/code/tool.py and runs it against /app/code/context.json.
# Mirrors the WASM bootstrap but uses bind-mount paths. Untrusted code runs ONLY here.
# ──────────────────────────────────────────────────────────────────────────────
_CONTAINER_BOOTSTRAP = r'''
import json, os, sys, importlib.util, inspect, traceback
from pathlib import Path

CODE = Path("/app/code")

class _Ctx:
    def __init__(self, i, o, p):
        self._i, self._o, self._p = i, o, p
    @property
    def inputs(self):  return self._i
    @property
    def outputs(self): return self._o
    @property
    def params(self):  return self._p

def _main():
    try:
        data = json.loads((CODE / "context.json").read_text())
        ctx = _Ctx(data.get("inputs", {}), data.get("outputs", {}), data.get("params", {}))
        spec = importlib.util.spec_from_file_location("tool_module", str(CODE / "tool.py"))
        tm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tm)
        run_fn = getattr(tm, "run", None)
        if run_fn is None:
            raise RuntimeError("tool module defines no run()")
        sig = None
        try:
            sig = inspect.signature(run_fn)
        except Exception:
            pass
        if sig is not None and len(sig.parameters) >= 1:
            run_fn(ctx)
        else:
            os.environ["GIMS_IO_JSON"] = json.dumps({
                "kind": data.get("kind", "parser"),
                "inputs": data.get("inputs", {}),
                "outputs": data.get("outputs", {}),
                "params": data.get("params", {}),
            })
            run_fn()
        print("__GIMS_RESULT__" + json.dumps({"ok": True}))
    except Exception as e:
        print("__GIMS_RESULT__" + json.dumps({"ok": False, "error": str(e),
                                              "traceback": traceback.format_exc()}))

_main()
'''.lstrip()

_RESULT_MARKER = "__GIMS_RESULT__"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers (pure host logic, unit-testable without a container)
# ──────────────────────────────────────────────────────────────────────────────
def extract_dependencies(tool_path: Path) -> List[str]:
    """Best-effort, **exec-free** read of a tool's declared pip dependencies.

    Parses the source AST for a module-level ``DEPENDENCIES = [...]`` or a ``TOOL``/``META`` dict
    literal with a ``"dependencies"`` key, via :func:`ast.literal_eval` on that node only — the
    untrusted module is never imported here. Returns ``[]`` if none are declared/parseable."""
    try:
        tree = ast.parse(Path(tool_path).read_text(encoding="utf-8"))
    except Exception:
        return []
    deps: List[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = {t.id for t in node.targets if isinstance(t, ast.Name)}
        try:
            if names & {"DEPENDENCIES", "DEPS"}:
                val = ast.literal_eval(node.value)
                if isinstance(val, (list, tuple)):
                    deps += [str(x) for x in val]
            elif names & {"TOOL", "META", "METADATA"}:
                val = ast.literal_eval(node.value)
                if isinstance(val, dict) and isinstance(val.get("dependencies"), (list, tuple)):
                    deps += [str(x) for x in val["dependencies"]]
        except Exception:
            continue
    # de-dup, drop blanks, keep order
    seen, out = set(), []
    for d in deps:
        d = d.strip()
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _stage_inputs(inputs: Dict[str, Any], staging: Path) -> Dict[str, Any]:
    """Copy input files into the read-only staging dir, rewriting paths to /app/inputs/<name>.
    Non-file values pass through unchanged (mirrors the WASM executor)."""
    def _one(value):
        if isinstance(value, str):
            try:
                src = Path(value)
                if src.is_file():
                    dst = staging / src.name
                    shutil.copyfile(src, dst)  # content only; inputs are trusted project data
                    return f"/app/inputs/{src.name}"
            except OSError:
                pass
        return value

    sandboxed: Dict[str, Any] = {}
    for key, value in (inputs or {}).items():
        if isinstance(value, list):
            sandboxed[key] = [_one(v) for v in value]
        else:
            sandboxed[key] = _one(value)
    return sandboxed


def _plan_outputs(outputs: Dict[str, Any], kind: str):
    """Return (sandboxed_outputs, plan). For pphrase the whole /app/output dir is the surface
    (plan = {'__folder__': real_dir}); for parsers each declared output maps a filename to its
    real destination path (plan = {filename: real_path})."""
    sandboxed: Dict[str, Any] = {}
    plan: Dict[str, str] = {}
    for key, value in (outputs or {}).items():
        if kind == "pphrase" and key == "OUTPUT_FOLDER":
            sandboxed[key] = "/app/output"
            if isinstance(value, str):
                plan["__folder__"] = value
        elif isinstance(value, str):
            filename = Path(value).name
            sandboxed[key] = f"/app/output/{filename}"
            plan[filename] = value
        else:
            sandboxed[key] = value
    return sandboxed, plan


def _broker_outputs(out_dir: Path, plan: Dict[str, str], result: Dict[str, Any]) -> List[str]:
    """Validate the container's produced files and copy survivors to their real destinations."""
    produced: List[str] = []
    rejected: List[Dict[str, str]] = []

    if "__folder__" in plan:
        rep = artifact_broker.collect_artifacts(out_dir, Path(plan["__folder__"]))
        produced += rep.committed
        rejected += rep.rejected
    else:
        for filename, real in plan.items():
            src = out_dir / filename
            if not src.exists():
                log.debug("[container] declared output not produced:", filename)
                continue
            ok, reason = artifact_broker.validate_artifact(src, src_root=out_dir)
            if not ok:
                log.warning("[container] dropped output", filename, "->", reason)
                rejected.append({"path": filename, "reason": reason})
                continue
            dst = Path(real)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            try:
                os.chmod(dst, 0o644)
            except OSError:
                pass
            produced.append(str(dst))

    if rejected:
        result.setdefault("logs", []).append(
            "artifacts rejected by broker: " + json.dumps(rejected)
        )
        result["rejected_artifacts"] = rejected
    return produced


def _image_present(image: str, runtime: str) -> bool:
    try:
        r = subprocess.run([runtime, "image", "inspect", image],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _ensure_image(deps: List[str], runtime: str) -> str:
    """Ensure an image exists to run the tool. No deps -> the base image (pulled if missing).
    With deps -> a derived image (FROM base + pip install) cached by a deps hash. Pull/build use
    the network (trusted ops); the untrusted tool run itself stays --network=none."""
    base = config.container_base_image()
    if not deps:
        if not _image_present(base, runtime):
            log.debug("[container] pulling base image", base)
            subprocess.run([runtime, "pull", base], check=True, timeout=600)
        return base

    digest = hashlib.sha256(("|".join([base, *sorted(deps)])).encode()).hexdigest()[:12]
    tag = f"gims_tool:{digest}"
    if _image_present(tag, runtime):
        return tag
    if not _image_present(base, runtime):
        subprocess.run([runtime, "pull", base], check=True, timeout=600)
    with tempfile.TemporaryDirectory() as bd:
        # NB: deps come from the (untrusted) tool; pip-installing them runs build code on the
        # daemon, same trust model as the legacy Track-B parser images. Operators vet which tools
        # are installed. The untrusted *run* is still fully hardened + offline.
        dockerfile = (
            f"FROM {base}\n"
            f"ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
            f"RUN pip install --no-cache-dir {' '.join(deps)}\n"
        )
        Path(bd, "Dockerfile").write_text(dockerfile, encoding="utf-8")
        log.debug("[container] building tool image", tag, "deps:", deps)
        subprocess.run([runtime, "build", "-t", tag, bd], check=True, timeout=1800)
    return tag


def _parse_container_result(proc: subprocess.CompletedProcess) -> Dict[str, Any]:
    """Extract the bootstrap's JSON verdict (prefixed with the result marker) from stdout."""
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith(_RESULT_MARKER):
            try:
                res = json.loads(line[len(_RESULT_MARKER):])
                res.setdefault("logs", [])
                if stderr:
                    res["logs"].append(stderr)
                return res
            except json.JSONDecodeError:
                break
    return {
        "ok": False,
        "error": f"tool container produced no result (exit {proc.returncode})",
        "logs": [stdout[-2000:], stderr[-2000:]],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Backends
# ──────────────────────────────────────────────────────────────────────────────
class ContainerToolExecutor:
    """Runs a single-file custom tool in a hardened container, brokering artifacts back out."""

    def execute(self, _entry_point_fn: Callable[..., Any], env: Dict[str, Any]) -> Dict[str, Any]:
        tool_path = env.get("tool_module_path")
        work_dir = env.get("work_dir")
        ctx = env.get("ctx")
        kind = env.get("kind") or "parser"
        if not tool_path:
            return {"ok": False, "error": "ContainerBackend: missing env['tool_module_path']"}
        if not work_dir:
            return {"ok": False, "error": "ContainerBackend: missing env['work_dir']"}
        if ctx is None:
            return {"ok": False, "error": "ContainerBackend: missing env['ctx']"}

        runtime = container_run.runtime_binary_or_raise()
        deps = extract_dependencies(Path(tool_path))
        try:
            image = _ensure_image(deps, runtime)
        except (OSError, subprocess.SubprocessError) as e:
            raise AppError("CONTAINER_IMAGE_BUILD_FAILED",
                           "Failed to prepare the tool container image", status=500,
                           details={"deps": deps, "error": str(e)}) from e

        with tempfile.TemporaryDirectory(dir=str(work_dir)) as td:
            root = Path(td)
            code_dir, in_dir, out_dir = root / "code", root / "inputs", root / "outputs"
            for d in (code_dir, in_dir, out_dir):
                d.mkdir()

            (code_dir / "bootstrap.py").write_text(_CONTAINER_BOOTSTRAP, encoding="utf-8")
            (code_dir / "tool.py").write_bytes(Path(tool_path).read_bytes())

            sandboxed_inputs = _stage_inputs(getattr(ctx, "inputs", {}) or {}, in_dir)
            sandboxed_outputs, plan = _plan_outputs(getattr(ctx, "outputs", {}) or {}, kind)
            (code_dir / "context.json").write_text(json.dumps({
                "kind": kind,
                "inputs": sandboxed_inputs,
                "outputs": sandboxed_outputs,
                "params": getattr(ctx, "params", {}) or {},
            }), encoding="utf-8")

            cmd = container_run.build_hardened_run_cmd(
                runtime_binary=runtime,
                image=image,
                mounts=[
                    container_run.Mount(str(code_dir.resolve()), "/app/code", "ro"),
                    container_run.Mount(str(in_dir.resolve()), "/app/inputs", "ro"),
                    container_run.Mount(str(out_dir.resolve()), "/app/output", "rw"),
                ],
                workdir="/app/output",
                command=["python", "/app/code/bootstrap.py"],
            )
            proc = container_run.run_container(cmd, capture=True)
            result = _parse_container_result(proc)
            if not result.get("ok"):
                return result
            result["produced"] = _broker_outputs(out_dir, plan, result)
            return result


def make_container_backend() -> Backend:
    """The default backend: hardened container + artifact broker."""
    executor = ContainerToolExecutor()
    return executor.execute


def inprocess_backend(_entry_point_fn: Callable[..., Any], env: Dict[str, Any]) -> Dict[str, Any]:
    """Gated in-process execution (trusted self-hosted only). Runs the tool in THIS interpreter
    with full host privileges — refused unless ``GIMS_ALLOW_INPROCESS_TOOLS`` is set."""
    if not config.allow_inprocess_tools():
        raise AppError(
            "INPROCESS_TOOLS_DISABLED",
            "In-process tool execution is disabled. Untrusted tools run in a container; set "
            "GIMS_ALLOW_INPROCESS_TOOLS=true only for trusted self-hosted installs.",
            status=403,
        )
    ctx = env.get("ctx")
    kind = env.get("kind") or "parser"
    old_cwd = os.getcwd()
    try:
        if kind == "pphrase" and ctx is not None:
            out_folder = (getattr(ctx, "outputs", {}) or {}).get("OUTPUT_FOLDER")
            if out_folder:
                os.chdir(out_folder)
        _entry_point_fn()  # calls tool_module.run(ctx)
    finally:
        os.chdir(old_cwd)
    return {"ok": True, "logs": []}


def make_wasm_backend(python_wasm: Optional[str] = None) -> Backend:
    """Wrap the existing wasmtime sandbox as a backend (lazy import avoids an import cycle)."""
    from core.run_custom import make_wasmtime_sandbox
    return make_wasmtime_sandbox(python_wasm)


def select_backend(exec_mode: Optional[str], *, python_wasm: Optional[str] = None) -> Backend:
    """Map a requested ``exec_mode`` to a backend. DEFAULT (None/empty/``container``) is the
    hardened container. ``wasm`` -> wasmtime. ``native``/``inprocess`` -> the gated in-process
    backend (which itself raises 403 unless explicitly allowed). Unknown modes -> AppError."""
    mode = (exec_mode or "container").strip().lower()
    if mode in ("", "container", "docker", "podman"):
        return make_container_backend()
    if mode == "wasm":
        return make_wasm_backend(python_wasm)
    if mode in ("native", "inprocess", "in_process", "in-process"):
        return inprocess_backend
    raise AppError("UNKNOWN_EXEC_MODE", f"Unknown execution mode: {exec_mode!r}", status=400,
                   details={"exec_mode": exec_mode})
