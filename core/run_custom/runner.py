# core/run_custom/runner.py
from __future__ import annotations
import os
from typing import Dict, List, Any, Optional
from pathlib import Path
from .schema import IoSpec, ExecutableBase
from ._types import LayoutResolver, SandboxExec, RunError, ContextError
from .registry import resolve_executor
from .predigest import PreDigestRegistry, default_predigest_registry, predigest_passthrough
from .post_doc import _run_post_doc_safe
from ._common import config, AppError, log, fs_is_dir, fs_iterdir, fs_is_file, fs_mkdirs, fs_copytree, fs_copy


# ──────────────────────────────────────────────────────────────────────────────
# Run-id helpers (pphrase). De-nested from run_custom_tool (Phase 4) — pure, with
# the formerly-captured iospec/tool_module passed explicitly so they are unit-testable.
# NOTE: deliberately distinct from the (currently dead) same-named helpers in predigest.py,
# which use different rules; consolidating them would change behaviour, so they stay separate.
# ──────────────────────────────────────────────────────────────────────────────
def _collect_run_ids_from_context(ctx: Dict[str, Any], *, iospec: IoSpec, tool_module) -> List[str]:
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
                    log.debug(f"[runner][runids][warn] fetch_noun_items({noun_type}) failed:", repr(e))
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
                        log.debug(f"[runner][runids][miss] noun={noun_type} value={w!r} -> no _runID")

            out = sorted(run_ids)

    # dedupe / normalize
    out = [x for x in out if x]
    out = sorted({*out})
    log.debug("[runner] collected run_ids from context ->", out)
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


class _ToolContext:
    """The execution context object handed to a custom tool's ``run(context)`` — exposes the
    resolved ``inputs`` / ``outputs`` maps and the caller ``params``. De-nested from
    run_custom_tool (Phase 4); identical shape to the former local ``_Ctx``."""
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


class ExecutionService:
    """Orchestrates a single custom execution (parser or prepositional phrase).

    One instance per run. The procedure the GUI used to inline as a 400-line function now lives as
    a sequence of small, individually-followable phases:
      1) (pphrase) collect + inject run_ids into the data_dump_dir db_input
      2) enforce required context, pick + run the executor's validate / resolve_mounts
      3) resolve logical mounts -> real paths (carrying slot metadata forward)
      4) assemble inputs, pre-digesting raw folders (1-file policy)
      5) assemble outputs (pphrase OUTPUT_FOLDER gets a work_dir scratch + canonical destination)
      6) build the per-tool context and execute through the selected ExecutionBackend
      7) (pphrase) sync scratch -> canonical, then run the optional host-side post_doc

    Behaviour is identical to the pre-extraction monolith; :func:`run_custom_tool` is the thin
    back-compat entry point that builds a service and calls :meth:`run`.

    IMPORTANT: for ``iospec.kind == "parser"`` this REQUIRES ``context['verb_group']`` and
    ``context['run_id']``. Prepositional phrases do not require a run_id (they consume none or many).
    """

    def __init__(
        self,
        *,
        tool_module,
        iospec: IoSpec,
        verb_schema: dict,
        db_map: dict | None,
        context: Dict[str, Any],
        layout_resolver: LayoutResolver,
        work_dir: Path,
        executor: ExecutableBase | None = None,
        predigest: PreDigestRegistry | None = None,
        sandbox_exec: SandboxExec | None = None,
        backend: SandboxExec | None = None,
    ) -> None:
        self.tool_module = tool_module
        self.iospec = iospec
        self.verb_schema = verb_schema
        self.db_map = db_map
        self.context = context
        self.layout_resolver = layout_resolver
        self.work_dir = work_dir
        self.executor = executor
        self.predigest = predigest
        self.sandbox_exec = sandbox_exec
        self.backend = backend

    # ---------- phases ----------
    def _inject_pphrase_run_ids(self) -> None:
        """(pphrase only) wire resolved run_id(s) into data_dump_dir before validation/mounting."""
        if self.iospec.kind != "pphrase":
            return
        resolved_run_ids = _collect_run_ids_from_context(
            self.context, iospec=self.iospec, tool_module=self.tool_module
        )
        if _inject_run_ids_into_db_inputs(self.iospec, run_ids=resolved_run_ids, context=self.context):
            log.debug("[runner] run_id(s) injected into db_inputs[data_dump_dir] ->", resolved_run_ids)
        else:
            log.debug("[runner] no run_id injection performed (none found or no data_dump_dir endpoint)")

    def _require_context(self) -> None:
        """0) enforce required context (parser needs verb_group + run_id)."""
        if self.iospec.kind == "parser":
            if not self.context.get("verb_group") or not self.context.get("run_id"):
                log.debug("[runner][error] missing verb_group/run_id for parser")
                raise ContextError("Custom parser requires 'verb_group' and 'run_id' in context")

    def _resolve_mounts(self) -> Dict[str, Dict[str, Any]]:
        """1-3) pick executor, validate, then logical plan -> real paths (slot metadata preserved)."""
        # 1) pick executor (via the one kind->executor registry; see resolve_executor)
        if self.executor is None:
            self.executor = resolve_executor(self.iospec.kind)
        log.debug("[runner] executor=", type(self.executor).__name__)

        # 2) validate
        log.debug("[runner] validate begin")
        self.executor.validate(self.iospec, self.verb_schema, db_map=self.db_map, context=self.context)
        log.debug("[runner] validate ok")

        # 3) logical plan -> real paths
        log.debug("[runner] resolve_mounts begin")
        logical = self.executor.resolve_mounts(self.iospec, self.verb_schema, db_map=self.db_map, context=self.context)
        log.debug("[runner] logical mounts:", {k: list(v.keys()) for k, v in logical.items()})
        ExecutableBase.assert_logical_mounts(logical)

        log.debug("[runner] layout_resolver call")
        resolved = self.layout_resolver(logical)
        log.debug("[runner] resolved mounts keys:", {k: list((resolved.get(k) or {}).keys()) for k in ("inputs", "outputs")})

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
        log.debug("[runner] mounts (with slots) ready | inputs=", len(mounts["inputs"]), "outputs=", len(mounts["outputs"]))
        return mounts

    def _assemble_inputs(self, mounts: Dict[str, Dict[str, Any]]):
        """4) build the inputs map, pre-digesting raw folders (1-file policy). Returns
        (inputs_map, inputs_map_lists)."""
        predigest = self.predigest or default_predigest_registry()
        inputs_map: Dict[str, Any] = {}
        inputs_map_lists: Dict[str, List[str]] = {}

        log.debug("[runner] input assembly + predigest begin")
        for alias, meta in mounts.get("inputs", {}).items():
            slot = meta.get("slot", {})
            kind = slot.get("kind")
            log.debug("[runner][in] alias=", alias, "kind=", kind)
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
                log.debug("[runner][in][raw] folder=", str(folder), "files=", [Path(f).name for f in files])
                if len(files) != 1:
                    raise RunError(f"Raw input folder '{alias}' must have exactly one file, found {len(files)}")
                raw_file = files[0]

                handler = predigest.get(Path(raw_file).suffix)
                out_dir = self.work_dir / "predigest" / alias
                out_dir.mkdir(parents=True, exist_ok=True)

                produced = handler(raw_file, out_dir) if handler else predigest_passthrough(raw_file, out_dir)
                produced_strs = [str(p) for p in produced]
                inputs_map_lists[alias] = produced_strs
                inputs_map[alias] = produced_strs[0] if len(produced_strs) == 1 else produced_strs
                log.debug("[runner][in][raw] produced=", produced_strs)

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
                log.debug("[runner][in] mapped ->", vals)
        return inputs_map, inputs_map_lists

    def _assemble_outputs(self, mounts: Dict[str, Dict[str, Any]]):
        """5) build the outputs map. pphrase OUTPUT_FOLDER gets a work_dir scratch dir to write
        into, plus the canonical destination remembered for the post-run sync. Returns
        (outputs_map, canonical_output_root)."""
        outputs_map: Dict[str, str] = {}
        canonical_output_root: Optional[Path] = None  # where we will sync to after the container run

        for alias, meta in mounts.get("outputs", {}).items():
            if alias == "OUTPUT_FOLDER" and self.iospec.kind == "pphrase":
                # 5a) always allocate a scratch dir under work_dir for the tool to write into
                phrase_name = str(self.context.get("pphrase_name") or "phrase")
                scratch_out = Path(self.work_dir) / "pphrase_out" / phrase_name
                scratch_out.mkdir(parents=True, exist_ok=True)
                outputs_map[alias] = str(scratch_out)

                # 5b) remember the canonical destination resolved by layout_resolver (host path or S3 URI)
                canonical_output_root = None
                if "path" in meta and meta["path"]:
                    canonical_output_root = Path(meta["path"])
                elif "paths" in meta and meta["paths"]:
                    canonical_output_root = Path(meta["paths"][0])

                log.debug(
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
                log.debug("[runner][out] alias=", alias, "->", outputs_map.get(alias))

        log.debug("[runner] outputs_map size=", len(outputs_map))
        return outputs_map, canonical_output_root

    def _execute(self, ctx: _ToolContext, inputs_map: Dict[str, Any], outputs_map: Dict[str, str],
                 canonical_output_root: Optional[Path]) -> Dict[str, Any]:
        """6-7) execute through the selected ExecutionBackend, then (pphrase) sync scratch ->
        canonical. Default backend is the hardened container (R15); in-process is gated behind
        GIMS_ALLOW_INPROCESS_TOOLS; wasm and any legacy sandbox_exec callable still work."""
        result = {"ok": True, "produced": list(outputs_map.values()), "logs": []}
        try:
            from core.orchestration.execution_backend import select_backend
            chosen = self.backend if self.backend is not None else self.sandbox_exec
            if chosen is None:
                # No explicit backend: container unless in-process is explicitly permitted.
                chosen = select_backend("native" if config.allow_inprocess_tools() else "container")
            tool_path = getattr(self.tool_module, "__file__", None)
            env = {
                "kind": self.iospec.kind,
                "ctx": ctx,
                "tool_module_path": str(tool_path) if tool_path else None,
                "work_dir": str(self.work_dir),
                "python_wasm_module": os.environ.get("GIMS_PYTHON_WASM", None),
            }
            def _entry(c=ctx): self.tool_module.run(c)  # in-process backends call back through this
            log.debug("[runner][exec] backend=", getattr(chosen, "__name__", type(chosen).__name__),
                      "| kind=", self.iospec.kind, "| tool=", tool_path)
            result.update(chosen(_entry, env))
            log.debug("[runner][exec] done | ok=", result.get("ok"))

            # 7.5) If this is a pphrase, sync container scratch -> canonical phrase output root (S3-aware)
            if result.get("ok") and self.iospec.kind == "pphrase":
                scratch_out = Path(outputs_map.get("OUTPUT_FOLDER", str(self.work_dir)))
                if canonical_output_root is not None:
                    # Create the canonical root (local or S3)
                    fs_mkdirs(canonical_output_root)

                    # Check if post_doc exists to determine if we need _prepared
                    post_doc = (self.iospec.extra or {}).get("post_doc") if isinstance(self.iospec.extra, dict) else None

                    for item in scratch_out.iterdir():
                        # Skip internal directories EXCEPT _prepared when post_doc exists
                        if item.name.startswith('_'):
                            if item.name == '_prepared' and post_doc:
                                log.debug("[runner][sync] including _prepared for post_doc")
                            else:
                                log.debug(f"[runner][sync] skipping internal directory: {item.name}")
                                continue

                        src = item
                        dst = canonical_output_root / item.name
                        if src.is_dir():
                            fs_copytree(src, dst)   # S3-aware recursive copy
                        else:
                            fs_copy(src, dst)       # S3-aware file copy
                    log.debug("[runner][sync] scratch -> canonical ok [or]", str(scratch_out), "->", str(canonical_output_root))

        except AppError:
            # Preserve the backend's typed status (CONTAINER_RUNTIME_NOT_FOUND 503,
            # CONTAINER_RUN_TIMEOUT 504, INPROCESS_TOOLS_DISABLED 403, ...) — a deployment/config
            # error must not be downgraded to a generic tool-failure. The GUI re-raises HTTPException
            # so FastAPI renders the right status.
            raise
        except Exception as e:
            log.debug("[runner][execute][error]", repr(e))
            result.update({"ok": False, "error": str(e)})
        return result

    def _run_post_doc(self, result: Dict[str, Any], *, inputs_map_lists, outputs_map, mounts,
                      canonical_output_root: Optional[Path]) -> None:
        """8) optional host-side document step (pphrase only), after a successful run()."""
        post_doc = (self.iospec.extra or {}).get("post_doc") if isinstance(self.iospec.extra, dict) else None
        if not (result.get("ok") and self.iospec.kind == "pphrase" and post_doc):
            return
        context = self.context
        entry = post_doc.get("entry", "")
        args = post_doc.get("args", {}) or {}
        safety = post_doc.get("safety", "light")  # "light" (default) | "off"
        timeout_s = int(post_doc.get("timeout_s", 20))
        log.debug("[runner][post_doc] entry=", entry, "safety=", safety, "timeout_s=", timeout_s)
        try:
            # Use final canonical path if available; otherwise the scratch one.
            pp_out_dir = str(canonical_output_root) if (self.iospec.kind == "pphrase" and canonical_output_root is not None) else outputs_map.get("OUTPUT_FOLDER", str(self.work_dir))

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
                "work_dir": str(self.work_dir),
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
                tool_module=self.tool_module
            )
            result["post_doc"] = {"ok": True, "entry": entry, "return": ret, "safety": safety, "timeout_s": timeout_s}
            log.debug("[runner][post_doc] ok")
        except Exception as e:
            result["post_doc"] = {"ok": False, "entry": entry, "error": str(e), "safety": safety, "timeout_s": timeout_s}
            log.debug("[runner][post_doc][error]", repr(e))

    def run(self) -> Dict[str, Any]:
        log.debug("\n[runner] ===== run_custom_tool ENTER =====")
        log.debug("[runner] iospec.kind=", self.iospec.kind, "| work_dir=", str(self.work_dir))
        log.debug("[runner] context keys=", list(self.context.keys()))

        self._inject_pphrase_run_ids()
        self._require_context()
        mounts = self._resolve_mounts()
        inputs_map, inputs_map_lists = self._assemble_inputs(mounts)
        outputs_map, canonical_output_root = self._assemble_outputs(mounts)

        # 6) build execution context for the tool
        ctx = _ToolContext(inputs_map, outputs_map, self.context.get("params", {}))
        log.debug("[runner] ctx ready | inputs=", len(inputs_map), "outputs=", len(outputs_map), "params_keys=", list(ctx.params.keys()))

        # 7) execute + (pphrase) sync
        result = self._execute(ctx, inputs_map, outputs_map, canonical_output_root)

        # 8) optional host-side document step
        self._run_post_doc(result, inputs_map_lists=inputs_map_lists, outputs_map=outputs_map,
                           mounts=mounts, canonical_output_root=canonical_output_root)

        log.debug("[runner] ===== run_custom_tool EXIT ===== | ok=", result.get("ok"))
        return result


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
    sandbox_exec: SandboxExec | None = None,
    backend: SandboxExec | None = None,   # R15: selected ExecutionBackend (default: container)
) -> Dict[str, Any]:
    """Back-compat entry point: build an :class:`ExecutionService` and run it.

    Orchestrates a single custom execution (see :class:`ExecutionService` for the phase-by-phase
    contract). Kept as a free function because the GUI router and ``core.run_custom`` export it.
    """
    return ExecutionService(
        tool_module=tool_module,
        iospec=iospec,
        verb_schema=verb_schema,
        db_map=db_map,
        context=context,
        layout_resolver=layout_resolver,
        work_dir=work_dir,
        executor=executor,
        predigest=predigest,
        sandbox_exec=sandbox_exec,
        backend=backend,
    ).run()
