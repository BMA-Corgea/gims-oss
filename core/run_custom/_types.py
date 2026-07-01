# core/run_custom/_types.py
from __future__ import annotations
from typing import Callable, Dict, Any


# ============================================================
# SECTION 3 — RUNNER / ORCHESTRATOR
# ============================================================

class RunError(Exception): ...
class ContextError(RunError): ...

# Types
# The layout_resolver must map each logical entry to a dict that contains:
#   - "path" or "paths" (physical resolution)
#   - it's OK (recommended) to also carry through the original "slot" for transparency
LayoutResolver = Callable[[Dict[str, Dict[str, Any]]], Dict[str, Dict[str, Any]]]

# SandboxExec(entry_point_fn, env) -> result_dict
#   - entry_point_fn: callable that (would) run tool_module.run(ctx) natively (not used by WASM)
#   - env: dict; we pass rich metadata so sandboxes can self-contain execution:
#       {
#         "kind": iospec.kind,
#         "ctx": <_Ctx object with .inputs/.outputs/.params>,
#         "tool_module_path": "<host path to tool module .py>",
#         "work_dir": "<host work dir path>",
#         "python_wasm_module": "<optional override to the Python-WASI .wasm>"
#       }
SandboxExec = Callable[[Callable[[Any], None], Dict[str, Any]], Dict[str, Any]]
