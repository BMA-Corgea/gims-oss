# core/executors/base_executor.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional


@dataclass
class IoSpec:
    """
    Canonical, *pathless* IO spec shared by all executors.

    kind:        "parser" | "pphrase"
    raw_folders: special inputs that are folder-based (backend may pre-digest; policy enforces 1 file per folder)
    file_inputs: exact-file inputs (logical names, not paths)
    outputs:     parser -> {"files":[...]} | pphrase -> {"folder":"..."}
    extra:       optional extensions, e.g.:
                   {"db_inputs":[...]}            # pphrase: RO endpoints from database map
                   {"post_doc": {                 # OPTIONAL host-side, post-container document hook
                       "entry": "pkg.module:func",# import path "module:callable" to run after container
                       "args": {...}              # arbitrary JSON payload passed through unchanged
                   }}
    """
    kind: str                               # "parser" | "pphrase"
    raw_folders: List[str] = field(default_factory=list)
    file_inputs: List[str] = field(default_factory=list)
    outputs: Dict[str, Any] = field(default_factory=dict)
    extra: Optional[Dict[str, Any]] = None  # see docstring above


class ExecutableBase:
    """
    Base class for execution policies. Subclasses must remain *purely logical*:
      - No filesystem Paths or host/container paths here.
      - Emit only logical "slots" (to be resolved by a layout_resolver).
      - Enforce schema/db-map rules in validate_schema().

    Canonical flow:
      1) validate(spec, schema, **kwargs)
         - shape checks (kind-aware, pathless)
         - subclass schema rules via validate_schema(...)
      2) resolve_mounts(spec, schema, **kwargs)
         - returns a *logical* mount plan (no 'path'/'paths' keys)
         - layout/resolution layer later maps slots -> real paths
    """

    # -------- Public entry: shape + subclass schema validation --------
    def validate(self, spec: IoSpec, schema: dict, **kwargs) -> None:
        """
        Validate kind-aware *shape* (no paths), then allow subclasses to enforce
        verb/pphrase-specific schema rules.
        kwargs can carry db_map, context, etc. if a subclass needs them.
        """
        self._validate_shape(spec)
        self._validate_extra_shape(spec.extra)  # light/optional checks for known keys
        self.validate_schema(spec, schema, **kwargs)

    # -------- Subclass hook (schema-aware checks go here) -------------
    def validate_schema(self, spec: IoSpec, schema: dict, **kwargs) -> None:
        """
        Override in subclasses to enforce verb-schema / database-map rules, e.g.:
          - raw_folders subset of verb.data_entry_schema.raw_data_inputs
          - file_inputs whitelist (DataEntry.json, adverbs.json, extras...)
          - parser outputs must match interpretation tabs
          - pphrase db_inputs must exist in local_layout_map.json and have required params

        IMPORTANT: post-doc is intentionally NOT schema-validated here to keep it fully host-side.
        """
        return  # default: no-op

    # -------- Kind-aware shape checks (no I/O, no schema assumptions) -
    def _validate_shape(self, spec: IoSpec) -> None:
        # kind
        if spec.kind not in ("parser", "pphrase"):
            raise ValueError("IoSpec.kind must be 'parser' or 'pphrase'")

        # lists
        if not isinstance(spec.raw_folders, list) or not all(isinstance(x, str) for x in spec.raw_folders):
            raise ValueError("IoSpec.raw_folders must be a list[str]")
        if not isinstance(spec.file_inputs, list) or not all(isinstance(x, str) for x in spec.file_inputs):
            raise ValueError("IoSpec.file_inputs must be a list[str]")

        # outputs (kind-specific)
        if not isinstance(spec.outputs, dict):
            raise ValueError("IoSpec.outputs must be a dict")

        if spec.kind == "parser":
            files = spec.outputs.get("files")
            if not isinstance(files, list) or not files or not all(isinstance(f, str) and f.strip() for f in files):
                raise ValueError("Parser must declare outputs.files as a non-empty list[str]")

        elif spec.kind == "pphrase":
            folder = spec.outputs.get("folder")
            if not isinstance(folder, str) or not folder.strip():
                raise ValueError("Prepositional phrase must declare outputs.folder as non-empty string")
            if not self._is_single_segment(folder):
                raise ValueError("outputs.folder must be a single segment (no slashes)")

        # extra (optional, shape only)
        if spec.extra is not None and not isinstance(spec.extra, dict):
            raise ValueError("IoSpec.extra, if provided, must be a dict")

    def _validate_extra_shape(self, extra: Optional[Dict[str, Any]]) -> None:
        """
        Only shallow validation of EXTRA shape to prevent obvious mistakes.
        - We purposefully do NOT enforce semantics for 'post_doc' beyond type/required field presence.
        """
        if not extra:
            return
        # post_doc: host-side callable specification (free to import anything)
        if "post_doc" in extra:
            pd = extra["post_doc"]
            if not isinstance(pd, dict):
                raise ValueError("extra.post_doc must be a dict")
            entry = pd.get("entry")
            if not isinstance(entry, str) or ":" not in entry or not entry.strip():
                raise ValueError("extra.post_doc.entry must be 'module.path:callable'")
            # args is optional and opaque
            if "args" in pd and not isinstance(pd["args"], dict):
                raise ValueError("extra.post_doc.args, if provided, must be a dict")

    # -------- Utilities (purely logical) ------------------------------
    def _is_single_segment(self, name: str) -> bool:
        s = name.strip("/")
        return ("/" not in s) and (s != "")

    # -------- Mount plan (logical, no paths) --------------------------
    def resolve_mounts(self, spec: IoSpec, schema: dict, **kwargs) -> Dict[str, Dict[str, Any]]:
        """
        Subclasses must return a *logical* mount plan (slots only, no filesystem paths).

        Canonical shape:
          {
            "inputs": {
              "<alias>": { "slot": {...}, "mode": "ro" | "rw" }
            },
            "outputs": {
              "<alias>|OUTPUT_FOLDER": { "slot": {...}, "mode": "rw" }
            }
          }

        'slot' is a dictionary describing the logical resource, e.g.:
          { "kind": "raw_folder", "name": "images" }
          { "kind": "file", "name": "DataEntry.json" }
          { "kind": "data_entry" } / { "kind": "adverbs" } / { "kind": "status" }
          { "kind": "db_endpoint", "endpoint": "noun_items", "params": {...} }
          { "kind": "interpretation", "name": "Results.csv" }
          { "kind": "pphrase_output_root", "pphrase_name": "...", "folder": "Report" }

        NOTE: Do NOT include concrete filesystem keys like 'path' or 'paths' here.
        The layout/resolution layer will later map each {"slot": {...}} to actual host/container paths.
        """
        raise NotImplementedError

    # -------- Optional guard for subclasses/tests ---------------------
    @staticmethod
    def assert_logical_mounts(mounts: Dict[str, Dict[str, Any]]) -> None:
        """
        Defensive validator to ensure a mount plan is *logical-only*:
          - top-level keys are 'inputs' and/or 'outputs'
          - each entry has a 'slot' and a 'mode'
          - NO 'path' or 'paths' keys are present anywhere (prevents mixing concerns)
        """
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
                # hard guard against physical leakage
                if "path" in entry or "paths" in entry:
                    raise ValueError(
                        f"mounts['{section}']['{alias}'] must not contain 'path' or 'paths' "
                        "(physical resolution belongs to layout_resolver)"
                    )
                slot = entry["slot"]
                if not isinstance(slot, dict) or "kind" not in slot:
                    raise ValueError(f"mounts['{section}']['{alias}'].slot must be a dict with a 'kind' field")
