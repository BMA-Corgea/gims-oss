# core/run_custom/__init__.py
"""Role-named package split of the former core/core_run_customs.py (wiring-neutral).

Re-exports the exact public surface importers relied on via
`from core.run_custom import ...`.
The wasm_sandbox submodule is imported eagerly so its import-time side effects
(sys.path insert + PYTHON_WASM_MODULE_DEFAULT resolution) fire exactly as before.
"""
from .schema import IoSpec, ExecutableBase
from .parser_executor import CustomParserExecutable
from .pphrase_executor import PrepositionalPhraseExecutable
from .pphrase_expand import expand_prephrase_settings_dynamic
from .inspect_static import probe_pphrase_settings_static
from .predigest import (
    PreDigestRegistry,
    predigest_passthrough,
    predigest_xlsx_to_csvs,
    default_predigest_registry,
)
from ._types import RunError, ContextError, LayoutResolver, SandboxExec
from .registry import EXECUTOR_REGISTRY, resolve_executor
from .runner import ExecutionService, run_custom_tool
from .wasm_sandbox import WasmtimeExecutor, make_wasmtime_sandbox

__all__ = [
    "IoSpec",
    "ExecutableBase",
    "CustomParserExecutable",
    "PrepositionalPhraseExecutable",
    "expand_prephrase_settings_dynamic",
    "probe_pphrase_settings_static",
    "PreDigestRegistry",
    "predigest_passthrough",
    "predigest_xlsx_to_csvs",
    "default_predigest_registry",
    "RunError",
    "ContextError",
    "LayoutResolver",
    "SandboxExec",
    "EXECUTOR_REGISTRY",
    "resolve_executor",
    "ExecutionService",
    "run_custom_tool",
    "WasmtimeExecutor",
    "make_wasmtime_sandbox",
]
