# core/executors/pphrase.py
from __future__ import annotations
from typing import Dict, Any, List
from .base_executor import ExecutableBase, IoSpec

class PrepositionalPhraseExecutable(ExecutableBase):
    """
    Policy:
      - READS: May declare inputs from:
          * raw folders (per verb schema)          -> kind: "raw_folder"
          * exact file inputs (per verb schema)    -> kind: "file", "data_entry", "adverbs", etc.
          * ANY endpoint present in local_layout_map.json ("database map") -> kind: "db_endpoint"
            (e.g., noun_items, noun_images, data_entry, status_file, adverb_file,
                   data_dump_dir, verb_group_log, verb_group_log_config, etc.)
      - WRITES: Must declare ONE output folder name; writing allowed only inside
                prepositional phrases/<pphrase_name>/<declared_folder> (resolved by layout layer).
      - This module emits ONLY logical slots; a layout/resolution layer maps slots -> host/container paths.

      - OPTIONAL HOST-SIDE POST STEP (document generation):
          If IoSpec.extra.post_doc is present, the orchestrator (outside the container) may import and call
          that entry point with resolved *host* paths. Example shape:

            spec.extra = {
              "post_doc": {
                "entry": "my_reports.pphrase_reports:make_document",
                "args": {"template": "coa_v1.docx", "title": "COA"}
              }
            }

          The executor DOES NOT validate or execute this step; it only surfaces metadata via mounts["meta"].
          The host runner is expected to:
            * import module:function from 'entry'
            * construct an env dict with resolved host paths for inputs/outputs and the 'context'
            * call function(env, **args)
    """

    # ---------------- Validation ----------------
    def _validate_outputs(self, spec: IoSpec, schema: dict) -> None:
        folder = (spec.outputs or {}).get("folder", "")
        if not isinstance(folder, str) or not folder.strip():
            raise ValueError("Prepositional phrase must declare a non-empty output folder name")
        if "/" in folder.strip("/"):
            raise ValueError("Output folder must be a single segment (no nested paths)")

    def validate(self, spec: IoSpec, schema: dict, db_map: dict | None = None) -> None:
        """
        - raw_folders must be declared in verb.data_entry_schema.raw_data_inputs
        - file_inputs must be allowed by verb schema (DataEntry.json, adverbs.json if adverb_schema, extras if modeled)
        - db_inputs (if present in IoSpec) must reference keys that exist in local_layout_map.json,
          and provide all required params that its template placeholders imply
        - outputs: single folder name (checked above)

        NOTE: extra.post_doc is intentionally NOT schema-validated here beyond base shape checks.
        """
        super().validate(spec, schema)  # base validates shapes where applicable
        self._validate_outputs(spec, schema)

        # 1) raw folder inputs ⊆ verb schema
        allowed_raw = set(self._schema_raw_inputs(schema))
        unknown_raw = [rf for rf in spec.raw_folders if rf not in allowed_raw]
        if unknown_raw:
            raise ValueError(
                f"Raw folder inputs not declared in verb schema: {unknown_raw}. "
                f"Declared: {sorted(allowed_raw)}"
            )

        # 2) file inputs whitelist (schema-aware)
        allowed_files = self._allowed_file_inputs(schema)
        bad_files = [fi for fi in spec.file_inputs if fi not in allowed_files]
        if bad_files:
            raise ValueError(
                f"File inputs not allowed by verb schema: {bad_files}. "
                f"Allowed: {sorted(allowed_files)}"
            )

        # 3) database-map endpoints
        db_inputs: List[dict] = (spec.extra or {}).get("db_inputs", [])
        if db_inputs:
            if not isinstance(db_map, dict) or not db_map:
                raise ValueError("Database map (local_layout_map.json) is required to validate db_inputs")

            for i, item in enumerate(db_inputs, 1):
                endpoint = item.get("endpoint")
                params   = item.get("params", {})
                if endpoint not in db_map:
                    raise ValueError(f"db_inputs[{i}] references unknown endpoint '{endpoint}'")
                # Infer required params from template placeholders like {verb_group}, {run_id}, etc.
                required = self._placeholders(db_map[endpoint])
                missing  = [k for k in required if k not in params]
                if missing:
                    raise ValueError(
                        f"db_inputs[{i}] for endpoint '{endpoint}' missing params: {missing}. "
                        f"Template requires: {sorted(required)}"
                    )

    # ---------------- Logical Mounts (no paths) ----------------
    def resolve_mounts(
        self,
        spec: IoSpec,
        schema: dict,
        **kwargs
    ) -> Dict[str, Dict[str, Any]]:
        """
        Returns a logical mount plan; layout layer maps these slots to actual paths.
        kwargs may include:
          - db_map: dict[str, str] of endpoint -> template
          - context: identifiers used by templates (e.g., verb_group, run_id, noun_type, pphrase_name)
        Shape:
          {
            "inputs": { "<alias>": {"slot": {...}, "mode": "ro"} },
            "outputs": { "OUTPUT_FOLDER": {"slot": {...}, "mode": "rw"} },
            "meta": { "post_doc": {"entry": "mod:func", "args": {...}} }   # optional
          }
        """
        db_map: dict = kwargs.get("db_map") or {}
        context: dict = kwargs.get("context") or {}

        mounts: Dict[str, Dict[str, Any]] = {"inputs": {}, "outputs": {}}

        # A) raw folders (RO)
        for alias in spec.raw_folders:
            mounts["inputs"][alias] = {
                "slot": {"kind": "raw_folder", "name": alias},
                "mode": "ro"
            }

        # B) exact file inputs (RO) derived from schema (e.g., DataEntry.json, adverbs.json)
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

        # C) database-map endpoints (RO)
        for entry in (spec.extra or {}).get("db_inputs", []):
            endpoint = entry["endpoint"]
            params   = entry.get("params", {})
            alias = entry.get("alias") or endpoint
            mounts["inputs"][alias] = {
                "slot": {"kind": "db_endpoint", "endpoint": endpoint, "params": params},
                "mode": "ro"
            }

        # D) output folder (RW) inside phrase root
        folder_name = spec.outputs["folder"]
        mounts["outputs"]["OUTPUT_FOLDER"] = {
            "slot": {
                "kind": "pphrase_output_root",
                "pphrase_name": context.get("pphrase_name"),
                "folder": folder_name
            },
            "mode": "rw"
        }

        # E) optional post-doc metadata (purely declarative; host runner consumes)
        post_doc = (spec.extra or {}).get("post_doc")
        if post_doc:
            mounts.setdefault("meta", {})
            # Pass through exactly as declared (no validation here beyond base shape)
            mounts["meta"]["post_doc"] = {
                "entry": post_doc.get("entry"),
                "args": post_doc.get("args", {}),
                # For convenience, surface the context keys that are typically useful to the host step
                "context": {
                    "pphrase_name": context.get("pphrase_name"),
                    "verb_group": context.get("verb_group"),
                    "run_id": context.get("run_id"),
                    "noun_type": context.get("noun_type"),
                },
                # Let the host know which logical aliases exist (it will receive resolved host paths)
                "aliases": {
                    "inputs": list(mounts["inputs"].keys()),
                    "outputs": list(mounts["outputs"].keys()),
                },
            }

        return mounts

    # ---------------- Helpers (no filesystem) ----------------
    def _schema_raw_inputs(self, schema: dict) -> List[str]:
        return list(schema.get("data_entry_schema", {}).get("raw_data_inputs", []))

    def _allowed_file_inputs(self, schema: dict) -> List[str]:
        allowed = ["DataEntry.json"]
        if schema.get("adverb_schema"):
            allowed.append("adverbs.json")
        extras = schema.get("data_entry_schema", {}).get("file_inputs", [])
        if isinstance(extras, list):
            allowed.extend(extras)
        # You can optionally allow status here if desired:
        # allowed.append("Status.json")
        return allowed

    def _placeholders(self, template: str) -> List[str]:
        """
        Extract {placeholders} from a template string like "verbs/{verb_group}/data_dumps/{run_id}/DataEntry.json".
        """
        out: List[str] = []
        buf: List[str] = []
        inside = False
        for ch in template:
            if ch == "{":
                inside, buf = True, []
            elif ch == "}" and inside:
                inside = False
                out.append("".join(buf))
                buf = []
            elif inside:
                buf.append(ch)
        return out
