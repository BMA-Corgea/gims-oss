# core/run_custom/schema.py
# ============================================================
# SECTION 1 - SCHEMA + EXECUTORS (PATHLESS POLICY ONLY)
# ============================================================
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from ._common import log


# ---------- IoSpec (pathless) ----------
@dataclass
class IoSpec:
    """
    Canonical, pathless IO spec shared by all executables.

    kind:        "parser" | "pphrase"
    raw_folders: special inputs that are folder-based (backend enforces 1 file per folder, may pre-digest)
    file_inputs: exact-file inputs (logical names, not paths)
    outputs:     parser -> {"files":[...]} | pphrase -> {"folder":"..."}
    extra:       optional extensions, e.g.:
                   {
                     "db_inputs":[{ "endpoint": str, "params": dict, "alias"?: str }, ...],
                     "post_doc": {                                # OPTIONAL host-side, post-container document hook
                       "entry": "pkg.module:callable",            # import path "module:callable"
                       "args": { ... arbitrary JSON payload ... },# passed through unchanged
                       "safety": "light" | "off",                 # default "light"
                       "timeout_s": int                           # default 20
                     }
                   }
    """
    kind: str                               # "parser" | "pphrase"
    raw_folders: List[str] = field(default_factory=list)
    file_inputs: List[str] = field(default_factory=list)
    outputs: Dict[str, Any] = field(default_factory=dict)
    extra: Optional[Dict[str, Any]] = None


# ---------- Base (shape validation + hooks) ----------
class ExecutableBase:
    def validate(self, spec: IoSpec, schema: dict, **kwargs) -> None:
        log.debug("[exec.validate] begin | kind=", spec.kind)
        self._validate_shape(spec)
        self.validate_schema(spec, schema, **kwargs)
        log.debug("[exec.validate] ok")

    def validate_schema(self, spec: IoSpec, schema: dict, **kwargs) -> None:
        # subclasses enforce verb/db-map specific rules
        log.debug("[exec.validate_schema] base no-op")
        return

    def _validate_shape(self, spec: IoSpec) -> None:
        log.debug("[exec._validate_shape] checking kind/raw/file/outputs/extra")
        if spec.kind not in ("parser", "pphrase"):
            raise ValueError("IoSpec.kind must be 'parser' or 'pphrase'")

        if not isinstance(spec.raw_folders, list) or not all(isinstance(x, str) for x in spec.raw_folders):
            raise ValueError("IoSpec.raw_folders must be list[str]")
        if not isinstance(spec.file_inputs, list) or not all(isinstance(x, str) for x in spec.file_inputs):
            raise ValueError("IoSpec.file_inputs must be list[str]")
        if not isinstance(spec.outputs, dict):
            raise ValueError("IoSpec.outputs must be a dict")

        if spec.kind == "parser":
            files = spec.outputs.get("files")
            log.debug("[exec._validate_shape][parser] outputs.files=", files)
            if not isinstance(files, list) or not files or not all(isinstance(f, str) and f.strip() for f in files):
                raise ValueError("Parser must declare outputs.files as non-empty list[str]")
        else:  # pphrase
            folder = spec.outputs.get("folder")
            log.debug("[exec._validate_shape][pphrase] outputs.folder=", repr(folder))

            if folder is None:
                # default: no extra subfolder, just canonical base
                spec.outputs["folder"] = None
            elif not isinstance(folder, str):
                raise ValueError("outputs.folder must be a string (or None)")
            else:
                s = folder.strip("/")
                if s and "/" in s:
                    raise ValueError("outputs.folder must be a single segment (no slashes)")
                spec.outputs["folder"] = s if s else None

        if spec.extra is not None and not isinstance(spec.extra, dict):
            raise ValueError("IoSpec.extra must be a dict if provided")

        # Light shape check for optional post_doc
        pd = (spec.extra or {}).get("post_doc")
        if pd is not None:
            log.debug("[exec._validate_shape] post_doc present | keys=", list(pd.keys()))
            if not isinstance(pd, dict):
                raise ValueError("extra.post_doc must be a dict")
            entry = pd.get("entry")
            if not isinstance(entry, str) or ":" not in entry or not entry.strip():
                raise ValueError("extra.post_doc.entry must be 'module.path:callable'")
            if "args" in pd and not isinstance(pd["args"], dict):
                raise ValueError("extra.post_doc.args, if provided, must be a dict")

    def resolve_mounts(self, spec: IoSpec, schema: dict, **kwargs) -> Dict[str, Dict[str, Any]]:
        """
        Return *logical* mount plan (slots only; no real paths).
        {
          "inputs":  "<alias>": {"slot": {...}, "mode": "ro|rw"},
          "outputs": "<alias>|OUTPUT_FOLDER": {"slot": {...}, "mode": "rw"}
        }
        """
        raise NotImplementedError

    @staticmethod
    def assert_logical_mounts(mounts: Dict[str, Dict[str, Any]]) -> None:
        """
        Defensive validator to ensure a mount plan is *logical-only*:
          - top-level keys are 'inputs' and/or 'outputs'
          - each entry has a 'slot' and a 'mode'
          - NO 'path' or 'paths' keys are present (prevents mixing concerns)
        """
        log.debug("[exec.assert_logical] verifying mounts shape...")
        if not isinstance(mounts, dict):
            raise ValueError("mounts must be a dict")

        for top in mounts.keys():
            if top not in ("inputs", "outputs"):
                raise ValueError("mounts must contain only 'inputs' and/or 'outputs'")

        for section in ("inputs", "outputs"):
            entries = mounts.get(section, {})
            if not isinstance(entries, dict):
                raise ValueError(f"mounts['{section}'] must be a dict")

            for alias, entry in entries.items():
                if not isinstance(entry, dict):
                    raise ValueError(f"mounts['{section}']['{alias}'] must be a dict")
                if "slot" not in entry or "mode" not in entry:
                    raise ValueError(f"mounts['{section}']['{alias}'] must contain 'slot' and 'mode'")
                if "path" in entry or "paths" in entry:
                    raise ValueError(
                        f"mounts['{section}']['{alias}'] must not contain 'path' or 'paths' "
                        "(physical resolution belongs to layout_resolver)"
                    )
                slot = entry["slot"]
                if not isinstance(slot, dict) or "kind" not in slot:
                    raise ValueError(f"mounts['{section}']['{alias}'].slot must be a dict with a 'kind' field")
        log.debug("[exec.assert_logical] ok")
