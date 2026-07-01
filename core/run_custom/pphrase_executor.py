# core/run_custom/pphrase_executor.py
from __future__ import annotations
from typing import Dict, List, Any, Optional, Set, Tuple
from pathlib import Path
from .schema import ExecutableBase, IoSpec
from ._common import log


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

        log.debug("[pphrase.validate_schema] begin")

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
            log.debug("[pphrase.validate_schema][error] bad_raw=", bad_raw)
            raise ValueError(f"Raw folder inputs not in verb schema: {bad_raw}")

        # --- File whitelist check ---
        allowed_files = self._allowed_file_inputs(schema)
        bad_files = [fi for fi in spec.file_inputs if fi not in allowed_files]
        log.debug("[pphrase.validate_schema] allowed_files=", allowed_files, "requested=", spec.file_inputs)
        if bad_files:
            log.debug("[pphrase.validate_schema][error] bad_files=", bad_files)
            raise ValueError(f"File inputs not allowed by verb schema: {bad_files} (allowed: {sorted(allowed_files)})")

        # --- DB-map validation ---
        db_inputs = (spec.extra or {}).get("db_inputs", [])
        if db_inputs:
            if not isinstance(db_map, dict) or not db_map:
                raise ValueError("Database map (e.g., local_layout_map.json) is required to validate db_inputs")
            for i, item in enumerate(db_inputs, 1):
                endpoint = item.get("endpoint")
                params   = item.get("params", {})
                log.debug(f"[pphrase.validate_schema] db_inputs[{i}] endpoint={endpoint} params={list(params.keys())}")
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

        log.debug("[pphrase.validate_schema] ok | phrase_root=", str(phrase_root), "output_root=", str(output_root))

    # ------------------------ logical mounts (no I/O) ------------------------
    def resolve_mounts(self, spec: IoSpec, schema: dict, **kwargs) -> Dict[str, Dict[str, Any]]:
        context: Dict[str, Any] = kwargs.get("context") or {}
        log.debug("[pphrase.resolve_mounts] begin | folder=", spec.outputs.get("folder"),
              "ctx.pphrase_name=", context.get("pphrase_name"))

        mounts: Dict[str, Dict[str, Any]] = {"inputs": {}, "outputs": {}}

        # A) raw folders (RO)
        for alias in spec.raw_folders:
            mounts["inputs"][alias] = {"slot": {"kind": "raw_folder", "name": alias}, "mode": "ro"}
            log.debug("[pphrase.resolve_mounts][in] raw_folder ->", alias)

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
            log.debug("[pphrase.resolve_mounts][in] file_input ->", alias, "| slot=", slot)

        # C) db-map endpoints (RO)
        for entry in (spec.extra or {}).get("db_inputs", []):
            alias = entry.get("alias") or entry["endpoint"]
            mounts["inputs"][alias] = {
                "slot": {"kind": "db_endpoint", "endpoint": entry["endpoint"], "params": entry.get("params", {})},
                "mode": "ro",
            }
            log.debug("[pphrase.resolve_mounts][in] db_endpoint ->", alias, "| params_keys=", list(entry.get("params", {}).keys()))

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
        log.debug("[pphrase.resolve_mounts] done")
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
            log.debug("[pphrase.plan_post_doc] no post_doc")
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
        log.debug("[pphrase.plan_post_doc] prepared plan | entry=", entry)
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
        log.debug("[pphrase._allowed_file_inputs] ->", allowed)
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
        log.debug("[pphrase._placeholders] template=", template, "->", out)
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
