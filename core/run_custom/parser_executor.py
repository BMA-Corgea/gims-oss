# core/run_custom/parser_executor.py
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Set, Tuple
from .schema import IoSpec, ExecutableBase
from ._common import log


# ---------- Custom Parser (schema-aware, pathless) ----------
class CustomParserExecutable(ExecutableBase):
    """
    Policy:
      - Raw inputs: folder-based (subset of verb.data_entry_schema.raw_data_inputs); backend may pre-digest.
      - File inputs: from whitelist (DataEntry.json, adverbs.json if adverb_schema, schema extras).
      - Interpretation outputs: must correspond to interpretation tabs; files are RW and mirrored as inputs.
    """
    def validate_schema(self, spec: IoSpec, schema: dict, **kwargs) -> None:
        # raw subset
        log.debug("[parser.validate_schema] begin")
        declared_raw = set(schema.get("data_entry_schema", {}).get("raw_data_inputs", []))
        bad_raw = [rf for rf in spec.raw_folders if rf not in declared_raw]
        log.debug("[parser.validate_schema] declared_raw=", declared_raw, "requested=", spec.raw_folders)
        if bad_raw:
            log.debug("[parser.validate_schema][error] bad_raw=", bad_raw)
            raise ValueError(f"Raw folder inputs not in verb schema: {bad_raw}")

        # file whitelist
        allowed_files = self._allowed_file_inputs(schema)
        bad_files = [fi for fi in spec.file_inputs if fi not in allowed_files]
        log.debug("[parser.validate_schema] allowed_files=", allowed_files, "requested=", spec.file_inputs)
        if bad_files:
            log.debug("[parser.validate_schema][error] bad_files=", bad_files)
            raise ValueError(f"File inputs not allowed by verb schema: {bad_files} (allowed: {sorted(allowed_files)})")

        # outputs must map to interpretation tab filenames (handles string or dict tab defs)
        allowed_filenames = self._expected_interpretation_filenames(schema)
        illegal = [f for f in spec.outputs.get("files", []) if self._basename(f) not in allowed_filenames]
        log.debug("[parser.validate_schema] allowed_interpret=", allowed_filenames, "declared_outputs=", spec.outputs.get("files"))
        if illegal:
            log.debug("[parser.validate_schema][error] illegal_outputs=", illegal)
            raise ValueError(
                "Parser outputs must match interpretation tabs. "
                f"Unexpected outputs: {illegal}. Allowed: {sorted(allowed_filenames)}"
            )
        log.debug("[parser.validate_schema] ok")

    def resolve_mounts(self, spec: IoSpec, schema: dict, **kwargs) -> Dict[str, Dict[str, Any]]:
        log.debug("[parser.resolve_mounts] begin")
        mounts = {"inputs": {}, "outputs": {}}

        # raw folders (RO)
        for alias in spec.raw_folders:
            mounts["inputs"][alias] = {"slot": {"kind": "raw_folder", "name": alias}, "mode": "ro"}
            log.debug("[parser.resolve_mounts][in] raw_folder ->", alias)

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
            log.debug("[parser.resolve_mounts][in] file_input ->", alias, "| slot=", slot)

        # interpretation files (RW) mirrored as inputs
        for fname in spec.outputs["files"]:
            tab, _ = self._infer_tab_name(fname)
            slot = {"kind": "interpretation", "name": tab}
            mounts["outputs"][fname] = {"slot": slot, "mode": "rw"}
            mounts["inputs"][fname]  = {"slot": slot, "mode": "rw"}  # read-before-write
            log.debug("[parser.resolve_mounts][io] interpretation mirror ->", fname, "| tab=", tab)

        log.debug("[parser.resolve_mounts] done | inputs=", list(mounts["inputs"].keys()), "outputs=", list(mounts["outputs"].keys()))
        return mounts

    # helpers
    def _allowed_file_inputs(self, schema: dict) -> Set[str]:
        allowed = {"DataEntry.json"}
        if schema.get("adverb_schema"):
            allowed.add("adverbs.json")
        extras = schema.get("data_entry_schema", {}).get("file_inputs", [])
        if isinstance(extras, list):
            allowed.update(extras)
        log.debug("[parser._allowed_file_inputs] ->", allowed)
        return allowed

    def _expected_interpretation_filenames(self, schema: dict) -> Set[str]:
        """
        From interpretation tabs (strings or dicts), derive allowed CSV basenames.
        Respects explicit 'file'/'filename' if provided; otherwise '<Label>.csv'.
        """
        from pathlib import Path
        tabs = schema.get("data_entry_schema", {}).get("interpretation", {}).get("tabs", [])
        files: Set[str] = set()
        for t in tabs:
            label, fname = self._infer_tab_name(t)
            files.add(Path(fname).name)
            log.debug("[parser._expected_interpretation] tab=", label, "-> file=", fname)
        log.debug("[parser._expected_interpretation] final set ->", files)
        return files

    def _infer_tab_name(self, tab: Any) -> Tuple[str, str]:
        """
        Normalize an interpretation tab definition and derive its filename.
        Returns: (label, filename_basename)
        """
        from pathlib import Path
        if isinstance(tab, str):
            label = tab
            file_hint = None
        elif isinstance(tab, dict):
            label = tab.get("name") or tab.get("label") or tab.get("tab") or "Tab"
            file_hint = tab.get("file") or tab.get("filename")
        else:
            label, file_hint = "Tab", None

        if file_hint:
            fname = Path(file_hint).name  # basename only
        else:
            fname = f"{label}.csv"

        fname = Path(fname).name
        log.debug("[parser._infer_tab_name] tab_def=", tab, "->", (label, fname))
        return label, fname

    def _basename(self, path_like: str) -> str:
        b = Path(str(path_like)).name
        log.debug("[parser._basename] ->", b)
        return b
