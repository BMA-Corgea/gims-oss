# core/core_run_customs.py
# ============================================================
# SECTION 1 — SCHEMA + EXECUTORS (PATHLESS POLICY ONLY)
# ============================================================
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Set, Callable, Tuple
from pathlib import Path
from copy import deepcopy
import ast

#changed my mind, the WASM runtime is frustrating me
from api.manifest.resolver import resolve_path

# ──────────────────────────────────────────────────────────────
# S3-aware FS bridge (fallback to local if json_proxy absent)
# ──────────────────────────────────────────────────────────────
try:
    # Prefer the project’s S3/json_proxy-aware helpers
    from api.i_o import (  # type: ignore
        fs_exists, fs_is_dir, fs_is_file, fs_iterdir, fs_listdir,
        fs_mkdirs, fs_read_bytes, fs_write_bytes,
        fs_open_readbin, fs_open_writebin,
        fs_copy, fs_copytree, fs_remove, fs_rmtree,
        debug as io_debug,
        S3_ENABLED as _S3_ENABLED_FLAG,
    )
    S3_ENABLED = _S3_ENABLED_FLAG
except Exception:
    # Minimal local fallbacks (keep signatures compatible)
    S3_ENABLED = False

    def fs_exists(p: Path) -> bool: return Path(p).exists()
    def fs_is_dir(p: Path) -> bool: return Path(p).is_dir()
    def fs_is_file(p: Path) -> bool: return Path(p).is_file()
    def fs_iterdir(p: Path):         return list(Path(p).iterdir())
    def fs_listdir(p: Path):         return [x.name for x in Path(p).iterdir()]
    def fs_mkdirs(p: Path) -> None:  Path(p).mkdir(parents=True, exist_ok=True)
    def fs_read_bytes(p: Path) -> bytes: return Path(p).read_bytes()
    def fs_write_bytes(p: Path, data: bytes) -> None:
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_bytes(data)
    def fs_open_readbin(p: Path):    return open(p, "rb")
    def fs_open_writebin(p: Path):   # returns a file-like handle
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        return open(p, "wb")
    def fs_copy(src: Path, dst: Path) -> None:
        from shutil import copy2
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        copy2(src, dst)
    def fs_copytree(src: Path, dst: Path) -> None:
        from shutil import copytree
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        copytree(src, dst, dirs_exist_ok=True)
    def fs_remove(p: Path) -> None:
        try:
            Path(p).unlink(missing_ok=True)  # py3.8+: handle as needed if older
        except TypeError:
            try:
                Path(p).unlink()
            except FileNotFoundError:
                pass
    def fs_rmtree(p: Path) -> None:
        from shutil import rmtree
        if Path(p).exists():
            rmtree(p)

    def io_debug(*a, **k):  # no-op fallback
        pass

# Debug control - set to False to disable all backend debug logging
DEBUG_ENABLED = False  # Change to True to enable debug logs

def debug(*args, **kwargs):
    """Debug print that respects DEBUG_ENABLED flag."""
    if DEBUG_ENABLED:
        print(*args, **kwargs)
    # also mirror into i_o debug channel if available
    try:
        io_debug(*args, **kwargs)
    except Exception:
        pass

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
        debug("[exec.validate] begin | kind=", spec.kind)
        self._validate_shape(spec)
        self.validate_schema(spec, schema, **kwargs)
        debug("[exec.validate] ok")

    def validate_schema(self, spec: IoSpec, schema: dict, **kwargs) -> None:
        # subclasses enforce verb/db-map specific rules
        debug("[exec.validate_schema] base no-op")
        return

    def _validate_shape(self, spec: IoSpec) -> None:
        debug("[exec._validate_shape] checking kind/raw/file/outputs/extra")
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
            debug("[exec._validate_shape][parser] outputs.files=", files)
            if not isinstance(files, list) or not files or not all(isinstance(f, str) and f.strip() for f in files):
                raise ValueError("Parser must declare outputs.files as non-empty list[str]")
        else:  # pphrase
            folder = spec.outputs.get("folder")
            debug("[exec._validate_shape][pphrase] outputs.folder=", repr(folder))

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
            debug("[exec._validate_shape] post_doc present | keys=", list(pd.keys()))
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
        debug("[exec.assert_logical] verifying mounts shape...")
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
        debug("[exec.assert_logical] ok")


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
        debug("[parser.validate_schema] begin")
        declared_raw = set(schema.get("data_entry_schema", {}).get("raw_data_inputs", []))
        bad_raw = [rf for rf in spec.raw_folders if rf not in declared_raw]
        debug("[parser.validate_schema] declared_raw=", declared_raw, "requested=", spec.raw_folders)
        if bad_raw:
            debug("[parser.validate_schema][error] bad_raw=", bad_raw)
            raise ValueError(f"Raw folder inputs not in verb schema: {bad_raw}")

        # file whitelist
        allowed_files = self._allowed_file_inputs(schema)
        bad_files = [fi for fi in spec.file_inputs if fi not in allowed_files]
        debug("[parser.validate_schema] allowed_files=", allowed_files, "requested=", spec.file_inputs)
        if bad_files:
            debug("[parser.validate_schema][error] bad_files=", bad_files)
            raise ValueError(f"File inputs not allowed by verb schema: {bad_files} (allowed: {sorted(allowed_files)})")

        # outputs must map to interpretation tab filenames (handles string or dict tab defs)
        allowed_filenames = self._expected_interpretation_filenames(schema)
        illegal = [f for f in spec.outputs.get("files", []) if self._basename(f) not in allowed_filenames]
        debug("[parser.validate_schema] allowed_interpret=", allowed_filenames, "declared_outputs=", spec.outputs.get("files"))
        if illegal:
            debug("[parser.validate_schema][error] illegal_outputs=", illegal)
            raise ValueError(
                "Parser outputs must match interpretation tabs. "
                f"Unexpected outputs: {illegal}. Allowed: {sorted(allowed_filenames)}"
            )
        debug("[parser.validate_schema] ok")

    def resolve_mounts(self, spec: IoSpec, schema: dict, **kwargs) -> Dict[str, Dict[str, Any]]:
        debug("[parser.resolve_mounts] begin")
        mounts = {"inputs": {}, "outputs": {}}

        # raw folders (RO)
        for alias in spec.raw_folders:
            mounts["inputs"][alias] = {"slot": {"kind": "raw_folder", "name": alias}, "mode": "ro"}
            debug("[parser.resolve_mounts][in] raw_folder ->", alias)

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
            debug("[parser.resolve_mounts][in] file_input ->", alias, "| slot=", slot)

        # interpretation files (RW) mirrored as inputs
        for fname in spec.outputs["files"]:
            tab, _ = self._infer_tab_name(fname)
            slot = {"kind": "interpretation", "name": tab}
            mounts["outputs"][fname] = {"slot": slot, "mode": "rw"}
            mounts["inputs"][fname]  = {"slot": slot, "mode": "rw"}  # read-before-write
            debug("[parser.resolve_mounts][io] interpretation mirror ->", fname, "| tab=", tab)

        debug("[parser.resolve_mounts] done | inputs=", list(mounts["inputs"].keys()), "outputs=", list(mounts["outputs"].keys()))
        return mounts

    # helpers
    def _allowed_file_inputs(self, schema: dict) -> Set[str]:
        allowed = {"DataEntry.json"}
        if schema.get("adverb_schema"):
            allowed.add("adverbs.json")
        extras = schema.get("data_entry_schema", {}).get("file_inputs", [])
        if isinstance(extras, list):
            allowed.update(extras)
        debug("[parser._allowed_file_inputs] ->", allowed)
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
            debug("[parser._expected_interpretation] tab=", label, "-> file=", fname)
        debug("[parser._expected_interpretation] final set ->", files)
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
        debug("[parser._infer_tab_name] tab_def=", tab, "->", (label, fname))
        return label, fname

    def _basename(self, path_like: str) -> str:
        b = Path(str(path_like)).name
        debug("[parser._basename] ->", b)
        return b


# ============================================================
# PREPHRASE EXPANSION (PURE, NO I/O) — put this in core
# ============================================================

# ---------- Prepositional Phrase (db-map aware, pathless, no I/O) ----------
class PrepositionalPhraseExecutable(ExecutableBase):
    """
    Policy (PURE / NO I/O in this class):
      - READ side: declares logical inputs (raw folders, file inputs, db-endpoints).
                   No filesystem access is performed here.
      - WRITE side: declares exactly one logical output folder.
      - CANONICAL BASE: All physical writes for this pphrase MUST be rooted under the
                        canonical phrase base:
                            CANON_BASE = resolve_path(project_path, "prepositional_phrase_output_dir")
                            PHRASE_ROOT = CANON_BASE / {pphrase_name}
                            OUTPUT_ROOT = PHRASE_ROOT / spec.outputs.folder
                        This class does not touch the filesystem; it *computes and enforces*
                        these expectations via pure checks and plan objects.

    Expected kwargs (passed to validate_schema/resolve_mounts/plan_post_doc):
      - context: dict with "pphrase_name" (required)
      - canonical_phrase_base: Path  # typically resolve_path(project_path, "prepositional_phrase_output_dir")
      - db_map: Optional[dict]       # required if using extra.db_inputs
    """

    # ------------------------ validation (no I/O) ------------------------
    def validate_schema(self, spec: IoSpec, schema: dict, **kwargs) -> None:
        """
        Validate a prepositional phrase IoSpec against schema and db_map.

        Pure logic only:
        - Requires context.pphrase_name
        - Requires canonical_phrase_base (must be passed in, not auto-resolved)
        - Validates raw_folders and file_inputs against verb schema
        - Validates db_inputs against db_map (dict only, no file I/O)
        - Computes canonical phrase/output roots (pure path math)
        """
        from pathlib import Path

        context: Dict[str, Any] = kwargs.get("context") or {}
        db_map = kwargs.get("db_map")

        # Accept canonical base from kwargs OR from context
        canonical_phrase_base: Optional[Path] = (
            kwargs.get("canonical_phrase_base")
            or context.get("canonical_phrase_base")
        )

        debug("[pphrase.validate_schema] begin")

        # --- Required context ---
        pphrase_name = context.get("pphrase_name")
        if not isinstance(pphrase_name, str) or not pphrase_name.strip():
            raise ValueError("context.pphrase_name is required for prepositional phrases")

        # --- Canonical base must already be injected ---
        if not isinstance(canonical_phrase_base, Path):
            raise ValueError(
                "canonical_phrase_base (Path) is required and must be provided by caller. "
                "No auto-resolve or file I/O in core validate."
            )

        # --- Raw subset check ---
        declared_raw = set(schema.get("data_entry_schema", {}).get("raw_data_inputs", []))
        bad_raw = [rf for rf in spec.raw_folders if rf not in declared_raw]
        if bad_raw:
            debug("[pphrase.validate_schema][error] bad_raw=", bad_raw)
            raise ValueError(f"Raw folder inputs not in verb schema: {bad_raw}")

        # --- File whitelist check ---
        allowed_files = self._allowed_file_inputs(schema)
        bad_files = [fi for fi in spec.file_inputs if fi not in allowed_files]
        debug("[pphrase.validate_schema] allowed_files=", allowed_files, "requested=", spec.file_inputs)
        if bad_files:
            debug("[pphrase.validate_schema][error] bad_files=", bad_files)
            raise ValueError(f"File inputs not allowed by verb schema: {bad_files} (allowed: {sorted(allowed_files)})")

        # --- DB-map validation ---
        db_inputs = (spec.extra or {}).get("db_inputs", [])
        if db_inputs:
            if not isinstance(db_map, dict) or not db_map:
                raise ValueError("Database map (e.g., local_layout_map.json) is required to validate db_inputs")
            for i, item in enumerate(db_inputs, 1):
                endpoint = item.get("endpoint")
                params   = item.get("params", {})
                debug(f"[pphrase.validate_schema] db_inputs[{i}] endpoint={endpoint} params={list(params.keys())}")
                if endpoint not in db_map:
                    raise ValueError(f"db_inputs[{i}] unknown endpoint '{endpoint}'")

                # find required placeholders for this endpoint
                required = self._placeholders(db_map[endpoint])

                # special-case: some placeholders (like run_id) are resolved later
                deferred_allowed = {"run_id"} if endpoint == "data_dump_dir" else set()

                missing = [k for k in required if k not in params and k not in deferred_allowed]
                if missing:
                    raise ValueError(f"db_inputs[{i}] missing params {missing} for endpoint '{endpoint}'")

        # --- Compute canonical roots (pure path math only) ---
        phrase_root, output_root = self._compute_canonical_paths(
            canonical_phrase_base, str(pphrase_name), spec.outputs.get("folder")
        )
        self._canonical_phrase_root = phrase_root
        self._canonical_output_root = output_root

        debug("[pphrase.validate_schema] ok | phrase_root=", str(phrase_root), "output_root=", str(output_root))

    # ------------------------ logical mounts (no I/O) ------------------------
    def resolve_mounts(self, spec: IoSpec, schema: dict, **kwargs) -> Dict[str, Dict[str, Any]]:
        context: Dict[str, Any] = kwargs.get("context") or {}
        debug("[pphrase.resolve_mounts] begin | folder=", spec.outputs.get("folder"),
              "ctx.pphrase_name=", context.get("pphrase_name"))

        mounts: Dict[str, Dict[str, Any]] = {"inputs": {}, "outputs": {}}

        # A) raw folders (RO)
        for alias in spec.raw_folders:
            mounts["inputs"][alias] = {"slot": {"kind": "raw_folder", "name": alias}, "mode": "ro"}
            debug("[pphrase.resolve_mounts][in] raw_folder ->", alias)

        # B) file inputs (RO)
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
            debug("[pphrase.resolve_mounts][in] file_input ->", alias, "| slot=", slot)

        # C) db-map endpoints (RO)
        for entry in (spec.extra or {}).get("db_inputs", []):
            alias = entry.get("alias") or entry["endpoint"]
            mounts["inputs"][alias] = {
                "slot": {"kind": "db_endpoint", "endpoint": entry["endpoint"], "params": entry.get("params", {})},
                "mode": "ro",
            }
            debug("[pphrase.resolve_mounts][in] db_endpoint ->", alias, "| params_keys=", list(entry.get("params", {}).keys()))

        # D) output folder (RW) under canonical phrase root (expressed logically)
        folder_name = spec.outputs["folder"]
        mounts["outputs"]["OUTPUT_FOLDER"] = {
            "slot": {
                "kind": "pphrase_output_root",
                "pphrase_name": context.get("pphrase_name"),
                "folder": folder_name,  # single segment (shape-checked in ExecutableBase)
            },
            "mode": "rw",
        }
        debug("[pphrase.resolve_mounts] done")
        return mounts

    # ------------------------ post-doc planning (no I/O) ------------------------
    def plan_post_doc(
        self,
        spec: IoSpec,
        *,
        project_path: Path,
        context: Dict[str, Any],
        canonical_phrase_base: Path,
    ) -> Optional[Dict[str, Any]]:
        """
        Pure plan for host-side post-doc execution (no imports, no calls, no mkdirs).
        Returns:
          None if no post_doc declared
          OR dict {
            "entry": "module.path:callable",
            "kwargs": {
              "output_root": str,     # projects/.../prepositional phrases/{pphrase}/<folder>
              "phrase_root": str,     # projects/.../prepositional phrases/{pphrase}
              "project_path": str,    # absolute project root path
              "context": dict,        # pass-through
              ...                     # plus any static args from spec.extra.post_doc.args
            },
            "canonical": {
              "phrase_root": str,
              "output_root": str
            }
          }
        """
        pd = (getattr(spec, "extra", None) or {}).get("post_doc")
        if not isinstance(pd, dict):
            debug("[pphrase.plan_post_doc] no post_doc")
            return None

        pphrase_name = (context or {}).get("pphrase_name")
        if not isinstance(pphrase_name, str) or not pphrase_name.strip():
            raise ValueError("context.pphrase_name is required to plan post_doc")

        phrase_root, output_root = self._compute_canonical_paths(
            canonical_phrase_base, pphrase_name, spec.outputs.get("folder")
        )

        entry = pd.get("entry")
        if not isinstance(entry, str) or ":" not in entry or not entry.strip():
            raise ValueError("post_doc.entry must be 'module.path:callable'")

        args = dict(pd.get("args") or {})

        plan = {
            "entry": entry,
            "kwargs": {
                "output_root": str(output_root),
                "phrase_root": str(phrase_root),
                "project_path": str(Path(project_path).resolve()),
                "context": context or {},
                **args,
            },
            "canonical": {
                "phrase_root": str(phrase_root),
                "output_root": str(output_root),
            },
        }
        debug("[pphrase.plan_post_doc] prepared plan | entry=", entry)
        return plan

    # ------------------------ enforcement helpers (no I/O) ------------------------
    @staticmethod
    def assert_physical_output_root_within_canonical(
        physical_output_root: Path,
        *,
        canonical_phrase_base: Path,
        pphrase_name: str,
        output_folder: str,
    ) -> None:
        """
        Pure guard for runners: ensure the resolved physical OUTPUT_FOLDER
        is inside the canonical base (no traversal outside).
        Raises ValueError if the invariant is violated.
        """
        # Compute expected canonical roots (pure)
        phrase_root = Path(canonical_phrase_base).resolve() / pphrase_name
        expected_output_root = (phrase_root / (output_folder or "")).resolve()

        if not PrepositionalPhraseExecutable._is_within(phrase_root, physical_output_root):
            raise ValueError(
                f"OUTPUT_FOLDER escapes phrase root:\n"
                f"  physical: {physical_output_root}\n"
                f"  phrase_root: {phrase_root}"
            )
        if PrepositionalPhraseExecutable._normalize(physical_output_root) != PrepositionalPhraseExecutable._normalize(expected_output_root):
            # Allow stricter equality to the expected root
            raise ValueError(
                f"OUTPUT_FOLDER mismatch:\n"
                f"  physical: {physical_output_root}\n"
                f"  expected: {expected_output_root}"
            )

    # ------------------------ small pure helpers ------------------------
    def _allowed_file_inputs(self, schema: dict) -> Set[str]:
        allowed: Set[str] = {"DataEntry.json"}
        if schema.get("adverb_schema"):
            allowed.add("adverbs.json")
        extras = schema.get("data_entry_schema", {}).get("file_inputs", [])
        if isinstance(extras, list):
            allowed.update(extras)
        debug("[pphrase._allowed_file_inputs] ->", allowed)
        return allowed

    def _placeholders(self, template: str) -> List[str]:
        out: List[str] = []
        buf: List[str] = []
        inside = False
        for ch in template or "":
            if ch == "{":
                inside, buf = True, []
            elif ch == "}" and inside:
                inside = False
                out.append("".join(buf))
                buf = []
            elif inside:
                buf.append(ch)
        debug("[pphrase._placeholders] template=", template, "->", out)
        return out

    @staticmethod
    def _compute_canonical_paths(canonical_phrase_base: Path, pphrase_name: str, output_folder: Optional[str]) -> Tuple[Path, Path]:
        """
        PURE: compute PHRASE_ROOT and OUTPUT_ROOT without touching the filesystem.
        """
        if not isinstance(canonical_phrase_base, Path):
            raise ValueError("canonical_phrase_base must be a Path")
        if not isinstance(pphrase_name, str) or not pphrase_name.strip():
            raise ValueError("pphrase_name must be a non-empty string")
        
        # Handle None case - output directly to phrase root
        if output_folder is None:
            phrase_root = (canonical_phrase_base.resolve() / pphrase_name).resolve()
            output_root = phrase_root  # No subfolder, just use phrase root
            return phrase_root, output_root
        
        # Handle string case with existing validation
        if not isinstance(output_folder, str) or not output_folder.strip() or "/" in output_folder.strip("/"):
            raise ValueError("outputs.folder must be a single non-empty segment (no slashes) or None")
        
        phrase_root = (canonical_phrase_base.resolve() / pphrase_name).resolve()
        output_root = (phrase_root / output_folder.strip("/")).resolve()
        return phrase_root, output_root

    @staticmethod
    def _is_within(base: Path, target: Path) -> bool:
        try:
            target.resolve().relative_to(base.resolve())
            return True
        except Exception:
            return False

    @staticmethod
    def _normalize(p: Path) -> str:
        return str(p.resolve())

try:
    debug  # type: ignore
except NameError:  # pragma: no cover
    def debug(*args, **kwargs):
        print(*args)

_SUP_OPS = {"in", "=", "!=", "contains", "between", "has_pair", "exists", "missing"}

def expand_prephrase_settings_dynamic(
    settings: List[Dict[str, Any]],
    user_values: Optional[Dict[str, Any]] = None,
    *,
    fetch_noun_schema: Callable[[str], Optional[Dict[str, Any]]],
    fetch_noun_items: Callable[[str], List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    PURE expander: interprets dynamic 'options' dicts without performing I/O itself.

    DI (dependency injection):
      - fetch_noun_schema(noun_name) -> schema dict or None
      - fetch_noun_items(noun_name)  -> list[dict] rows

    Steps per field (kind in {single,multi} and options is dict):
      - source = "noun: <Type>" only (others -> [])
      - read primary_id_field from noun schema (fallback to "id")
      - optionally gate to 'complete' rows (all required fields non-empty)
      - apply filters (NO-OP on empty inputs)
      - dedupe (unique_by or primary_id_field)
      - sort (pre-map by row fields; post-map by label/value)
      - map label/value (format strings)
      - limit
    """
    debug("[expand] begin | fields=", len(settings))
    out = deepcopy(settings)
    uv = user_values or {}

    for i, field in enumerate(out, 1):
        fid = field.get("id")
        fkind = (field.get("kind") or "").lower()
        debug(f"[expand] field[{i}] id={fid!r} kind={fkind!r}")

        if fkind not in {"single", "multi"}:
            debug(f"[expand] field[{i}] skip (kind not single/multi)")
            continue

        options = field.get("options")
        if not isinstance(options, dict):
            debug(f"[expand] field[{i}] options not dynamic dict -> leave as-is")
            continue

        source = options.get("source")
        debug(f"[expand] field[{i}] dynamic source={source!r}")
        if not source or not isinstance(source, str):
            debug(f"[expand][warn] field[{i}] missing/invalid source -> options=[]")
            field["options"] = []
            continue

        if not source.lower().startswith("noun:"):
            debug(f"[expand] field[{i}] unsupported source {source!r} -> options=[]")
            field["options"] = []
            continue

        noun_type = source.split(":", 1)[1].strip()
        debug(f"[expand] field[{i}] noun_type={noun_type!r}")

        # -- Load noun schema/items via DI --
        noun_schema = fetch_noun_schema(noun_type)
        if not noun_schema:
            debug(f"[expand][error] field[{i}] noun schema not found -> options=[]")
            field["options"] = []
            continue

        primary_id = noun_schema.get("primary_id_field") or "id"
        required_fields = _extract_required_fields(noun_schema)
        debug(f"[expand] field[{i}] primary_id={primary_id!r} required_fields={required_fields}")

        try:
            rows = fetch_noun_items(noun_type)  # list[dict]
            debug(f"[expand] field[{i}] loaded {len(rows)} noun item(s)")
        except Exception as e:
            debug(f"[expand][error] field[{i}] fetch_noun_items failed: {e}")
            field["options"] = []
            continue

        # -- Complete gate --
        complete_flag = bool(options.get("complete", False))
        if complete_flag:
            before = len(rows)
            rows = [r for r in rows if _row_is_complete(r, required_fields)]
            debug(f"[expand] field[{i}] complete=True | {before} -> {len(rows)}")
        else:
            debug(f"[expand] field[{i}] complete=False (skip gate)")

        # -- Filters --
        filters = options.get("filters", [])
        debug(f"[expand] field[{i}] filters_present={isinstance(filters, list)} count={len(filters) if isinstance(filters, list) else 0}")
        if isinstance(filters, list):
            for k, flt in enumerate(filters, 1):
                debug(f"[expand] field[{i}] filter[{k}] raw={flt!r}")
                rows = _apply_filter(rows, flt, uv, i, k)

        # -- Dedupe --
        allow_dup = bool(options.get("allow_duplicates", False))
        unique_by = options.get("unique_by")
        if allow_dup:
            debug(f"[expand] field[{i}] allow_duplicates=True (skip dedupe)")
        else:
            if isinstance(unique_by, list) and all(isinstance(x, str) for x in unique_by):
                before = len(rows)
                rows = _dedupe_rows_by_keys(rows, unique_by)
                debug(f"[expand] field[{i}] dedupe by {unique_by} | {before} -> {len(rows)}")
            elif isinstance(primary_id, str) and primary_id:
                before = len(rows)
                rows = _dedupe_rows_by_keys(rows, [primary_id])
                debug(f"[expand] field[{i}] dedupe by primary_id={primary_id!r} | {before} -> {len(rows)}")
            else:
                debug(f"[expand] field[{i}] no unique_by and no primary_id; skipping dedupe")

        # -- Sort (rows pre-map) --
        sort_specs = options.get("sort", [])
        if isinstance(sort_specs, list) and sort_specs:
            row_sorts, opt_sorts = _partition_sort_specs(sort_specs)
            if row_sorts:
                debug(f"[expand] field[{i}] row sort specs -> {row_sorts}")
                rows = _sort_rows(rows, row_sorts)
                debug(f"[expand] field[{i}] rows sorted (pre-map)")
        else:
            opt_sorts = []

        # -- Map label/value (with _runID passthrough) --
        map_spec = options.get("map") or {}
        label_tpl = map_spec.get("label", "{"+(primary_id or "id")+"}")
        value_tpl = map_spec.get("value", "{"+(primary_id or "id")+"}")
        debug(f"[expand] field[{i}] map label={label_tpl!r} value={value_tpl!r}")

        options_list: List[Dict[str, Any]] = []
        for ridx, row in enumerate(rows, 1):
            label = _format_template(label_tpl, row)
            value = _format_template(value_tpl, row)
            opt: Dict[str, Any] = {"label": label, "value": value}
            if "_runID" in row:
                opt["_runID"] = row["_runID"]
                debug(f"[expand] field[{i}] row[{ridx}] _runID={row['_runID']!r} -> option")
            options_list.append(opt)
        debug(f"[expand] field[{i}] mapped {len(options_list)} option(s)")

        # -- Sort (options post-map) --
        if opt_sorts:
            debug(f"[expand] field[{i}] option sort specs -> {opt_sorts}")
            options_list = _sort_options(options_list, opt_sorts)
            debug(f"[expand] field[{i}] options sorted (post-map)")

        # -- Limit --
        limit = options.get("limit")
        if isinstance(limit, int) and limit >= 0:
            before = len(options_list)
            options_list = options_list[:limit]
            debug(f"[expand] field[{i}] limit={limit} | {before} -> {len(options_list)}")
        else:
            debug(f"[expand] field[{i}] no/invalid limit -> keep {len(options_list)}")

        field["options"] = options_list
        debug(f"[expand] field[{i}] done | options_len={len(options_list)}")

    debug("[expand] done")
    return out


# --------------------------
# Helpers (pure)
# --------------------------

def _extract_required_fields(noun_schema: Dict[str, Any]) -> List[str]:
    fields = noun_schema.get("fields", {}) or {}
    req = [name for name, meta in fields.items() if isinstance(meta, dict) and meta.get("required") is True]
    debug("[expand.helpers] required_fields ->", req)
    return req

def _is_nonempty(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) > 0
    return bool(v)

def _row_is_complete(row: Dict[str, Any], required_fields: List[str]) -> bool:
    for f in required_fields:
        if not _is_nonempty(row.get(f)):
            return False
    return True

def _get_ref_values(flt: Dict[str, Any], user_values: Dict[str, Any]) -> Any:
    ref = flt.get("ref")
    if ref is None:
        return None
    if isinstance(ref, str):
        val = user_values.get(ref)
        debug(f"[expand.helpers] ref={ref!r} -> {val!r}")
        return val
    if isinstance(ref, list):
        vals = [user_values.get(r) for r in ref]
        debug(f"[expand.helpers] ref(list)={ref!r} -> {vals!r}")
        return vals
    debug(f"[expand.helpers] ref invalid type -> {type(ref).__name__}")
    return None

def _should_apply(op: str, value: Any) -> bool:
    if op == "in":
        return isinstance(value, (list, tuple, set)) and len(value) > 0
    if op == "between":
        if isinstance(value, (list, tuple)) and len(value) == 2:
            lo, hi = value
            return _is_nonempty(lo) or _is_nonempty(hi)
        return False
    if op in {"contains", "=", "!="}:
        return _is_nonempty(value)
    if op in {"exists", "missing", "has_pair"}:
        return True
    return False

def _apply_filter(rows: List[Dict[str, Any]], flt: Dict[str, Any], uv: Dict[str, Any], fi: int, ki: int) -> List[Dict[str, Any]]:
    op = flt.get("op")
    field = flt.get("field")
    val  = flt.get("value", None)
    refv = _get_ref_values(flt, uv)
    value = refv if refv is not None else val

    if op not in _SUP_OPS:
        debug(f"[expand][warn] field[{fi}] filter[{ki}] unsupported op={op!r} -> NO-OP")
        return rows

    if op in {"in", "=", "!=", "contains", "between"} and not field:
        debug(f"[expand][warn] field[{fi}] filter[{ki}] op={op!r} missing 'field' -> NO-OP")
        return rows

    if not _should_apply(op, value):
        debug(f"[expand] field[{fi}] filter[{ki}] NO-OP (empty value) | op={op!r} value={value!r}")
        return rows

    before = len(rows)
    debug(f"[expand] field[{fi}] filter[{ki}] apply | op={op!r} field={field!r} value={value!r} rows={before}")

    if op == "exists":
        target = True if flt.get("value", None) is None else bool(flt["value"])
        rows = [r for r in rows if (_is_nonempty(r.get(field)) if target else not _is_nonempty(r.get(field)))]
    elif op == "missing":
        rows = [r for r in rows if not _is_nonempty(r.get(field))]
    elif op == "in":
        s = set(value)  # type: ignore[arg-type]
        rows = [r for r in rows if r.get(field) in s]
    elif op == "=":
        rows = [r for r in rows if r.get(field) == value]
    elif op == "!=":
        rows = [r for r in rows if r.get(field) != value]
    elif op == "contains":
        needle = str(value).lower()
        rows = [r for r in rows if needle in str(r.get(field, "")).lower()]
    elif op == "between":
        lo, hi = value if isinstance(value, (list, tuple)) and len(value) == 2 else (None, None)
        def in_range(x):
            if not _is_nonempty(x): return False
            sx = str(x)
            ok_lo = (not _is_nonempty(lo)) or (sx >= str(lo))
            ok_hi = (not _is_nonempty(hi)) or (sx <= str(hi))
            return ok_lo and ok_hi
        rows = [r for r in rows if in_range(r.get(field))]
    elif op == "has_pair":
        keys = value if isinstance(value, (list, tuple)) else []
        rows = [r for r in rows if all(_is_nonempty(r.get(k)) for k in keys)]

    after = len(rows)
    debug(f"[expand] field[{fi}] filter[{ki}] done | {before} -> {after}")
    return rows

def _dedupe_rows_by_keys(rows: List[Dict[str, Any]], keys: List[str]) -> List[Dict[str, Any]]:
    seen: set[Tuple[Any, ...]] = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
        sig = tuple(r.get(k) for k in keys)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(r)
    return out

def _partition_sort_specs(sort_specs: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    row_sorts, opt_sorts = [], []
    for s in sort_specs:
        fld = s.get("field")
        if fld in {"label", "value"}:
            opt_sorts.append(s)
        else:
            row_sorts.append(s)
    return row_sorts, opt_sorts

def _sort_key(x: Any) -> Tuple[int, str]:
    if x is None:
        return (0, "")
    return (1, str(x))

def _sort_rows(rows: List[Dict[str, Any]], sort_specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for s in reversed(sort_specs):
        fld = s.get("field")
        reverse = s.get("dir", "asc") == "desc"
        rows.sort(key=lambda r: _sort_key(r.get(fld)), reverse=reverse)
    return rows

def _sort_options(options: List[Dict[str, Any]], sort_specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for s in reversed(sort_specs):
        fld = s.get("field")
        reverse = s.get("dir", "asc") == "desc"
        options.sort(key=lambda o: _sort_key(o.get(fld)), reverse=reverse)
    return options

class _SafeDict(dict):
    def __missing__(self, key):  # allows "{missing}" -> ""
        return ""

def _format_template(tpl: str, row: Dict[str, Any]) -> str:
    try:
        return tpl.format_map(_SafeDict({k: "" if v is None else v for k, v in row.items()}))
    except Exception:
        return str(row)

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
    debug("[probe_static] begin | module=", str(mp))
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
                debug(f"[probe_static] PREPHRASE_SETTINGS literal ok (len={len(val)})")
            else:
                requires_import = True
                debug("[probe_static] PREPHRASE_SETTINGS non-literal; requires import")

        # --- TOOL_KIND (plain assignment) ---
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOL_KIND" for t in node.targets):
            lit = _lit(node.value)
            if isinstance(lit, str):
                tool_kind = lit
                debug("[probe_static] TOOL_KIND literal =", tool_kind)

        # --- TOOL_KIND (annotated assignment) ---
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "TOOL_KIND":
            lit = _lit(node.value)
            if isinstance(lit, str):
                tool_kind = lit
                debug("[probe_static] TOOL_KIND (annotated) literal =", tool_kind)

        # --- TOOL_VERSION (plain assignment) ---
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOL_VERSION" for t in node.targets):
            lit = _lit(node.value)
            if isinstance(lit, str):
                tool_version = lit
                debug("[probe_static] TOOL_VERSION literal =", tool_version)

        # --- TOOL_VERSION (annotated assignment) ---
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "TOOL_VERSION":
            lit = _lit(node.value)
            if isinstance(lit, str):
                tool_version = lit
                debug("[probe_static] TOOL_VERSION (annotated) literal =", tool_version)

        # --- TOOL dict / IoSpec fallback ---
        if tool_kind is None and isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOL" for t in node.targets):
            if isinstance(node.value, ast.Dict):
                lit = _lit(node.value)
                if isinstance(lit, dict):
                    tool_kind = str(lit.get("kind")) if lit.get("kind") is not None else None
                    debug("[probe_static] TOOL dict literal ok | kind=", tool_kind)
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
                    debug("[probe_static] TOOL IoSpec(...) literal args ok | kind=", tool_kind)

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
    debug("[probe_static] ok | dyn_sources=", dyn)
    return out

# ============================================================
# SECTION 1.6 — DERIVE RUN IDS FROM SELECTED SAMPLES (pphrase)
# ============================================================
def collect_run_ids_from_samples(
    project_path: Path,
    prephrase_settings: List[Dict[str, Any]],
    params: Dict[str, Any],
) -> List[str]:
    """
    Best-effort helper that maps user-selected sample IDs (from PREPHRASE_SETTINGS fields
    with dynamic 'source': 'noun: ...') to the run IDs recorded in each noun's items.jsonl
    under the '_runID' key. No noun schema is required.
    """
    try:
        # Map field.id -> noun_type (only for dynamic noun sources)
        field_to_noun: Dict[str, str] = {}
        for f in prephrase_settings or []:
            if not isinstance(f, dict):
                continue
            fid = f.get("id")
            opts = f.get("options")
            if not fid or not isinstance(opts, dict):
                continue
            src = opts.get("source")
            if isinstance(src, str) and src.lower().startswith("noun:"):
                noun = src.split(":", 1)[1].strip()
                if noun:
                    field_to_noun[str(fid)] = noun

        if not field_to_noun:
            debug("[runids] no dynamic noun sources found in PREPHRASE_SETTINGS")
            return []

        run_ids: set[str] = set()

        # NOTE: We expect DI to provide fetch_noun_items outside this function,
        # or the caller can patch it in the context. This function remains FS-neutral.
        from typing import Callable as _Callable  # just for clarity

        def _fetch(noun_type: str) -> List[Dict[str, Any]]:
            # The caller should monkey-patch or inject this in the outer orchestration if needed.
            # We keep this function here as a placeholder to make intent explicit.
            raise RuntimeError("collect_run_ids_from_samples: fetch_noun_items not injected")

        for fid, noun_type in field_to_noun.items():
            selected = params.get(fid)
            if selected is None:
                continue
            # normalize to list[str]
            if isinstance(selected, str):
                wanted = [selected]
            elif isinstance(selected, (list, tuple, set)):
                wanted = [str(x) for x in selected]
            else:
                continue
            if not wanted:
                continue

            rows = []  # would come from injected fetch function
            try:
                rows = _fetch(noun_type)  # type: ignore[misc]
            except Exception:
                # Leave empty; caller-level variant (_collect_run_ids_from_context) handles DI properly
                pass

            if not rows:
                continue

            keys_all = set().union(*(r.keys() for r in rows))
            cand_keys = []
            if "Sample ID" in keys_all:
                cand_keys.append("Sample ID")
            if "id" in keys_all:
                cand_keys.append("id")
            for k in keys_all:
                kl = str(k).lower()
                if k not in cand_keys and (kl.endswith("id") or k.endswith(" ID")):
                    cand_keys.append(k)

            indices: Dict[str, Dict[str, Dict[str, Any]]] = {k: {} for k in cand_keys}
            for r in rows:
                for k in cand_keys:
                    v = r.get(k)
                    if v is not None:
                        indices[k][str(v)] = r

            for w in wanted:
                matched: Optional[Dict[str, Any]] = None
                for k in cand_keys:
                    if w in indices[k]:
                        matched = indices[k][w]
                        break
                if matched and "_runID" in matched and matched["_runID"]:
                    run_ids.add(str(matched["_runID"]))
                else:
                    debug(f"[runids][miss] {noun_type} value={w!r} -> no _runID")

        out = sorted(run_ids)
        debug("[runids] derived run_ids:", out)
        return out
    except Exception as e:
        debug("[runids][error]", repr(e))
        return []


# ============================================================
# SECTION 2 — PRE-DIGESTION ADAPTERS (OPTIONAL HEAVY DEPS HERE)
# ============================================================

class PreDigestRegistry:
    """Registry of handlers mapping file extensions to pre-digestion callables."""
    def __init__(self) -> None:
        self._reg: Dict[str, Callable[[Path, Path], List[Path]]] = {}
        debug("[predigest.registry] init")

    def register(self, ext: str, fn: Callable[[Path, Path], List[Path]]) -> None:
        self._reg[ext.lower()] = fn
        debug("[predigest.registry] register", ext, "->", getattr(fn, "__name__", str(fn)))

    def get(self, ext: str) -> Optional[Callable[[Path, Path], List[Path]]]:
        fn = self._reg.get(ext.lower())
        debug("[predigest.registry] get", ext, "->", getattr(fn, "__name__", None))
        return fn

# ---- Example handlers ----
def predigest_passthrough(input_file: Path, out_dir: Path) -> List[Path]:
    """
    For already-friendly formats (e.g., .csv, .json): copy to local out_dir.
    S3-aware read (fs_read_bytes) + local write.
    """
    debug("[predigest.passthrough] src=", str(input_file), "out_dir=", str(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / Path(input_file).name
    try:
        data = fs_read_bytes(input_file)
        target.write_bytes(data)  # local/write is ephemeral by design
        debug("[predigest.passthrough] wrote ->", str(target))
    except Exception as e:
        # fallback if fs_read_bytes fails on local
        debug("[predigest.passthrough][warn] fs_read_bytes failed; fallback .read_bytes()", repr(e))
        if str(Path(input_file).resolve()) != str(target.resolve()):
            target.write_bytes(Path(input_file).read_bytes())
            debug("[predigest.passthrough] copied via local ->", str(target))
    return [target]

def predigest_xlsx_to_csvs(input_file: Path, out_dir: Path) -> List[Path]:
    """
    Convert .xlsx workbook into one CSV per sheet using pandas/openpyxl.
    S3-aware: use fs_open_readbin to stream the workbook; write CSVs locally.
    """
    debug("[predigest.xlsx] src=", str(input_file), "out_dir=", str(out_dir))
    try:
        import re
        import pandas as pd
        out_dir.mkdir(parents=True, exist_ok=True)

        # Open as binary stream to support S3 or local seamlessly
        with fs_open_readbin(input_file) as f:
            xls = pd.ExcelFile(f, engine="openpyxl")
            sanitize = lambda s: re.sub(r"[^A-Za-z0-9._-]+", "_", s.strip())[:80] or "Sheet"
            produced: List[Path] = []
            for sheet in xls.sheet_names:
                df = xls.parse(sheet_name=sheet, header=0)
                out_file = out_dir / f"{Path(input_file).stem}_{sanitize(sheet)}.csv"
                df.to_csv(out_file, index=False, encoding="utf-8", lineterminator="\n")
                produced.append(out_file)
                debug("[predigest.xlsx] sheet ->", sheet, "file=", str(out_file))
        return produced
    except ModuleNotFoundError as e:
        debug("[predigest.xlsx][error] pandas/openpyxl missing")
        raise RuntimeError("Pre-digestion for .xlsx requires pandas and openpyxl") from e

def default_predigest_registry() -> PreDigestRegistry:
    reg = PreDigestRegistry()
    # friendly formats
    for ext in (".csv", ".json", ".txt"):
        reg.register(ext, predigest_passthrough)
    # excel
    reg.register(".xlsx", predigest_xlsx_to_csvs)
    debug("[predigest.default] handlers=", list(reg._reg.keys()))
    return reg

def _collect_run_ids_from_context(context: Dict[str, Any]) -> List[str]:
    """
    Extract run IDs from the context/params in a forgiving way.

    Supported inputs:
      - context["run_id"] -> str
      - context["run_ids"] -> list[str]
      - context["params"]["run_id" or "__run_id"] -> str
      - context["params"]["run_ids" or "__run_ids"] -> list[str]
      - any params list of dicts that carry {"_runID": "..."} (e.g., expanded options sent back)
      - global param meta mapping under context["params"]["__option_meta"] : {value: {"_runID": "..."}}
    """
    out: List[str] = []

    # 1) top-level context
    rid = context.get("run_id")
    if isinstance(rid, str) and rid.strip():
        out.append(rid.strip())

    rids = context.get("run_ids")
    if isinstance(rids, list):
        out.extend([x for x in rids if isinstance(x, str) and x.strip()])

    params = context.get("params") or {}
    if isinstance(params, dict):
        for k in ("run_id", "__run_id"):
            v = params.get(k)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        for k in ("run_ids", "__run_ids"):
            v = params.get(k)
            if isinstance(v, list):
                out.extend([x for x in v if isinstance(x, str) and x.strip()])

        # 2) look for list-of-dicts with '_runID' (e.g., UI posts selected option meta)
        for v in params.values():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        rid = item.get("_runID")
                        if isinstance(rid, str) and rid.strip():
                            out.append(rid.strip())

        # 3) global option meta map: {'957': {'_runID': '...'}, ...}
        opt_meta = params.get("__option_meta")
        if isinstance(opt_meta, dict):
            for meta in opt_meta.values():
                if isinstance(meta, dict):
                    rid = meta.get("_runID")
                    if isinstance(rid, str) and rid.strip():
                        out.append(rid.strip())

    # de-dupe, preserve order
    seen = set(); dedup: List[str] = []
    for x in out:
        if x not in seen:
            seen.add(x); dedup.append(x)
    return dedup


def _inject_run_ids_into_db_inputs(
    iospec: IoSpec, *, run_ids: List[str], context: Dict[str, Any]
) -> bool:
    """
    If the IoSpec declares a db_input with endpoint='data_dump_dir', inject
    the resolved run_id(s) into its params so layout_resolver can produce
    the specific run dump path(s).

    Returns True if we injected anything.
    """
    if not isinstance(iospec.extra, dict):
        return False
    db_inputs = iospec.extra.get("db_inputs")
    if not isinstance(db_inputs, list):
        return False

    injected = False
    for entry in db_inputs:
        if not isinstance(entry, dict):
            continue
        if entry.get("endpoint") != "data_dump_dir":
            continue

        params = entry.setdefault("params", {})

        # Make sure verb_group is present if context carries it
        if "verb_group" not in params and context.get("verb_group"):
            params["verb_group"] = context["verb_group"]

        if run_ids:
            # For single run, inject string. For multi-run, inject list.
            if len(run_ids) == 1:
                params["run_id"] = run_ids[0]
            else:
                params["run_id"] = run_ids[:]
            injected = True
            debug(
                "[runner][inject] data_dump_dir params -> verb_group=",
                params.get("verb_group"),
                "run_id=",
                params["run_id"],
            )
        else:
            debug(
                "[runner][inject] no run_ids available; leaving data_dump_dir params unchanged"
            )

    return injected
    
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


# ---- Light "concert pat-down" for post-doc -----------------------------------
def _run_post_doc_safe(entry: str, args: dict, env: dict, *, output_dir: str, safety: str = "light", timeout_s: int = 20, tool_module=None):
    """
    Executes module:function with common-sense guards.

    HARD REQUIREMENT:
      - All writes must be inside the project's prepositional_phrase_output_dir
        (resolved by the caller and passed as env["prepositional_phrase_output_dir"]).
      - Cleans up _prepared directory after successful execution

    Notes:
      - We do NOT resolve paths here (no I/O in core). The caller must supply:
          env["prepositional_phrase_output_dir"]  -> absolute path anchor for writes
          env["canonical_phrase_base"]            -> "custom/prepositional phrases" absolute base
          env["pphrase_name"]                     -> the phrase folder name
          env["project_path"]                     -> project root
      - We pass the expected kwargs to the post-doc function:
          output_root, phrase_root, project_path, context
      - Resource limits are applied INSIDE the worker thread to avoid "can't start new thread".
    """
    from importlib import import_module as _imp
    import builtins, os, threading
    from pathlib import Path
    import socket as _socket
    import subprocess as _subprocess
    import shutil as _shutil

    debug("[post_doc] begin | entry=", entry, "safety=", safety, "timeout_s=", timeout_s, "output_dir=", output_dir)

    # ---------- resolve anchors from env (no filesystem operations here) ----------
    phrase_out_dir = env.get("prepositional_phrase_output_dir")
    if not phrase_out_dir:
        raise RuntimeError("post_doc: missing prepositional_phrase_output_dir in env")
    allowed_root = Path(phrase_out_dir).resolve()

    canonical_base = env.get("canonical_phrase_base") or ""
    pphrase_name   = env.get("pphrase_name") or ""
    # phrase_root is the folder where the template for this phrase lives:
    phrase_root = Path(str(canonical_base)) / str(pphrase_name) if (canonical_base and pphrase_name) else Path(str(canonical_base))

    # Track _prepared location for cleanup
    prepared_dir = allowed_root / "_prepared"

    # ---------- direct/unsafe path ----------
    if safety == "off":
        debug("[post_doc] safety=off (no guards)")
        mod_name, fn_name = entry.split(":", 1)
        target_mod = tool_module if (
            tool_module
            and (
                mod_name == getattr(tool_module, "__name__", "")
                or mod_name == Path(getattr(tool_module, "__file__", "")).stem
            )
        ) else _imp(mod_name)
        fn = getattr(target_mod, fn_name)

        call_args = {
            "output_root": str(allowed_root),           # write under canonical phrase output dir
            "phrase_root": str(phrase_root),            # where template lives
            "project_path": str(env.get("project_path") or ""),
            "context": env,                              # pass full env as context
            **(args or {}),
        }
        ret = fn(env, **call_args)
        debug("[post_doc] done (off) ->", type(ret).__name__)
        
        # Clean up _prepared if it exists
        if prepared_dir.exists():
            try:
                # shutil is not monkey-patched in 'off' mode, so this is safe.
                _shutil.rmtree(prepared_dir)
                debug("[post_doc] cleaned up _prepared directory")
            except Exception as e:
                debug(f"[post_doc] warning: failed to clean up _prepared: {e}")
        
        return ret

    # ---------- safe path ----------
    def _is_under_allowed(path: str) -> bool:
        rp = Path(path).resolve()
        try:
            rp.relative_to(allowed_root)
            return True
        except Exception:
            return False

    # monkeypatch targets
    _orig_open   = builtins.open
    _orig_popen  = _subprocess.Popen
    _orig_system = os.system
    _orig_socket = _socket.socket
    _orig_rmtree = _shutil.rmtree
    _orig_remove = os.remove
    _orig_unlink = os.unlink
    _orig_rmdir  = os.rmdir

    def _blocked(*a, **kw):
        debug("[post_doc][guard] blocked call")
        raise RuntimeError("post_doc: operation blocked by safety policy")

    def _guard_open(file, mode="r", *a, **kw):
        if any(m in str(mode) for m in ("w", "a", "+")):
            if not _is_under_allowed(str(file)):
                debug("[post_doc][guard] write blocked (outside allowed root):", file)
                raise RuntimeError(f"post_doc: write must be under prepositional_phrase_output_dir: {file}")
        return _orig_open(file, mode, *a, **kw)

    def _guard_rmtree(path, *a, **kw):
        if not _is_under_allowed(str(path)):
            debug("[post_doc][guard] rmtree blocked (outside allowed root):", path)
            raise RuntimeError(f"post_doc: rmtree must be under prepositional_phrase_output_dir: {path}")
        return _orig_rmtree(path, *a, **kw)

    def _guard_remove(path, *a, **kw):
        if not _is_under_allowed(str(path)):
            debug("[post_doc][guard] remove blocked (outside allowed root):", path)
            raise RuntimeError(f"post_doc: remove must be under prepositional_phrase_output_dir: {path}")
        return _orig_remove(path, *a, **kw)

    def _guard_unlink(path, *a, **kw):
        if not _is_under_allowed(str(path)):
            debug("[post_doc][guard] unlink blocked (outside allowed root):", path)
            raise RuntimeError(f"post_doc: unlink must be under prepositional_phrase_output_dir: {path}")
        return _orig_unlink(path, *a, **kw)

    def _guard_rmdir(path, *a, **kw):
        if not _is_under_allowed(str(path)):
            debug("[post_doc][guard] rmdir blocked (outside allowed root):", path)
            raise RuntimeError(f"post_doc: rmdir must be under prepositional_phrase_output_dir: {path}")
        return _orig_rmdir(path, *a, **kw)

    # resolve callable
    mod_name, fn_name = entry.split(":", 1)
    target_mod = tool_module if (
        tool_module
        and (
            mod_name == getattr(tool_module, "__name__", "")
            or mod_name == Path(getattr(tool_module, "__file__", "")).stem
        )
    ) else _imp(mod_name)
    fn = getattr(target_mod, fn_name)

    _saved_env = dict(os.environ)
    execution_success = False
    return_value = None
    try:
        # neuter env for network/subprocess surprises
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("NO_PROXY", None)
        os.environ["PATH"] = "/usr/bin:/bin"

        # guards
        builtins.open     = _guard_open
        _subprocess.Popen = _blocked
        os.system         = _blocked
        _socket.socket    = _blocked
        _shutil.rmtree    = _guard_rmtree
        os.remove         = _guard_remove
        os.unlink         = _guard_unlink
        os.rmdir          = _guard_rmdir

        err = {"exc": None}
        ret_holder = {"ret": None}

        def _runner():
            try:
                call_args = {
                    "output_root": str(allowed_root),
                    "phrase_root": str(phrase_root),
                    "project_path": str(env.get("project_path") or ""),
                    "context": env,
                    **(args or {}),
                }
                ret_holder["ret"] = fn(env, **call_args)
            except Exception as ex:
                err["exc"] = ex

        th = threading.Thread(target=_runner, daemon=True)
        th.start()
        th.join(timeout=timeout_s)
        if th.is_alive():
            debug("[post_doc][error] timeout")
            raise TimeoutError(f"post_doc: timed out after {timeout_s}s")
        if err["exc"]:
            debug("[post_doc][error] raised:", repr(err["exc"]))
            raise err["exc"]
        
        # If we reach here, the guarded execution was successful.
        execution_success = True
        return_value = ret_holder["ret"]
        debug("[post_doc] ok ->", type(return_value).__name__)

    finally:
        # ALWAYS restore the original functions, regardless of success or failure.
        os.environ.clear()
        os.environ.update(_saved_env)
        builtins.open     = _orig_open
        _subprocess.Popen = _orig_popen
        os.system         = _orig_system
        _socket.socket    = _orig_socket
        _shutil.rmtree    = _orig_rmtree
        os.remove         = _orig_remove
        os.unlink         = _orig_unlink
        os.rmdir          = _orig_rmdir
        debug("[post_doc] guards restored")

    # --- Cleanup Phase ---
    # This code runs AFTER the 'finally' block has restored all functions.
    if execution_success and prepared_dir.exists():
        try:
            # Now we are calling the original, unguarded shutil.rmtree.
            _shutil.rmtree(prepared_dir)
            debug("[post_doc] cleaned up _prepared directory successfully")
        except Exception as e:
            debug(f"[post_doc] warning: failed to clean up _prepared directory: {e}")

    return return_value

def run_custom_tool(
    *,
    tool_module,                      # imported user module with TOOL + run(context)
    iospec: IoSpec,
    verb_schema: dict,
    db_map: dict | None,
    context: dict,                    # e.g., {"verb_group": "...", "run_id": "...", "pphrase_name": "...", "params": {...}}
    layout_resolver: LayoutResolver,  # maps logical slots -> {"paths":[Path,...]} or {"path":Path} (may carry 'slot')
    work_dir: Path,
    executor: ExecutableBase | None = None,
    predigest: PreDigestRegistry | None = None,
    sandbox_exec: SandboxExec | None = None
) -> Dict[str, Any]:
    """
    Orchestrates a single custom execution:
      1) choose executor by kind
      2) validate (shape + schema/db-map)
      3) resolve logical mounts -> real paths
      4) pre-digest raw folders (1 file policy) using registry
      5) assemble I/O map and execute tool
      6) (optional) host-side document step if iospec.extra.post_doc is provided (pphrase only)

    IMPORTANT: For iospec.kind == "parser", this function REQUIRES:
      - context['verb_group']
      - context['run_id']
    Prepositional phrases do not require a run_id (they may consume none or multiple runs).
    """
    import os
    debug("\n[runner] ===== run_custom_tool ENTER =====")
    debug("[runner] iospec.kind=", iospec.kind, "| work_dir=", str(work_dir))
    debug("[runner] context keys=", list(context.keys()))

    # ---------- helpers (local, pure) ----------
    def _collect_run_ids_from_context(ctx: Dict[str, Any]) -> List[str]:
        """Collect run_ids from context.params or derive from PREPHRASE_SETTINGS via fetch_noun_items."""
        params = (ctx.get("params") or {})
        out: List[str] = []

        # 1) direct params
        for key in ("run_ids", "selected_run_ids", "samples_run_ids", "runs"):
            vals = params.get(key)
            if isinstance(vals, str) and vals.strip():
                out.append(vals.strip())
            elif isinstance(vals, (list, tuple, set)):
                out.extend([str(x).strip() for x in vals if str(x).strip()])

        # 2) if none, try to derive from selected sample fields in PREPHRASE_SETTINGS via injected fetch_noun_items
        if not out and iospec.kind == "pphrase":
            fetch = ctx.get("fetch_noun_items")  # expected callable(noun_type) -> list[dict]
            pp_settings = getattr(tool_module, "PREPHRASE_SETTINGS", None)
            if callable(fetch) and isinstance(pp_settings, list):
                # Map field.id -> noun_type for dynamic noun sources
                field_to_noun: Dict[str, str] = {}
                for f in pp_settings:
                    if not isinstance(f, dict):
                        continue
                    fid = f.get("id")
                    opts = f.get("options")
                    if not fid or not isinstance(opts, dict):
                        continue
                    src = opts.get("source")
                    if isinstance(src, str) and src.lower().startswith("noun:"):
                        noun = src.split(":", 1)[1].strip()
                        if noun:
                            field_to_noun[str(fid)] = noun

                run_ids: set[str] = set()
                for fid, noun_type in field_to_noun.items():
                    selected = params.get(fid)
                    if selected is None:
                        continue
                    wanted = []
                    if isinstance(selected, str):
                        wanted = [selected]
                    elif isinstance(selected, (list, tuple, set)):
                        wanted = [str(x) for x in selected]
                    if not wanted:
                        continue

                    try:
                        rows = fetch(noun_type) or []
                    except Exception as e:
                        debug(f"[runner][runids][warn] fetch_noun_items({noun_type}) failed:", repr(e))
                        rows = []

                    if not rows:
                        continue

                    keys_all = set().union(*(r.keys() for r in rows)) if rows else set()
                    cand_keys: List[str] = []
                    if "Sample ID" in keys_all: cand_keys.append("Sample ID")
                    if "id" in keys_all:        cand_keys.append("id")
                    for k in keys_all:
                        kl = str(k).lower()
                        if k not in cand_keys and (kl.endswith("id") or k.endswith(" ID")):
                            cand_keys.append(k)

                    indices: Dict[str, Dict[str, Dict[str, Any]]] = {k: {} for k in cand_keys}
                    for r in rows:
                        for k in cand_keys:
                            v = r.get(k)
                            if v is not None:
                                indices[k][str(v)] = r

                    for w in wanted:
                        hit = None
                        for k in cand_keys:
                            if w in indices[k]:
                                hit = indices[k][w]
                                break
                        if hit and "_runID" in hit and hit["_runID"]:
                            run_ids.add(str(hit["_runID"]))
                        else:
                            debug(f"[runner][runids][miss] noun={noun_type} value={w!r} -> no _runID")

                out = sorted(run_ids)

        # dedupe / normalize
        out = [x for x in out if x]
        out = sorted({*out})
        debug("[runner] collected run_ids from context ->", out)
        return out

    def _inject_run_ids_into_db_inputs(spec: IoSpec, *, run_ids: List[str], context: Dict[str, Any]) -> bool:
        """
        If there is a db_inputs entry for endpoint 'data_dump_dir', inject/merge a 'run_id' param list.
        Returns True if we modified the IoSpec.
        """
        if not run_ids:
            return False
        if not isinstance(spec.extra, dict):
            return False
        db_inputs = spec.extra.get("db_inputs")
        if not isinstance(db_inputs, list):
            return False

        touched = False
        for entry in db_inputs:
            if not isinstance(entry, dict):
                continue
            if entry.get("endpoint") != "data_dump_dir":
                continue
            params = entry.setdefault("params", {})
            # Merge with any existing param 'run_id'
            existing = params.get("run_id")
            if isinstance(existing, str) and existing.strip():
                merged = sorted({existing.strip(), *run_ids})
            elif isinstance(existing, (list, tuple, set)):
                merged = sorted({*(str(x).strip() for x in existing if str(x).strip()), *run_ids})
            else:
                merged = sorted({*run_ids})
            params["run_id"] = merged
            touched = True
        return touched

    # If this is a prepositional phrase, wire run_id(s) into data_dump_dir before validation/mounting.
    if iospec.kind == "pphrase":
        resolved_run_ids = _collect_run_ids_from_context(context)
        if _inject_run_ids_into_db_inputs(iospec, run_ids=resolved_run_ids, context=context):
            debug("[runner] run_id(s) injected into db_inputs[data_dump_dir] ->", resolved_run_ids)
        else:
            debug("[runner] no run_id injection performed (none found or no data_dump_dir endpoint)")

    # 0) enforce required context
    if iospec.kind == "parser":
        if not context.get("verb_group") or not context.get("run_id"):
            debug("[runner][error] missing verb_group/run_id for parser")
            raise ContextError("Custom parser requires 'verb_group' and 'run_id' in context")

    # 1) pick executor
    if executor is None:
        executor = CustomParserExecutable() if iospec.kind == "parser" else PrepositionalPhraseExecutable()
    debug("[runner] executor=", type(executor).__name__)

    # 2) validate
    debug("[runner] validate begin")
    executor.validate(iospec, verb_schema, db_map=db_map, context=context)
    debug("[runner] validate ok")

    # 3) logical plan -> real paths
    debug("[runner] resolve_mounts begin")
    logical = executor.resolve_mounts(iospec, verb_schema, db_map=db_map, context=context)
    debug("[runner] logical mounts:", {k: list(v.keys()) for k, v in logical.items()})
    ExecutableBase.assert_logical_mounts(logical)

    debug("[runner] layout_resolver call")
    resolved = layout_resolver(logical)
    debug("[runner] resolved mounts keys:", {k: list((resolved.get(k) or {}).keys()) for k in ("inputs", "outputs")})

    # ensure we carry slot metadata forward (so runner can detect raw_folder for pre-digest)
    mounts: Dict[str, Dict[str, Any]] = {"inputs": {}, "outputs": {}}
    for section in ("inputs", "outputs"):
        mounts[section] = {}
        for alias, meta in resolved.get(section, {}).items():
            merged = dict(meta)  # physical mapping from resolver
            # propagate original slot (if resolver didn't include it)
            if "slot" not in merged and alias in logical.get(section, {}):
                merged["slot"] = logical[section][alias].get("slot", {})
            mounts[section][alias] = merged
    debug("[runner] mounts (with slots) ready | inputs=", len(mounts["inputs"]), "outputs=", len(mounts["outputs"]))

    # 4) pre-digest raw folders
    predigest = predigest or default_predigest_registry()
    inputs_map: Dict[str, Any] = {}
    inputs_map_lists: Dict[str, List[str]] = {}

    debug("[runner] input assembly + predigest begin")
    for alias, meta in mounts.get("inputs", {}).items():
        slot = meta.get("slot", {})
        kind = slot.get("kind")
        debug("[runner][in] alias=", alias, "kind=", kind)
        if kind == "raw_folder":
            paths = meta.get("paths") or []
            if not paths:
                raise RunError(f"Resolver did not provide 'paths' for raw input folder '{alias}'")
            folder = Path(paths[0])

            if not fs_is_dir(folder):
                raise RunError(f"Raw input folder missing: {folder}")

            # list "files" using S3-aware iterdir + file filter
            entries = list(fs_iterdir(folder))
            files = [p for p in entries if fs_is_file(p)]
            debug("[runner][in][raw] folder=", str(folder), "files=", [Path(f).name for f in files])
            if len(files) != 1:
                raise RunError(f"Raw input folder '{alias}' must have exactly one file, found {len(files)}")
            raw_file = files[0]

            handler = predigest.get(Path(raw_file).suffix)
            out_dir = work_dir / "predigest" / alias
            out_dir.mkdir(parents=True, exist_ok=True)

            produced = handler(raw_file, out_dir) if handler else predigest_passthrough(raw_file, out_dir)
            produced_strs = [str(p) for p in produced]
            inputs_map_lists[alias] = produced_strs
            inputs_map[alias] = produced_strs[0] if len(produced_strs) == 1 else produced_strs
            debug("[runner][in][raw] produced=", produced_strs)

        else:
            # Non-raw inputs: keep resolver-provided mapping. They may be S3 URIs or local paths,
            # and the custom tool is expected to use project I/O helpers when necessary.
            if "paths" in meta:
                vals = [str(p) for p in meta["paths"]]
            elif "path" in meta:
                vals = [str(meta["path"])]
            else:
                vals = []
            inputs_map_lists[alias] = vals
            inputs_map[alias] = vals[0] if len(vals) == 1 else vals
            debug("[runner][in] mapped ->", vals)

    # 5) outputs map (RW) — use container scratch for pphrase OUTPUT_FOLDER, keep canonical for later sync
    from pathlib import Path as _Path
    import shutil as _shutil

    outputs_map: Dict[str, str] = {}
    canonical_output_root: Optional[_Path] = None  # where we will sync to after the container run

    for alias, meta in mounts.get("outputs", {}).items():
        if alias == "OUTPUT_FOLDER" and iospec.kind == "pphrase":
            # 5a) always allocate a scratch dir under work_dir for the tool to write into
            phrase_name = str(context.get("pphrase_name") or "phrase")
            scratch_out = _Path(work_dir) / "pphrase_out" / phrase_name
            scratch_out.mkdir(parents=True, exist_ok=True)
            outputs_map[alias] = str(scratch_out)

            # 5b) remember the canonical destination resolved by layout_resolver (host path or S3 URI)
            canonical_output_root = None
            if "path" in meta and meta["path"]:
                canonical_output_root = _Path(meta["path"])
            elif "paths" in meta and meta["paths"]:
                canonical_output_root = _Path(meta["paths"][0])

            debug(
                "[runner][out] alias=OUTPUT_FOLDER (scratch) ->",
                outputs_map[alias],
                "| canonical ->",
                str(canonical_output_root) if canonical_output_root else None,
            )
        else:
            # parsers or any other outputs keep resolver-provided path
            if "path" in meta:
                outputs_map[alias] = str(meta["path"])
            elif "paths" in meta and meta["paths"]:
                outputs_map[alias] = str(meta["paths"][0])
            debug("[runner][out] alias=", alias, "->", outputs_map.get(alias))

    debug("[runner] outputs_map size=", len(outputs_map))

    # 6) build execution context for the tool
    class _Ctx:
        def __init__(self, inputs, outputs, params):
            self._inputs = inputs
            self._outputs = outputs
            self._params = params or {}
        @property
        def inputs(self):  return self._inputs
        @property
        def outputs(self): return self._outputs
        @property
        def params(self):  return self._params

    ctx = _Ctx(inputs_map, outputs_map, context.get("params", {}))
    debug("[runner] ctx ready | inputs=", len(inputs_map), "outputs=", len(outputs_map), "params_keys=", list(ctx.params.keys()))

    # 7) execute: native (now) or sandbox (WASM)
    result = {"ok": True, "produced": list(outputs_map.values()), "logs": []}
    try:
        if sandbox_exec:
            tool_path = getattr(tool_module, "__file__", None)
            env = {
                "kind": iospec.kind,
                "ctx": ctx,
                "tool_module_path": str(tool_path) if tool_path else None,
                "work_dir": str(work_dir),
                "python_wasm_module": os.environ.get("GIMS_PYTHON_WASM", None),
            }
            def _entry(c=ctx): tool_module.run(c)  # for native sandboxes that just call back
            debug("[runner][sandbox] invoking... [or] tool=", tool_path)
            result.update(sandbox_exec(_entry, env))
            debug("[runner][sandbox] done [or] ok=", result.get("ok"))
        else:
            debug("[runner][native] tool_module.run(ctx)...")
            # Change working directory to output folder for pphrase to prevent writing to wrong locations
            old_cwd = os.getcwd()
            try:
                if iospec.kind == "pphrase":
                    os.chdir(outputs_map['OUTPUT_FOLDER'])
                tool_module.run(ctx)
            finally:
                os.chdir(old_cwd)
            debug("[runner][native] run() returned")

        # 7.5) If this is a pphrase, sync container scratch -> canonical phrase output root (S3-aware)
        if result.get("ok") and iospec.kind == "pphrase":
            scratch_out = _Path(outputs_map.get("OUTPUT_FOLDER", str(work_dir)))
            if canonical_output_root is not None:
                # Create the canonical root (local or S3)
                fs_mkdirs(canonical_output_root)

                # Check if post_doc exists to determine if we need _prepared
                post_doc = (iospec.extra or {}).get("post_doc") if isinstance(iospec.extra, dict) else None

                for item in scratch_out.iterdir():
                    # Skip internal directories EXCEPT _prepared when post_doc exists
                    if item.name.startswith('_'):
                        if item.name == '_prepared' and post_doc:
                            debug(f"[runner][sync] including _prepared for post_doc")
                        else:
                            debug(f"[runner][sync] skipping internal directory: {item.name}")
                            continue

                    src = item
                    dst = canonical_output_root / item.name
                    if src.is_dir():
                        fs_copytree(src, dst)   # S3-aware recursive copy
                    else:
                        fs_copy(src, dst)       # S3-aware file copy
                debug("[runner][sync] scratch -> canonical ok [or]", str(scratch_out), "->", str(canonical_output_root))

    except Exception as e:
        debug("[runner][execute][error]", repr(e))
        result.update({"ok": False, "error": str(e)})

    # 8) Optional host-side document step (pphrase only), after successful run()
    post_doc = (iospec.extra or {}).get("post_doc") if isinstance(iospec.extra, dict) else None
    if result.get("ok") and iospec.kind == "pphrase" and post_doc:
        entry = post_doc.get("entry", "")
        args = post_doc.get("args", {}) or {}
        safety = post_doc.get("safety", "light")  # "light" (default) | "off"
        timeout_s = int(post_doc.get("timeout_s", 20))
        debug("[runner][post_doc] entry=", entry, "safety=", safety, "timeout_s=", timeout_s)
        try:
            # Use final canonical path if available; otherwise the scratch one.
            pp_out_dir = str(canonical_output_root) if (iospec.kind == "pphrase" and canonical_output_root is not None) else outputs_map.get("OUTPUT_FOLDER", str(work_dir))

            env = {
                "context": {
                    "verb_group": context.get("verb_group"),
                    "run_id": context.get("run_id"),
                    "pphrase_name": context.get("pphrase_name"),
                    "noun_type": context.get("noun_type"),
                    "params": context.get("params", {}),
                },
                "inputs": inputs_map_lists,
                "outputs": outputs_map,  # keep original map for reference
                "mounts": mounts,
                "work_dir": str(work_dir),
                "prepositional_phrase_output_dir": pp_out_dir,
                "canonical_phrase_base": context.get("canonical_phrase_base"),
                "project_path": str(context.get("project_path") or ""),
                "pphrase_name": context.get("pphrase_name"),
            }

            ret = _run_post_doc_safe(
                entry, args, env,
                output_dir=pp_out_dir,
                safety=safety,
                timeout_s=timeout_s,
                tool_module=tool_module
            )
            result["post_doc"] = {"ok": True, "entry": entry, "return": ret, "safety": safety, "timeout_s": timeout_s}
            debug("[runner][post_doc] ok")
        except Exception as e:
            result["post_doc"] = {"ok": False, "entry": entry, "error": str(e), "safety": safety, "timeout_s": timeout_s}
            debug("[runner][post_doc][error]", repr(e))

    debug("[runner] ===== run_custom_tool EXIT ===== | ok=", result.get("ok"))
    return result

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
    debug(f"[core_run_customs] WASM module resolved: {PYTHON_WASM_MODULE_DEFAULT}")
except Exception as e:
    # Fallback if resolver fails
    PYTHON_WASM_MODULE_DEFAULT = Path(__file__).parent.parent / "custom" / "python-3.11.4.wasm"
    debug(f"[core_run_customs] WASM resolver failed, using fallback: {PYTHON_WASM_MODULE_DEFAULT}, error: {e}")

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
        debug(f"[WASM.__init__] tool_module_path={tool_module_path}")
        debug(f"[WASM.__init__] work_dir={work_dir}")
        debug(f"[WASM.__init__] python_wasm_module={python_wasm_module}")
        
        if not _HAVE_WASMTIME:
            raise RuntimeError("wasmtime is not installed. `pip install wasmtime`")

        debug(f"[WASM.__init__] checking if wasm module exists: {python_wasm_module.exists()}")
        if not python_wasm_module.exists():
            raise FileNotFoundError(f"Python WASI module not found: {python_wasm_module}")

        self._tool_module_path = Path(tool_module_path)
        self._work_dir = Path(work_dir)
        self._py_wasm = Path(python_wasm_module)
        debug(f"[WASM.__init__] paths stored")

        self._store = _WStore()
        debug(f"[WASM.__init__] store created")
        
        debug(f"[WASM.__init__] loading module from: {str(self._py_wasm)}")
        self._module = _WModule.from_file(self._store.engine, str(self._py_wasm))
        debug(f"[WASM.__init__] module loaded successfully")

    def execute(self, _entry_point_fn: Callable, ctx_obj: Any) -> Dict[str, Any]:
        """
        Execute tool_module.run(ctx) in the sandbox.
        ctx_obj: object with .inputs/.outputs/.params
        """
        with _tempfile.TemporaryDirectory() as tmpdir:
            sandbox_root = Path(tmpdir)
            debug(f"[WASM.execute] sandbox_root: {sandbox_root}")
            
            # Create sandbox directories
            inputs_dir = sandbox_root / "inputs"
            outputs_dir = sandbox_root / "outputs"
            inputs_dir.mkdir()
            outputs_dir.mkdir()
            debug(f"[WASM.execute] created dirs: inputs={inputs_dir}, outputs={outputs_dir}")

            # /bootstrap.py
            (sandbox_root / "bootstrap.py").write_text(_BOOTSTRAP_SCRIPT, encoding="utf-8")
            debug(f"[WASM.execute] wrote bootstrap.py")
            
            # /tool.py
            (sandbox_root / "tool.py").write_bytes(self._tool_module_path.read_bytes())
            debug(f"[WASM.execute] wrote tool.py from {self._tool_module_path}")
            
            # Copy input files into sandbox and rewrite paths
            sandboxed_inputs = {}
            original_inputs = getattr(ctx_obj, "inputs", {})
            debug(f"[WASM.execute] processing {len(original_inputs)} input keys: {list(original_inputs.keys())}")
            
            for key, value in original_inputs.items():
                debug(f"[WASM.execute] input[{key}]: type={type(value).__name__}, value={value}")
                
                if isinstance(value, str):
                    try:
                        src_path = Path(value).resolve()
                        debug(f"[WASM.execute] input[{key}]: resolved path={src_path}, exists={src_path.exists()}, is_file={src_path.is_file() if src_path.exists() else 'N/A'}")
                        
                        if src_path.exists() and src_path.is_file():
                            dst = inputs_dir / src_path.name
                            dst.write_bytes(src_path.read_bytes())
                            sandboxed_inputs[key] = f"/inputs/{src_path.name}"
                            debug(f"[WASM.execute] input[{key}]: copied {src_path.name} -> {dst}")
                        else:
                            sandboxed_inputs[key] = value
                            debug(f"[WASM.execute] input[{key}]: not a file, passing through")
                    except Exception as e:
                        sandboxed_inputs[key] = value
                        debug(f"[WASM.execute] input[{key}]: error resolving path: {e}")
                        
                elif isinstance(value, list):
                    debug(f"[WASM.execute] input[{key}]: processing list of {len(value)} items")
                    sandboxed_list = []
                    for idx, item in enumerate(value):
                        if isinstance(item, str):
                            try:
                                src_path = Path(item).resolve()
                                debug(f"[WASM.execute] input[{key}][{idx}]: path={src_path}, exists={src_path.exists()}, is_file={src_path.is_file() if src_path.exists() else 'N/A'}")
                                
                                if src_path.exists() and src_path.is_file():
                                    dst = inputs_dir / src_path.name
                                    dst.write_bytes(src_path.read_bytes())
                                    sandboxed_list.append(f"/inputs/{src_path.name}")
                                    debug(f"[WASM.execute] input[{key}][{idx}]: copied {src_path.name}")
                                else:
                                    sandboxed_list.append(item)
                                    debug(f"[WASM.execute] input[{key}][{idx}]: not a file, passing through")
                            except Exception as e:
                                sandboxed_list.append(item)
                                debug(f"[WASM.execute] input[{key}][{idx}]: error: {e}")
                        else:
                            sandboxed_list.append(item)
                            debug(f"[WASM.execute] input[{key}][{idx}]: non-string, passing through")
                    sandboxed_inputs[key] = sandboxed_list
                else:
                    sandboxed_inputs[key] = value
                    debug(f"[WASM.execute] input[{key}]: non-string/list, passing through")
            
            debug(f"[WASM.execute] sandboxed_inputs keys: {list(sandboxed_inputs.keys())}")
            
            # Rewrite output paths to point into sandbox
            sandboxed_outputs = {}
            original_outputs = getattr(ctx_obj, "outputs", {})
            output_map = {}  # Map sandbox paths back to real paths
            debug(f"[WASM.execute] processing {len(original_outputs)} output keys: {list(original_outputs.keys())}")
            
            for key, value in original_outputs.items():
                if isinstance(value, str):
                    filename = Path(value).name
                    sandboxed_outputs[key] = f"/outputs/{filename}"
                    output_map[f"/outputs/{filename}"] = value
                    debug(f"[WASM.execute] output[{key}]: {filename} -> /outputs/{filename} (real: {value})")
                else:
                    sandboxed_outputs[key] = value
                    debug(f"[WASM.execute] output[{key}]: non-string, passing through")
            
            # /context.json with sandboxed paths
            ctx_data = {
                "inputs": sandboxed_inputs,
                "outputs": sandboxed_outputs,
                "params": getattr(ctx_obj, "params", {}),
            }
            (sandbox_root / "context.json").write_text(_json.dumps(ctx_data, indent=2), encoding="utf-8")
            debug(f"[WASM.execute] wrote context.json: {len(ctx_data)} keys")

            # WASI config
            wasi = _WWasiConfig()
            wasi.argv = ("python", "/bootstrap.py")
            debug(f"[WASM.execute] WASI argv set: python /bootstrap.py")

            # Mount ONLY the sandbox root - no access to host filesystem
            resolved_root = str(sandbox_root.resolve())
            debug(f"[WASM.execute] mounting sandbox root: {resolved_root}")
            wasi.preopen_dir(resolved_root, "/")
            debug(f"[WASM.execute] preopen_dir successful")

            # Capture stdio to files (reliable across wasmtime versions)
            stdout_path = sandbox_root / "stdout.txt"
            stderr_path = sandbox_root / "stderr.txt"
            
            wasi.stdout_file = str(stdout_path)
            wasi.stderr_file = str(stderr_path)
            
            self._store.set_wasi(wasi)
            debug(f"[WASM.execute] WASI configured (file-based stdio), starting linker")

            linker = _WLinker(self._store.engine)
            linker.define_wasi()
            debug(f"[WASM.execute] linker defined, instantiating module")
            instance = linker.instantiate(self._store, self._module)
            debug(f"[WASM.execute] module instantiated, calling _start")

            try:
                instance.exports(self._store)["_start"](self._store)
                debug(f"[WASM.execute] _start completed successfully")
            except Exception as e:
                debug(f"[WASM.execute] _start failed: {e}")
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
                debug(f"[WASM.execute] stdout length: {len(stdout)}")
            except Exception as e:
                stdout = ""
                debug(f"[WASM.execute] failed to read stdout file: {e}")
                
            try:
                if stderr_path.exists():
                    stderr = stderr_path.read_text(encoding="utf-8")
                else:
                    stderr = ""
                debug(f"[WASM.execute] stderr length: {len(stderr)}")
            except Exception as e:
                stderr = ""
                debug(f"[WASM.execute] failed to read stderr file: {e}")

            if not stdout:
                debug(f"[WASM.execute] ERROR: no stdout produced")
                return {"ok": False, "error": "Sandbox produced no output.", "logs": [stderr]}

            try:
                result = _json.loads(stdout)
                debug(f"[WASM.execute] parsed result: ok={result.get('ok')}, keys={list(result.keys())}")
                result["logs"] = [stderr]
                
                # Copy output files from sandbox back to real filesystem
                if result.get("ok"):
                    debug(f"[WASM.execute] copying outputs back, map has {len(output_map)} entries")
                    produced = []
                    for sandbox_path, real_path in output_map.items():
                        sandbox_file = outputs_dir / Path(sandbox_path).name
                        debug(f"[WASM.execute] checking {sandbox_file}, exists={sandbox_file.exists()}")
                        if sandbox_file.exists():
                            Path(real_path).parent.mkdir(parents=True, exist_ok=True)
                            Path(real_path).write_bytes(sandbox_file.read_bytes())
                            produced.append(real_path)
                            debug(f"[WASM.execute] copied {sandbox_file.name} -> {real_path}")
                        else:
                            debug(f"[WASM.execute] WARNING: output file {sandbox_file} not found")
                    result["produced"] = produced
                    debug(f"[WASM.execute] produced {len(produced)} files")
                
                return result
            except Exception as e:
                debug(f"[WASM.execute] ERROR parsing result: {e}")
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
            debug(f"[make_wasmtime_sandbox] resolved WASM path: {default_wasm}")
        except Exception as e:
            debug(f"[make_wasmtime_sandbox] resolver failed: {e}, using PYTHON_WASM_MODULE_DEFAULT")
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