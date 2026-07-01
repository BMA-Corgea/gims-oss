# api/routers/run_customs/prephrase_settings.py
# Prephrase settings loading (split verbatim from run_customs.py).
from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from utils.logger import get_logger
log = get_logger("api.routers.run_customs")

from .fs_helpers import _read_text_via_io
from .module_loader import _module_from_source


def _load_prephrase_settings(module_path: Path) -> List[Dict[str, Any]]:
    """
    Load prephrase settings with S3 awareness:
      1) Parse source via _read_text_via_io
      2) Attempt static literal extraction
      3) Fallback to exec module source and read exported values
    """
    src = _read_text_via_io(module_path, encoding="utf-8")
    tree = ast.parse(src, filename=str(module_path))

    # Look for literal PREPHRASE_SETTINGS
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PREPHRASE_SETTINGS":
                    try:
                        val = ast.literal_eval(node.value)
                        if isinstance(val, list):
                            log.debug(f"[prephrase.router] PREPHRASE_SETTINGS literal found ({len(val)} fields)")
                            return val
                    except Exception:
                        pass
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "PREPHRASE_SETTINGS":
            try:
                val = ast.literal_eval(node.value)
                if isinstance(val, list):
                    log.debug(f"[prephrase.router] PREPHRASE_SETTINGS annotated literal found ({len(val)} fields)")
                    return val
            except Exception:
                pass

    # Fallback: exec source in a fresh temporary module and read settings
    tmp_name = f"pphrase_mod_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}"
    mod = _module_from_source(tmp_name, src, module_path)

    if hasattr(mod, "get_PREPHRASE_SETTINGS"):
        settings = mod.get_PREPHRASE_SETTINGS()
        log.debug(f"[prephrase.router] PREPHRASE_SETTINGS via function ({len(settings)} fields)")
        return settings
    if hasattr(mod, "PREPHRASE_SETTINGS"):
        settings = getattr(mod, "PREPHRASE_SETTINGS")
        if isinstance(settings, list):
            log.debug(f"[prephrase.router] PREPHRASE_SETTINGS via global ({len(settings)} fields)")
            return settings

    raise RuntimeError("No PREPHRASE_SETTINGS found in module")
