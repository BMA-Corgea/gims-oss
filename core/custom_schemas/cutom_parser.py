# core/executors/custom_parser.py
from __future__ import annotations
from typing import Dict, Any, List, Set
from .base_executor import ExecutableBase, IoSpec
from pathlib import Path

class CustomParserExecutable(ExecutableBase):
    """
    Policy (schema-aware):
      - Raw inputs: folder-based (one file per folder) and MUST be declared in verb.data_entry_schema.raw_data_inputs.
        Backend may pre-digest unfriendly formats before sandbox run.
      - File inputs: exact-file and MUST be in the allowed set derived from the verb schema:
          * DataEntry.json (always)
          * adverbs.json (IFF adverb_schema exists)
          * plus any extra files the schema explicitly allows (optional hook)
      - Interpretation outputs: file-exact and MUST correspond to interpretation tabs declared in the verb schema.
        Each interpretation file is mounted RW and exposed to the tool as BOTH an input and an output path.
    """

    # ------- public API from ExecutableBase --------
    def _validate_outputs(self, spec: IoSpec, schema: dict) -> None:
        files = spec.outputs.get("files", [])
        if not isinstance(files, list) or not files:
            raise ValueError("Parser must declare non-empty outputs.files (list of filenames with extensions)")

        # Enforce that declared outputs correspond to interpretation tabs
        expected_tab_files = self._expected_interpretation_filenames(schema)
        illegal = [f for f in files if self._basename(f) not in expected_tab_files]
        if illegal:
            raise ValueError(
                "Parser outputs must match interpretation tabs. "
                f"Unexpected outputs: {illegal}. Allowed: {sorted(expected_tab_files)}"
            )

    def validate(self, spec: IoSpec, schema: dict) -> None:
        """
        Extend base validation with verb-schema checks:
          - raw_folders ⊆ schema.raw_data_inputs
          - file_inputs ⊆ allowed_file_inputs (DataEntry.json, adverbs.json if present, optional extras)
          - outputs validated to interpretation tabs (see _validate_outputs)
        """
        super().validate(spec, schema)

        # 1) raw folder subset of schema.raw_data_inputs
        declared_raw: Set[str] = set(self._schema_raw_inputs(schema))
        unknown_raw = [rf for rf in spec.raw_folders if rf not in declared_raw]
        if unknown_raw:
            raise ValueError(
                f"Raw folder inputs not declared in verb schema: {unknown_raw}. "
                f"Declared raw_data_inputs: {sorted(declared_raw)}"
            )

        # 2) file inputs whitelist
        allowed_files = self._allowed_file_inputs(schema)
        illegal_files = [fi for fi in spec.file_inputs if fi not in allowed_files]
        if illegal_files:
            raise ValueError(
                f"File inputs not allowed by verb schema: {illegal_files}. "
                f"Allowed: {sorted(allowed_files)}"
            )

        # 3) outputs validated via _validate_outputs (already called by base -> override)
        self._validate_outputs(spec, schema)

    def resolve_mounts(self, spec: IoSpec, schema: dict, **kwargs) -> Dict[str, Dict[str, Any]]:
        mounts = {"inputs": {}, "outputs": {}}
        
        # raw folders (RO)
        for alias in spec.raw_folders:
            mounts["inputs"][alias] = {"slot": {"kind": "raw_folder", "name": alias}, "mode": "ro"}
        
        # file inputs (RO)
        for alias in spec.file_inputs:
            if alias == "DataEntry.json":
                slot = {"kind": "data_entry"}
            elif alias == "adverbs.json":
                slot = {"kind": "adverbs"}
            elif alias == "Status.json":
                slot = {"kind": "status"}
            else:
                slot = {"kind": "file", "name": alias}
            mounts["inputs"][alias] = {"slot": slot, "mode": "ro"}
        
        # interpretation files (RW) mirrored as inputs
        for fname in spec.outputs["files"]:
            label, _ = self._infer_tab_name(fname)
            slot = {"kind": "interpretation", "name": label}
            mounts["outputs"][fname] = {"slot": slot, "mode": "rw"}
            mounts["inputs"][fname] = {"slot": slot, "mode": "rw"}
        
        return mounts


    # ------- helpers (schema-aware) --------
    def _schema_raw_inputs(self, schema: dict) -> List[str]:
        return list(schema.get("data_entry_schema", {}).get("raw_data_inputs", []))

    def _has_adverbs(self, schema: dict) -> bool:
        return bool(schema.get("adverb_schema"))

    def _allowed_file_inputs(self, schema: dict) -> Set[str]:
        allowed = {"DataEntry.json"}
        if self._has_adverbs(schema):
            allowed.add("adverbs.json")
        # Optional: include any additional schema-declared file inputs if you model them
        extra_files = schema.get("data_entry_schema", {}).get("file_inputs", [])
        allowed.update(extra_files if isinstance(extra_files, list) else [])
        # Also allow interpretation files as readable/writable (added when declared as outputs)
        allowed.update(self._expected_interpretation_filenames(schema))
        return allowed

    def _expected_interpretation_filenames(self, schema: dict) -> Set[str]:
        """
        Derive expected CSV basenames from interpretation tabs (strings or dicts).
        Respects explicit 'file'/'filename' if provided; otherwise uses '<Label>.csv'.
        """
        tabs = schema.get("data_entry_schema", {}).get("interpretation", {}).get("tabs", [])
        files: Set[str] = set()
        for t in tabs:
            _label, fname = self._infer_tab_name(t)
            files.add(Path(fname).name)  # keep basename only
        return files

    def _resolve_interpretation_path(self, run_root: Path, fname: str) -> Path:
        """
        Canonical location for interpretation outputs:
          - If fname has a subpath, treat it as relative to run_root.
          - Otherwise, place at run_root / "Interpretation" / fname.
        """
        if "/" in fname:
            return run_root / fname
        return run_root / "Interpretation" / fname

    def _basename(self, path_like: str) -> str:
        return str(Path(path_like).name)

    def _infer_tab_name(self, tab: Any) -> tuple[str, str]:
        """
        Normalize an interpretation tab definition and derive its filename.

        Returns:
        (label, filename_basename)

        Supports:
        - "Results"                        -> ("Results", "Results.csv")
        - {"name": "Results"}              -> ("Results", "Results.csv")
        - {"label": "Results"}             -> ("Results", "Results.csv")
        - {"tab": "Results"}               -> ("Results", "Results.csv")
        - {"name": "Results", "file":"res.csv"} -> ("Results", "res.csv")
        - {"name": "Results", "filename":"res.csv"} -> ("Results", "res.csv")
        """
        # extract a display label
        if isinstance(tab, str):
            label = tab
            file_hint = None
        elif isinstance(tab, dict):
            label = tab.get("name") or tab.get("label") or tab.get("tab") or "Tab"
            file_hint = tab.get("file") or tab.get("filename")
        else:
            label, file_hint = "Tab", None

        # decide filename
        if file_hint:
            fname = Path(file_hint).name  # basename only
        else:
            # default mapping: <Label>.csv (do not slug here; keep parity with existing CSV names)
            fname = f"{label}.csv"

        # ensure we only return a basename
        fname = Path(fname).name
        return label, fname
