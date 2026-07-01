# core/run_custom/inspect_static.py
from __future__ import annotations
import ast
from typing import Dict, List, Any
from ._common import log
from .pphrase_executor import PrepositionalPhraseExecutable


# ============================================================
# SECTION 1.5 — STATIC, SAFE INSPECTION (pure)
# ============================================================

def probe_pphrase_settings_static(module_path) -> Dict[str, Any]:
    """
    Static, safe inspection of a custom pre-phrase module:
      - DOES NOT import/execute the module
      - Extracts PREPHRASE_SETTINGS iff literal
      - Extracts TOOL_KIND or TOOL.kind if literal
      - Extracts TOOL_VERSION if literal
      - Validates shape via PrepositionalPhraseExecutable._validate_prephrase_settings
      - Collects dynamic sources from options dicts
    """
    from pathlib import Path as _Path
    mp = _Path(module_path)
    log.debug("[probe_static] begin | module=", str(mp))
    out = {
        "ok": False,
        "module_path": str(mp),
        "kind": None,
        "tool_version": None,
        "prephrase_settings": [],
        "dynamic_sources": [],
        "requires_import": False,
        "warnings": [],
        "error": None,
    }

    if not mp.exists():
        out["error"] = f"Module not found: {mp}"
        return out

    try:
        src = mp.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(mp))
    except Exception as e:
        out["error"] = f"Failed to parse module: {e}"
        return out

    def _lit(node):
        try:
            return ast.literal_eval(node)
        except Exception:
            return None

    tool_kind = None
    tool_version = None
    prephrase = None
    requires_import = False

    for node in tree.body:
        # --- PREPHRASE_SETTINGS ---
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PREPHRASE_SETTINGS" for t in node.targets):
            val = _lit(node.value)
            if isinstance(val, list):
                prephrase = val
                log.debug(f"[probe_static] PREPHRASE_SETTINGS literal ok (len={len(val)})")
            else:
                requires_import = True
                log.debug("[probe_static] PREPHRASE_SETTINGS non-literal; requires import")

        # --- TOOL_KIND (plain assignment) ---
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOL_KIND" for t in node.targets):
            lit = _lit(node.value)
            if isinstance(lit, str):
                tool_kind = lit
                log.debug("[probe_static] TOOL_KIND literal =", tool_kind)

        # --- TOOL_KIND (annotated assignment) ---
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "TOOL_KIND":
            lit = _lit(node.value)
            if isinstance(lit, str):
                tool_kind = lit
                log.debug("[probe_static] TOOL_KIND (annotated) literal =", tool_kind)

        # --- TOOL_VERSION (plain assignment) ---
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOL_VERSION" for t in node.targets):
            lit = _lit(node.value)
            if isinstance(lit, str):
                tool_version = lit
                log.debug("[probe_static] TOOL_VERSION literal =", tool_version)

        # --- TOOL_VERSION (annotated assignment) ---
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "TOOL_VERSION":
            lit = _lit(node.value)
            if isinstance(lit, str):
                tool_version = lit
                log.debug("[probe_static] TOOL_VERSION (annotated) literal =", tool_version)

        # --- TOOL dict / IoSpec fallback ---
        if tool_kind is None and isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOL" for t in node.targets):
            if isinstance(node.value, ast.Dict):
                lit = _lit(node.value)
                if isinstance(lit, dict):
                    tool_kind = str(lit.get("kind")) if lit.get("kind") is not None else None
                    log.debug("[probe_static] TOOL dict literal ok | kind=", tool_kind)
                else:
                    requires_import = True
            elif isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "IoSpec":
                kwargs = {}
                for kw in (node.value.keywords or []):
                    if not isinstance(kw, ast.keyword) or kw.arg is None:
                        requires_import = True
                        break
                    lit = _lit(kw.value)
                    if lit is None:
                        requires_import = True
                        break
                    kwargs[kw.arg] = lit
                else:
                    tool_kind = str(kwargs.get("kind")) if kwargs.get("kind") is not None else None
                    log.debug("[probe_static] TOOL IoSpec(...) literal args ok | kind=", tool_kind)

    out["tool_version"] = tool_version
    out["requires_import"] = requires_import

    if not tool_kind:
        out["error"] = "Unable to determine TOOL_KIND statically."
        return out
    out["kind"] = tool_kind

    if tool_kind != "pphrase":
        out["ok"] = True
        if prephrase:
            out["warnings"].append("PREPHRASE_SETTINGS present but TOOL_KIND != 'pphrase'")
        return out

    settings = prephrase or []

    # --- Validate shape using core validator (no I/O) ---
    try:
        PrepositionalPhraseExecutable()._validate_prephrase_settings(settings)
    except Exception as e:
        out["error"] = f"Invalid PREPHRASE_SETTINGS: {e}"
        return out

    # --- Collect dynamic sources (from options dicts only) ---
    sources: List[str] = []
    for field in settings:
        if not isinstance(field, dict):
            continue
        opts = field.get("options")
        if isinstance(opts, dict):
            s = opts.get("source")
            if isinstance(s, str) and s.strip():
                sources.append(s.strip())

    # De-dup while preserving order
    seen = set(); dyn = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            dyn.append(s)

    out["prephrase_settings"] = settings
    out["dynamic_sources"] = dyn
    out["ok"] = True
    log.debug("[probe_static] ok | dyn_sources=", dyn)
    return out
