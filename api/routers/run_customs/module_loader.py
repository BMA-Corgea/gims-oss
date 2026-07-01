# api/routers/run_customs/module_loader.py
# Untrusted-source module loading (split verbatim from run_customs.py).
from __future__ import annotations

import sys
import types
from pathlib import Path

from utils.logger import get_logger
log = get_logger("api.routers.run_customs")


def _module_from_source(name: str, source: str, file_hint: Path) -> types.ModuleType:
    """
    Create a Python module from source text without relying on filesystem import.
    Sets __file__ to a meaningful hint for tracebacks.
    Always refreshes the module namespace to avoid stale code.
    """
    # Ensure a fresh slot in sys.modules (no stale cache)
    if name in sys.modules:
        log.debug("[module.load][refresh] removing previous module from sys.modules:", name)
        try:
            del sys.modules[name]
        except Exception as e:
            log.debug("[module.load][refresh][warn] could not delete old module:", repr(e))

    log.debug("[module.load] compiling source for", name, "file_hint=", str(file_hint))
    mod = types.ModuleType(name)
    mod.__file__ = str(file_hint)
    mod.__package__ = None
    exec(compile(source, filename=str(file_hint), mode="exec"), mod.__dict__)
    # Place fresh module in sys.modules under unique name
    sys.modules[name] = mod
    log.debug("[module.load] module exec OK ->", name)
    return mod
