# core/run_custom/wasm_sandbox.py
from __future__ import annotations
from typing import Callable, Any, Dict
from pathlib import Path
from ._types import SandboxExec
from ._common import resolve_path, log


# ============================================================
# SECTION 4 — WASM SANDBOX (pip-only wasmtime; NO wasi-vfs)
# ============================================================
# This section is optional at runtime: only used if you pass sandbox_exec=make_wasmtime_sandbox(...)
import json as _json
import tempfile as _tempfile
import os as _os
from typing import Union as _Union

try:
    from wasmtime import Store as _WStore, Module as _WModule, Linker as _WLinker, WasiConfig as _WWasiConfig
    _HAVE_WASMTIME = True
except Exception:
    _HAVE_WASMTIME = False

# Default path to your Python-in-WASI module (override via env GIMS_PYTHON_WASM or make_wasmtime_sandbox arg)
# Import resolver at module level
import sys as _sys
_core_parent = Path(__file__).parent.parent
if str(_core_parent) not in _sys.path:
    _sys.path.insert(0, str(_core_parent))

try:
    PYTHON_WASM_MODULE_DEFAULT = resolve_path(Path(), "wasm")
    log.debug(f"[core_run_customs] WASM module resolved: {PYTHON_WASM_MODULE_DEFAULT}")
except Exception as e:
    # Fallback if resolver fails
    PYTHON_WASM_MODULE_DEFAULT = Path(__file__).parent.parent / "custom" / "python-3.11.4.wasm"
    log.debug(f"[core_run_customs] WASM resolver failed, using fallback: {PYTHON_WASM_MODULE_DEFAULT}, error: {e}")

# Guest bootstrap that imports /tool.py and runs tool_module.run(ctx)
_BOOTSTRAP_SCRIPT = """
import json
import sys
import os
import importlib.util
import inspect
from pathlib import Path

TOOL_MODULE_PATH = Path("/tool.py")
CONTEXT_PATH = Path("/context.json")

class ToolContext:
    def __init__(self, inputs, outputs, params):
        self._inputs, self._outputs, self._params = inputs, outputs, params
    @property
    def inputs(self): return self._inputs
    @property
    def outputs(self): return self._outputs
    @property
    def params(self):  return self._params

def run_in_sandbox():
    try:
        with open(CONTEXT_PATH, "r") as f:
            ctx_data = json.load(f)

        ctx = ToolContext(ctx_data.get('inputs', {}),
                          ctx_data.get('outputs', {}),
                          ctx_data.get('params', {}))

        spec = importlib.util.spec_from_file_location("tool_module", TOOL_MODULE_PATH)
        tool_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool_module)

        # Prefer run(ctx) if available; otherwise set env and call zero-arg run()
        run_fn = getattr(tool_module, "run", None)
        if run_fn is None:
            raise RuntimeError("tool module has no 'run' function")

        sig = None
        try:
            sig = inspect.signature(run_fn)
        except Exception:
            pass

        if sig and len(sig.parameters) >= 1:
            # Supports run(context)
            run_fn(ctx)
        else:
            # Zero-arg run(); provide GIMS_IO_JSON for templates that expect it
            os.environ["GIMS_IO_JSON"] = json.dumps({
                "kind": "parser" if hasattr(tool_module, "TOOL_KIND") and getattr(tool_module, "TOOL_KIND") == "parser"
                        else ctx_data.get("kind", "parser"),
                "inputs": ctx_data.get("inputs", {}),
                "outputs": ctx_data.get("outputs", {}),
                "params": ctx_data.get("params", {})
            })
            run_fn()

        print(json.dumps({"ok": True}))
    except Exception as e:
        import traceback
        print(json.dumps({
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }))

run_in_sandbox()
""".lstrip()


class WasmtimeExecutor:
    """
    Executes a Python-based tool inside a Wasmtime WASI sandbox with proper isolation.
    Input files are copied into the sandbox, and output files are copied back out.
    The sandbox has NO access to the host filesystem except what we explicitly provide.
    """

    def __init__(self, *, tool_module_path: Path, work_dir: Path, python_wasm_module: Path):
        log.debug(f"[WASM.__init__] tool_module_path={tool_module_path}")
        log.debug(f"[WASM.__init__] work_dir={work_dir}")
        log.debug(f"[WASM.__init__] python_wasm_module={python_wasm_module}")
        
        if not _HAVE_WASMTIME:
            raise RuntimeError("wasmtime is not installed. `pip install wasmtime`")

        log.debug(f"[WASM.__init__] checking if wasm module exists: {python_wasm_module.exists()}")
        if not python_wasm_module.exists():
            raise FileNotFoundError(f"Python WASI module not found: {python_wasm_module}")

        self._tool_module_path = Path(tool_module_path)
        self._work_dir = Path(work_dir)
        self._py_wasm = Path(python_wasm_module)
        log.debug("[WASM.__init__] paths stored")

        self._store = _WStore()
        log.debug("[WASM.__init__] store created")
        
        log.debug(f"[WASM.__init__] loading module from: {str(self._py_wasm)}")
        self._module = _WModule.from_file(self._store.engine, str(self._py_wasm))
        log.debug("[WASM.__init__] module loaded successfully")

    def execute(self, _entry_point_fn: Callable, ctx_obj: Any) -> Dict[str, Any]:
        """
        Execute tool_module.run(ctx) in the sandbox.
        ctx_obj: object with .inputs/.outputs/.params
        """
        with _tempfile.TemporaryDirectory() as tmpdir:
            sandbox_root = Path(tmpdir)
            log.debug(f"[WASM.execute] sandbox_root: {sandbox_root}")
            
            # Create sandbox directories
            inputs_dir = sandbox_root / "inputs"
            outputs_dir = sandbox_root / "outputs"
            inputs_dir.mkdir()
            outputs_dir.mkdir()
            log.debug(f"[WASM.execute] created dirs: inputs={inputs_dir}, outputs={outputs_dir}")

            # /bootstrap.py
            (sandbox_root / "bootstrap.py").write_text(_BOOTSTRAP_SCRIPT, encoding="utf-8")
            log.debug("[WASM.execute] wrote bootstrap.py")
            
            # /tool.py
            (sandbox_root / "tool.py").write_bytes(self._tool_module_path.read_bytes())
            log.debug(f"[WASM.execute] wrote tool.py from {self._tool_module_path}")
            
            # Copy input files into sandbox and rewrite paths
            sandboxed_inputs = {}
            original_inputs = getattr(ctx_obj, "inputs", {})
            log.debug(f"[WASM.execute] processing {len(original_inputs)} input keys: {list(original_inputs.keys())}")
            
            for key, value in original_inputs.items():
                log.debug(f"[WASM.execute] input[{key}]: type={type(value).__name__}, value={value}")
                
                if isinstance(value, str):
                    try:
                        src_path = Path(value).resolve()
                        log.debug(f"[WASM.execute] input[{key}]: resolved path={src_path}, exists={src_path.exists()}, is_file={src_path.is_file() if src_path.exists() else 'N/A'}")
                        
                        if src_path.exists() and src_path.is_file():
                            dst = inputs_dir / src_path.name
                            dst.write_bytes(src_path.read_bytes())
                            sandboxed_inputs[key] = f"/inputs/{src_path.name}"
                            log.debug(f"[WASM.execute] input[{key}]: copied {src_path.name} -> {dst}")
                        else:
                            sandboxed_inputs[key] = value
                            log.debug(f"[WASM.execute] input[{key}]: not a file, passing through")
                    except Exception as e:
                        sandboxed_inputs[key] = value
                        log.debug(f"[WASM.execute] input[{key}]: error resolving path: {e}")
                        
                elif isinstance(value, list):
                    log.debug(f"[WASM.execute] input[{key}]: processing list of {len(value)} items")
                    sandboxed_list = []
                    for idx, item in enumerate(value):
                        if isinstance(item, str):
                            try:
                                src_path = Path(item).resolve()
                                log.debug(f"[WASM.execute] input[{key}][{idx}]: path={src_path}, exists={src_path.exists()}, is_file={src_path.is_file() if src_path.exists() else 'N/A'}")
                                
                                if src_path.exists() and src_path.is_file():
                                    dst = inputs_dir / src_path.name
                                    dst.write_bytes(src_path.read_bytes())
                                    sandboxed_list.append(f"/inputs/{src_path.name}")
                                    log.debug(f"[WASM.execute] input[{key}][{idx}]: copied {src_path.name}")
                                else:
                                    sandboxed_list.append(item)
                                    log.debug(f"[WASM.execute] input[{key}][{idx}]: not a file, passing through")
                            except Exception as e:
                                sandboxed_list.append(item)
                                log.debug(f"[WASM.execute] input[{key}][{idx}]: error: {e}")
                        else:
                            sandboxed_list.append(item)
                            log.debug(f"[WASM.execute] input[{key}][{idx}]: non-string, passing through")
                    sandboxed_inputs[key] = sandboxed_list
                else:
                    sandboxed_inputs[key] = value
                    log.debug(f"[WASM.execute] input[{key}]: non-string/list, passing through")
            
            log.debug(f"[WASM.execute] sandboxed_inputs keys: {list(sandboxed_inputs.keys())}")
            
            # Rewrite output paths to point into sandbox
            sandboxed_outputs = {}
            original_outputs = getattr(ctx_obj, "outputs", {})
            output_map = {}  # Map sandbox paths back to real paths
            log.debug(f"[WASM.execute] processing {len(original_outputs)} output keys: {list(original_outputs.keys())}")
            
            for key, value in original_outputs.items():
                if isinstance(value, str):
                    filename = Path(value).name
                    sandboxed_outputs[key] = f"/outputs/{filename}"
                    output_map[f"/outputs/{filename}"] = value
                    log.debug(f"[WASM.execute] output[{key}]: {filename} -> /outputs/{filename} (real: {value})")
                else:
                    sandboxed_outputs[key] = value
                    log.debug(f"[WASM.execute] output[{key}]: non-string, passing through")
            
            # /context.json with sandboxed paths
            ctx_data = {
                "inputs": sandboxed_inputs,
                "outputs": sandboxed_outputs,
                "params": getattr(ctx_obj, "params", {}),
            }
            (sandbox_root / "context.json").write_text(_json.dumps(ctx_data, indent=2), encoding="utf-8")
            log.debug(f"[WASM.execute] wrote context.json: {len(ctx_data)} keys")

            # WASI config
            wasi = _WWasiConfig()
            wasi.argv = ("python", "/bootstrap.py")
            log.debug("[WASM.execute] WASI argv set: python /bootstrap.py")

            # Mount ONLY the sandbox root - no access to host filesystem
            resolved_root = str(sandbox_root.resolve())
            log.debug(f"[WASM.execute] mounting sandbox root: {resolved_root}")
            wasi.preopen_dir(resolved_root, "/")
            log.debug("[WASM.execute] preopen_dir successful")

            # Capture stdio to files (reliable across wasmtime versions)
            stdout_path = sandbox_root / "stdout.txt"
            stderr_path = sandbox_root / "stderr.txt"
            
            wasi.stdout_file = str(stdout_path)
            wasi.stderr_file = str(stderr_path)
            
            self._store.set_wasi(wasi)
            log.debug("[WASM.execute] WASI configured (file-based stdio), starting linker")

            linker = _WLinker(self._store.engine)
            linker.define_wasi()
            log.debug("[WASM.execute] linker defined, instantiating module")
            instance = linker.instantiate(self._store, self._module)
            log.debug("[WASM.execute] module instantiated, calling _start")

            try:
                instance.exports(self._store)["_start"](self._store)
                log.debug("[WASM.execute] _start completed successfully")
            except Exception as e:
                log.debug(f"[WASM.execute] _start failed: {e}")
                try:
                    stderr = stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else str(e)
                except Exception:
                    stderr = str(e)
                return {"ok": False, "error": f"WASM trap: {e}\nStderr: {stderr}"}

            try:
                if stdout_path.exists():
                    stdout = stdout_path.read_text(encoding="utf-8").strip()
                else:
                    stdout = ""
                log.debug(f"[WASM.execute] stdout length: {len(stdout)}")
            except Exception as e:
                stdout = ""
                log.debug(f"[WASM.execute] failed to read stdout file: {e}")
                
            try:
                if stderr_path.exists():
                    stderr = stderr_path.read_text(encoding="utf-8")
                else:
                    stderr = ""
                log.debug(f"[WASM.execute] stderr length: {len(stderr)}")
            except Exception as e:
                stderr = ""
                log.debug(f"[WASM.execute] failed to read stderr file: {e}")

            if not stdout:
                log.debug("[WASM.execute] ERROR: no stdout produced")
                return {"ok": False, "error": "Sandbox produced no output.", "logs": [stderr]}

            try:
                result = _json.loads(stdout)
                log.debug(f"[WASM.execute] parsed result: ok={result.get('ok')}, keys={list(result.keys())}")
                result["logs"] = [stderr]
                
                # Copy output files from sandbox back to real filesystem
                if result.get("ok"):
                    log.debug(f"[WASM.execute] copying outputs back, map has {len(output_map)} entries")
                    produced = []
                    for sandbox_path, real_path in output_map.items():
                        sandbox_file = outputs_dir / Path(sandbox_path).name
                        log.debug(f"[WASM.execute] checking {sandbox_file}, exists={sandbox_file.exists()}")
                        if sandbox_file.exists():
                            Path(real_path).parent.mkdir(parents=True, exist_ok=True)
                            Path(real_path).write_bytes(sandbox_file.read_bytes())
                            produced.append(real_path)
                            log.debug(f"[WASM.execute] copied {sandbox_file.name} -> {real_path}")
                        else:
                            log.debug(f"[WASM.execute] WARNING: output file {sandbox_file} not found")
                    result["produced"] = produced
                    log.debug(f"[WASM.execute] produced {len(produced)} files")
                
                return result
            except Exception as e:
                log.debug(f"[WASM.execute] ERROR parsing result: {e}")
                return {"ok": False, "error": f"Failed to decode JSON from sandbox stdout: {stdout}\nException: {e}", "logs": [stderr]}


def make_wasmtime_sandbox(python_wasm_module: _Union[str, Path, None] = None) -> SandboxExec:
    """
    Factory that returns a SandboxExec compatible function using Wasmtime.
    Usage with run_custom_tool(..., sandbox_exec=make_wasmtime_sandbox()):
      - run_custom_tool will pass env with:
          env["tool_module_path"]   -> required
          env["work_dir"]           -> required
          env["ctx"]                -> required (the _Ctx object)
          env["python_wasm_module"] -> optional override
    """
    if isinstance(python_wasm_module, str):
        python_wasm_module = Path(python_wasm_module)
    env_wasm = _os.environ.get("GIMS_PYTHON_WASM", "")
    if python_wasm_module:
        default_wasm = python_wasm_module
    elif env_wasm:
        default_wasm = Path(env_wasm)
    else:
        # Use resolver to find WASM file from layout map
        try:
            import sys
            from pathlib import Path as _Path
            # Add parent directory to path so we can import manifest
            _parent = _Path(__file__).parent.parent
            if str(_parent) not in sys.path:
                sys.path.insert(0, str(_parent))
            default_wasm = resolve_path(Path(), "wasm")
            log.debug(f"[make_wasmtime_sandbox] resolved WASM path: {default_wasm}")
        except Exception as e:
            log.debug(f"[make_wasmtime_sandbox] resolver failed: {e}, using PYTHON_WASM_MODULE_DEFAULT")
            default_wasm = PYTHON_WASM_MODULE_DEFAULT

    def _sandbox_exec(_entry_point_fn: Callable[[Any], None], env: Dict[str, Any]) -> Dict[str, Any]:
        tool_path = env.get("tool_module_path")
        work_dir = env.get("work_dir")
        ctx_obj = env.get("ctx")
        wasm_override = env.get("python_wasm_module")

        if not tool_path:
            return {"ok": False, "error": "Wasmtime sandbox: missing env['tool_module_path']"}
        if not work_dir:
            return {"ok": False, "error": "Wasmtime sandbox: missing env['work_dir']"}
        if ctx_obj is None:
            return {"ok": False, "error": "Wasmtime sandbox: missing env['ctx']"}

        wasm_path = Path(wasm_override) if wasm_override else default_wasm
        try:
            executor = WasmtimeExecutor(
                tool_module_path=Path(tool_path),
                work_dir=Path(work_dir),
                python_wasm_module=wasm_path,
            )
            return executor.execute(_entry_point_fn, ctx_obj)
        except Exception as e:
            return {"ok": False, "error": f"Wasmtime sandbox init/exec failed: {e}"}

    return _sandbox_exec
