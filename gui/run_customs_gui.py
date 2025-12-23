# run_customs_gui.py
# FastAPI Backend API for custom parser/pphrase testing GUI
# Provides endpoints to check file existence and execute custom tools
from __future__ import annotations

from pathlib import Path
import json
import ast
import sys
import tempfile
import traceback
import types
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi import Path as FastAPIPath, Query as FastAPIQuery
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Import your core modules
from core.core_run_customs import (
    IoSpec,
    CustomParserExecutable,
    run_custom_tool,
    default_predigest_registry,
    probe_pphrase_settings_static,
    expand_prephrase_settings_dynamic,
)
from api.manifest.resolver import resolve_path, RDS_ENABLED
from api import i_o

# Debug control - set to False to disable all backend debug logging
DEBUG_ENABLED = False  # Change to False to silence debug logs

def debug(*args, **kwargs):
    """Debug print that respects DEBUG_ENABLED flag."""
    if DEBUG_ENABLED:
        print(*args, **kwargs)


# ---------------------------------------------------------------------------
# S3-aware helpers (mirror adjective_gui style: prefer i_o, fallback to Path)
# ---------------------------------------------------------------------------

def unlink_local(path: Path):
    """
    Force a true LOCAL filesystem delete, regardless of json_proxy or RDS mode.
    json_proxy tries to treat project-root paths as S3 paths; this bypasses that.
    """
    try:
        real = Path(str(path))  # ensures absolute local path resolution
        real.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def is_rds_mode() -> bool:
    """
    True when the project is running with Postgres+S3 (RDS) backend.
    EXACTLY matches json_proxy's determination of S3 mode.
    """
    return bool(RDS_ENABLED)

def _read_text_via_io(p: Path, encoding: str = "utf-8") -> str:
    """
    Read file contents, preferring i_o.open_file (S3-aware) and falling back to local Path.
    """
    fopen = getattr(i_o, "open_file", None)
    if callable(fopen):
        try:
            debug("[io.read_text] via i_o.open_file:", str(p))
            with fopen(p, mode="rb") as fh:
                data = fh.read()
            return data.decode(encoding, errors="replace")
        except FileNotFoundError:
            debug("[io.read_text][miss] not found via i_o:", str(p))
            raise
        except Exception as e:
            debug("[io.read_text][warn] i_o.open_file failed, fallback to Path:", repr(e))
    debug("[io.read_text] via Path.read_text:", str(p))
    return Path(p).read_text(encoding=encoding)

def _path_exists_via_io(p: Path) -> bool:
    """
    Fast existence check using i_o.fs_exists when present, else fallback to Path.exists().
    """
    fs_exists = getattr(i_o, "fs_exists", None)
    if callable(fs_exists):
        try:
            exists = bool(fs_exists(p))
            debug("[io.exists] via i_o.fs_exists:", str(p), "->", exists)
            return exists
        except Exception as e:
            debug("[io.exists][warn] fs_exists failed; fallback:", repr(e))
    try:
        exists = Path(p).exists()
        debug("[io.exists] via Path.exists:", str(p), "->", exists)
        return exists
    except Exception as e:
        debug("[io.exists][error] Path.exists failed:", repr(e))
        return False

def _module_from_source(name: str, source: str, file_hint: Path) -> types.ModuleType:
    """
    Create a Python module from source text without relying on filesystem import.
    Sets __file__ to a meaningful hint for tracebacks.
    Always refreshes the module namespace to avoid stale code.
    """
    # Ensure a fresh slot in sys.modules (no stale cache)
    if name in sys.modules:
        debug("[module.load][refresh] removing previous module from sys.modules:", name)
        try:
            del sys.modules[name]
        except Exception as e:
            debug("[module.load][refresh][warn] could not delete old module:", repr(e))

    debug("[module.load] compiling source for", name, "file_hint=", str(file_hint))
    mod = types.ModuleType(name)
    mod.__file__ = str(file_hint)
    mod.__package__ = None
    exec(compile(source, filename=str(file_hint), mode="exec"), mod.__dict__)
    # Place fresh module in sys.modules under unique name
    sys.modules[name] = mod
    debug("[module.load] module exec OK ->", name)
    return mod

# ---------------------------------------------------------------------------
# Router / config
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/parser_test", tags=["parser_test"])

# Configuration
CUSTOM_DIR = Path("custom")  # Dev-only helper; not used for S3 projects.

def get_project_path(project: str) -> Path:
    """
    Resolve /projects/<project> using layout map (no hard-coding).
    DO NOT call .resolve() here; let S3 paths remain virtual.
    """
    projects_root = resolve_path(Path(), "project_root")
    return (projects_root / project)

# Pydantic models for request bodies
class TestParserRequest(BaseModel):
    params: Dict[str, Any] = {}

# ---------------------------------------------------------------------------
# S3-aware module loading (no direct FS import) — ALWAYS FRESH
# ---------------------------------------------------------------------------

def load_custom_module(project: str, parser_name: str, parser_type: str):
    """
    Load a custom parser or prepositional phrase module from project storage.

    ALWAYS REFRESH:
      - Read latest source via i_o.open_file (if available)
      - Compile into a uniquely-named module to avoid reuse
      - Purge any stale module with the same stem before load
    """
    project_root = resolve_path(Path(), "project_root")
    project_path = project_root / project

    if parser_type == "custom_parser":
        base_dir = resolve_path(project_path, "custom_parser_dir")
    elif parser_type == "prep_phrase_parser":
        base_dir = resolve_path(project_path, "prepositional_phrases_dir")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown parser type: {parser_type}")

    module_path = base_dir / parser_name / f"{parser_name}.py"

    # Read source via S3-aware helper
    try:
        debug("[module.load] reading source directly from local filesystem:", str(module_path))
        with open(module_path, "r", encoding="utf-8") as f:
            src = f.read()
        debug("[module.load] source length:", len(src))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Custom parser not found: {module_path}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load module source: {e}")

    # Unique module namespace per run to guarantee freshness
    unique_mod_name = f"{parser_name}__loaded_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}"
    debug("[module.load] unique module name:", unique_mod_name)

    # Exec into a module object (fresh)
    try:
        module = _module_from_source(unique_mod_name, src, module_path)
    except Exception as e:
        debug("[module.load][error] exec failed:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Could not import {parser_name}: {e}")

    return module

# ---------------------------------------------------------------------------
# Utilities for test scaffolding (tempfile-based only; ephemeral)
# ---------------------------------------------------------------------------

def get_dummy_verb_schema(kind: str) -> dict:
    """Get a basic verb schema for testing purposes"""
    base_schema = {
        "data_entry_schema": {
            "raw_data_inputs": ["test_folder"],
            "file_inputs": ["test_file.json"],
            "interpretation": {
                "tabs": ["Results", "Summary"]
            }
        }
    }

    if kind == "pphrase":
        base_schema["verb_group"] = "Tests"

    return base_schema

def get_dummy_db_map() -> dict:
    """Get a basic database map for testing"""
    return {
        "test_endpoint": "/api/test/{param1}/{param2}",
        "sample_query": "/api/samples?type={sample_type}"
    }

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/check_parser/{project}/{parser_name}")
async def check_parser_exists(project: str, parser_name: str, type: str):
    """Check if a custom parser or prepositional phrase file exists (S3-aware)."""
    try:
        project_root = resolve_path(Path(), "project_root")
        project_path = project_root / project

        if type == "custom_parser":
            base_dir = resolve_path(project_path, "custom_parser_dir")
        elif type == "prep_phrase_parser":
            base_dir = resolve_path(project_path, "prepositional_phrases_dir")
        else:
            raise HTTPException(status_code=400, detail=f"Unknown parser type: {type}")

        module_path = base_dir / parser_name / f"{parser_name}.py"

        # Existence check via S3-aware reader
        exists = _path_exists_via_io(module_path)

        details = {
            "exists": exists,
            "path": str(module_path),
            "parser_name": parser_name
        }

        if exists:
            try:
                # Load FRESH every time
                module = load_custom_module(project, parser_name, type)
                has_tool = hasattr(module, 'TOOL')
                has_run = hasattr(module, 'run')

                details.update({
                    "has_tool": has_tool,
                    "has_run": has_run,
                    "valid": has_tool and has_run
                })

                if has_tool:
                    tool_spec = module.TOOL
                    if isinstance(tool_spec, dict):
                        details["tool_spec"] = tool_spec
                    elif hasattr(tool_spec, '__dataclass_fields__'):
                        details["tool_spec"] = asdict(tool_spec)
                    else:
                        details["tool_spec"] = str(tool_spec)

            except Exception as e:
                details.update({
                    "load_error": str(e),
                    "valid": False
                })

        return JSONResponse(content=details)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/test_parser/{project}/{parser_name}")
async def test_custom_parser(
    project: str,
    parser_name: str,
    request: TestParserRequest,
    parser_type: str = FastAPIQuery("custom_parser", description="custom_parser | prep_phrase_parser"),
    exec_mode: str = FastAPIQuery("wasm", description="native | wasm"),
    python_wasm: Optional[str] = FastAPIQuery(None, description="Optional path to Python WASI .wasm when exec_mode=wasm"),
    verb_group: Optional[str] = FastAPIQuery(None, description="Required when TOOL.kind='parser'"),
    run_id: Optional[str] = FastAPIQuery(None, description="Required when TOOL.kind='parser'"),
):
    import json
    import tempfile
    from pathlib import Path

    debug("[parser.test] -> start",
          f"project={project!r}",
          f"parser_name={parser_name!r}",
          f"parser_type={parser_type!r}",
          f"exec_mode={exec_mode!r}",
          f"python_wasm={(python_wasm or 'None')!r}",
          f"verb_group={(verb_group or 'None')!r}",
          f"run_id={(run_id or 'None')!r}",
          f"params={getattr(request, 'params', None)}")

    # Helper kept for PARSER branch only
    def _resolve_effective_verb_schema(project_path, verb_schema_all, verb_group, run_id, parser_name):
        try:
            if verb_group and run_id:
                log_cfg = i_o.get_verb_group_log_config(project_path, verb_group)
                primary = log_cfg.get("primary_id", "run_ID")
                entries = i_o.load_verb_group_log(project_path, verb_group)
                match = next((e for e in entries if str(e.get(primary)) == str(run_id)), None)
                if match:
                    for key in ("test_type", "verb", "verb_name"):
                        vname = match.get(key)
                        if vname and vname in verb_schema_all:
                            debug("[parser.test][schema] resolved from run log:", vname)
                            return verb_schema_all[vname]
        except Exception as e:
            debug("[parser.test][schema][warn] run->verb resolution failed:", repr(e))

        try:
            candidates = []
            for vname, vs in verb_schema_all.items():
                parsers = (
                    vs.get("data_entry_schema", {})
                      .get("interpretation", {})
                      .get("parsers", [])
                )
                if isinstance(parsers, list) and parser_name in parsers:
                    candidates.append(vs)
            if len(candidates) == 1:
                debug("[parser.test][schema] resolved by parser mapping (unique)")
                return candidates[0]
            elif len(candidates) > 1:
                debug("[parser.test][schema][error] parser maps to multiple verbs")
                raise HTTPException(
                    status_code=422,
                    detail=f"Parser '{parser_name}' is used by multiple verbs; provide a run_id for disambiguation."
                )
        except HTTPException:
            raise
        except Exception as e:
            debug("[parser.test][schema][warn] parser->verb fallback failed:", repr(e))

        raise HTTPException(
            status_code=422,
            detail=("Could not resolve a single verb schema for this request. "
                    "Ensure the run_id points to the correct test (e.g., Potency_Test) "
                    "or declare this parser under that verb’s interpretation.parsers.")
        )

    try:
        # --- Project + module ---
        project_path = get_project_path(project)
        debug("[parser.test][io] project_path", str(project_path))
        if not i_o.fs_exists(project_path):
            raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

        debug("[parser.test][load] loading custom module (ALWAYS FRESH)", f"type={parser_type}", f"name={parser_name}")
        module = load_custom_module(project, parser_name, parser_type)
        debug("[parser.test][load] module loaded OK", getattr(module, "__file__", "<no __file__>"))

        # --- IoSpec ---
        if hasattr(module, "get_io_spec") and callable(module.get_io_spec):
            spec_dict = module.get_io_spec()
            iospec = IoSpec(**spec_dict)
            debug("[parser.test][io_spec] from get_io_spec()")
        elif hasattr(module, "TOOL"):
            tool_spec = module.TOOL
            if isinstance(tool_spec, IoSpec):
                iospec = tool_spec
                debug("[parser.test][io_spec] TOOL is IoSpec instance")
            elif isinstance(tool_spec, dict):
                allowed = {"kind", "raw_folders", "file_inputs", "outputs", "extra"}
                extra_keys = set(tool_spec.keys()) - allowed
                if extra_keys:
                    raise HTTPException(
                        status_code=400,
                        detail=f"TOOL contains non-IoSpec keys {sorted(extra_keys)}. "
                               f"Expose IoSpec via get_io_spec() or limit TOOL to {sorted(allowed)}."
                    )
                iospec = IoSpec(**tool_spec)
                debug("[parser.test][io_spec] TOOL dict matched IoSpec fields")
            else:
                raise HTTPException(status_code=400, detail="TOOL must be IoSpec or dict")
        else:
            raise HTTPException(status_code=400, detail="Module missing IoSpec (get_io_spec or IoSpec-like TOOL)")

        # Metadata (optional)
        meta = {}
        if hasattr(module, "get_metadata") and callable(module.get_metadata):
            try:
                meta = module.get_metadata() or {}
            except Exception:
                meta = {}
        elif hasattr(module, "TOOL") and isinstance(module.TOOL, dict):
            meta = {k: module.TOOL[k] for k in ("name", "version", "about") if k in module.TOOL}
        if meta:
            debug("[parser.test][meta]", meta)

        debug("[parser.test][TOOL] kind", iospec.kind,
              "| raw_folders:", getattr(iospec, "raw_folders", None),
              "| file_inputs:", getattr(iospec, "file_inputs", None),
              "| outputs:", getattr(iospec, "outputs", None),
              "| extra:", getattr(iospec, "extra", None))

        is_parser  = (iospec.kind == "parser")
        is_pphrase = (iospec.kind == "pphrase")

        # ---- PARSER branch: require run context + verb schema ----
        if is_parser:
            debug("[parser.test][policy] parser kind detected; validating verb_group/run_id")
            if not verb_group or not run_id:
                raise HTTPException(
                    status_code=422,
                    detail="Custom parser requires verb_group and run_id (query params)."
                )

            debug("[parser.test][schema] loading verb schema...")
            verb_schema_all = i_o.load_schema(project_path, "verb")
            debug("[parser.test][schema] verb schema loaded")

            effective_schema = _resolve_effective_verb_schema(
                project_path, verb_schema_all, verb_group, run_id, parser_name
            )
            dri = effective_schema.get("data_entry_schema", {}).get("raw_data_inputs", [])
            tabs = effective_schema.get("data_entry_schema", {}).get("interpretation", {}).get("tabs", [])
            debug("[parser.test][schema] effective.raw_data_inputs=", dri, "| effective.tabs=", tabs)
        else:
            debug("[parser.test][schema] skip verb schema for pphrase")
            effective_schema = {}

        # (Optional) db map if declared
        db_map = None
        if isinstance(iospec.extra, dict) and iospec.extra.get("db_inputs"):
            debug("[parser.test][db] tool declares db_inputs; probing loaders")
            loaders = [
                getattr(i_o, "load_db_map", None),
                getattr(i_o, "load_local_layout_map", None),
            ]
            for loader in loaders:
                if callable(loader):
                    debug("[parser.test][db] trying loader:", loader.__name__)
                    try:
                        loaded = loader(project_path)
                        debug("[parser.test][db] loader returned type:", type(loaded).__name__)
                        if isinstance(loaded, dict):
                            if "db_map" in loaded and isinstance(loaded["db_map"], dict):
                                db_map = loaded["db_map"]
                            elif "db_endpoints" in loaded and isinstance(loaded["db_endpoints"], dict):
                                db_map = loaded["db_endpoints"]
                            else:
                                db_map = loaded
                            if db_map:
                                debug("[parser.test][db] using keys:", list(db_map.keys())[:10])
                                break
                    except Exception as le:
                        debug("[parser.test][db][warn] loader failed:", loader.__name__, repr(le))
            if not db_map:
                debug("[parser.test][db][warn] db_inputs declared, but no db_map discovered (validator may error)")
                db_map = None

        # ------------ Layout resolver ------------
        # Capture pphrase output root (for later COA move to S3)
        _resolved_pphrase_output_root: Optional[Path] = None

        def layout_resolver(logical_mounts: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
            nonlocal _resolved_pphrase_output_root
            debug("[parser.test][layout] resolve begin",
                  "| inputs:", list((logical_mounts.get("inputs") or {}).keys()),
                  "| outputs:", list((logical_mounts.get("outputs") or {}).keys()))
            resolved: Dict[str, Dict[str, Any]] = {"inputs": {}, "outputs": {}}

            def _extract_run_ids(slot_params: Dict[str, Any]) -> List[str]:
                rid_val = slot_params.get("run_id")
                if isinstance(rid_val, str) and rid_val.strip():
                    return [rid_val.strip()]
                if isinstance(rid_val, (list, tuple, set)):
                    return sorted([str(x) for x in rid_val if str(x).strip()])
                return []

            def _mount_parser_like_inputs(vg: str, rid: str):
                run_root = resolve_path(project_path, "data_dump_dir", verb_group=vg, run_id=rid)
                debug("[parser.test][layout][helper] run_root ->", str(run_root))
                for alias, meta in (logical_mounts.get("inputs") or {}).items():
                    slot = meta["slot"]; mode = meta["mode"]; k = slot.get("kind")
                    if alias in resolved["inputs"]:
                        continue
                    if k == "raw_folder":
                        folder_path = (run_root / slot["name"])
                        if not i_o.fs_is_dir(folder_path):
                            raise HTTPException(status_code=404,
                                detail=f"Raw folder '{slot['name']}' not found under {run_root}")
                        resolved["inputs"][alias] = {"paths": [folder_path], "mode": mode, "slot": slot}
                    elif k == "data_entry":
                        path = resolve_path(project_path, "data_entry", verb_group=vg, run_id=rid)
                        resolved["inputs"][alias] = {"path": path, "mode": mode, "slot": slot}
                    elif k == "adverbs":
                        path = resolve_path(project_path, "adverb_file", verb_group=vg, run_id=rid)
                        resolved["inputs"][alias] = {"path": path, "mode": mode, "slot": slot}
                    elif k == "status":
                        path = resolve_path(project_path, "status_file", verb_group=vg, run_id=rid)
                        resolved["inputs"][alias] = {"path": path, "mode": mode, "slot": slot}
                    elif k == "interpretation":
                        fname = Path(slot["name"]).name
                        file_path = (run_root / fname)
                        resolved["inputs"][alias] = {"path": file_path, "mode": mode, "slot": slot}
                    elif k == "file":
                        fname = Path(slot["name"]).name
                        file_path = (run_root / fname)
                        resolved["inputs"][alias] = {"path": file_path, "mode": mode, "slot": slot}

            if iospec.kind == "parser":
                vg = verb_group  # validated above
                rid = run_id
                _mount_parser_like_inputs(vg, rid)

                # Outputs for parsers go to the run’s dump dir (paths only; writing happens in tool)
                run_root = resolve_path(project_path, "data_dump_dir", verb_group=vg, run_id=rid)
                for alias, meta in (logical_mounts.get("outputs") or {}).items():
                    slot = meta["slot"]; mode = meta["mode"]; k = slot.get("kind")
                    debug("[parser.test][layout][out]", alias, f"kind={k}", f"mode={mode}", f"slot={slot}")
                    if k == "interpretation":
                        fname = Path(slot["name"]).name
                        file_path = (run_root / fname)
                        resolved["outputs"][alias] = {"path": file_path, "mode": mode, "slot": slot}
                    elif k == "pphrase_output_root":
                        folder = slot.get("folder")
                        if folder is None:
                            debug("[parser.test][layout][out] pphrase_output_root folder is None; skipping")
                            continue
                        else:
                            folder_path = (run_root / folder)
                            resolved["outputs"][alias] = {"path": folder_path, "mode": mode, "slot": slot}
                    else:
                        raise HTTPException(status_code=400, detail=f"Unhandled output slot kind: {k}")

                debug("[parser.test][layout] resolve done")
                return resolved

            # -------- PPHRASE layout (db_endpoint aware) --------
            # Inputs
            for alias, meta in (logical_mounts.get("inputs") or {}).items():
                slot = meta["slot"]; mode = meta["mode"]; k = slot.get("kind")
                debug("[parser.test][layout][in]", alias, f"kind={k}", f"mode={mode}", f"slot={slot}")

                if k in {"raw_folder", "data_entry", "adverbs", "status", "interpretation", "file"}:
                    # For pphrase, these are run-bound; require run context.
                    if not (verb_group and run_id):
                        raise HTTPException(
                            status_code=422,
                            detail=("This pre-phrase declares run-bound inputs but no verb_group/run_id were provided. "
                                    "Supply them to mount DataEntry/adverbs/raw folders, or remove those inputs.")
                        )
                    _mount_parser_like_inputs(verb_group, run_id)
                    continue

                if k == "db_endpoint":
                    endpoint = slot.get("endpoint")
                    params = dict(slot.get("params") or {})
                    if not isinstance(db_map, dict) or endpoint not in db_map:
                        raise HTTPException(status_code=422, detail=f"Unknown db endpoint '{endpoint}'")

                    template = db_map[endpoint]

                    if endpoint == "data_dump_dir":
                        vg = params.get("verb_group") or verb_group
                        if not vg:
                            raise HTTPException(status_code=422, detail="data_dump_dir requires 'verb_group'")

                        run_ids = _extract_run_ids(params)
                        if run_ids:
                            paths = []
                            for rid in run_ids:
                                rel = template.format(verb_group=vg, run_id=rid)
                                paths.append((project_path / rel))
                            resolved["inputs"][alias] = {"paths": paths, "mode": mode, "slot": slot}
                            debug("[parser.test][layout][in][db_endpoint] data_dump_dir multi-run ->", [str(p) for p in paths])
                        else:
                            base_tpl = template.rsplit("/{run_id}", 1)[0] if "/{run_id}" in template else template
                            rel = base_tpl.format(verb_group=vg)
                            path = (project_path / rel)
                            resolved["inputs"][alias] = {"path": path, "mode": mode, "slot": slot}
                            debug("[parser.test][layout][in][db_endpoint] data_dump_dir base ->", str(path))
                    else:
                        try:
                            rel = template.format(**params)
                        except KeyError as e:
                            raise HTTPException(
                                status_code=422,
                                detail=f"Missing param '{e.args[0]}' for endpoint '{endpoint}'"
                            )
                        path = (project_path / rel)
                        resolved["inputs"][alias] = {"path": path, "mode": mode, "slot": slot}
                        debug("[parser.test][layout][in][db_endpoint] ->", str(path))
                    continue

                raise HTTPException(status_code=400, detail=f"Unhandled input slot kind for pphrase: {k}")

            # Outputs (pphrase outputs under canonical phrase base) — paths only; tool writes.
            for alias, meta in (logical_mounts.get("outputs") or {}).items():
                slot = meta["slot"]; mode = meta["mode"]; k = slot.get("kind")
                debug("[parser.test][layout][out]", alias, f"kind={k}", f"mode={mode}", f"slot={slot}")

                if k == "pphrase_output_root":
                    pname = (parser_name or "pphrase")
                    folder = slot.get("folder")  # may be None or ""
                    canon_base = ctx_dict.get("canonical_phrase_base")

                    if not canon_base:
                        # use local scratch area (ephemeral) for missing canonical base
                        scratch_root = (Path(tempfile.gettempdir()) / "pphrase_test_scratch")
                        base_dir = (scratch_root / pname)
                        debug("[parser.test][layout][out][warn] missing canonical base; using scratch ->", str(base_dir))
                    else:
                        base_dir = (Path(canon_base) / pname)

                    rel = (folder.strip("/") if isinstance(folder, str) else "")
                    out_dir = (base_dir / rel) if rel else base_dir
                    # Do not create dirs here; the tool will write/mkdir as needed.
                    resolved["outputs"][alias] = {"path": out_dir, "mode": mode, "slot": slot}
                    _resolved_pphrase_output_root = out_dir  # capture for post-run S3 mirroring
                    debug("[parser.test][layout][out] captured pphrase output root ->", str(_resolved_pphrase_output_root))
                else:
                    raise HTTPException(status_code=400, detail=f"Unhandled output slot kind for pphrase: {k}")

            debug("[parser.test][layout] resolve done")
            return resolved

        # ---- Context for orchestrator ----
        canonical_phrase_base = None
        if is_pphrase:
            try:
                canonical_phrase_base = resolve_path(project_path, "prepositional_phrase_output_dir")
                debug("[parser.test][ctx] canonical_phrase_base resolved ->", str(canonical_phrase_base))
            except Exception as e:
                debug("[parser.test][ctx][warn] could not resolve canonical_phrase_base:", repr(e))
                canonical_phrase_base = None

        # --- collect run_ids from request.params ---
        extracted_run_ids: list[str] = []
        for v in (request.params or {}).values():
            if isinstance(v, dict) and "_runID" in v:
                extracted_run_ids.append(v["_runID"])
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict) and "_runID" in item:
                        extracted_run_ids.append(item["_runID"])
        extracted_run_ids = list(dict.fromkeys(extracted_run_ids))

        # Helper to provide noun items (S3-aware via i_o.get_noun_items)
        def _fetch_items_for_context(noun_type: str) -> List[Dict[str, Any]]:
            try:
                return i_o.get_noun_items(project_path, noun_type)
            except Exception:
                return []

        ctx_dict: Dict[str, Any] = {
            "verb_group": verb_group,
            "run_id": run_id,
            "pphrase_name": parser_name if is_pphrase else None,
            "params": {**(request.params or {}), "run_ids": extracted_run_ids},
            "project_path": project_path,
            "canonical_phrase_base": canonical_phrase_base,
            "fetch_noun_items": _fetch_items_for_context,  # INJECTED
        }

        debug("[parser.test][ctx]", ctx_dict)

        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            debug("[parser.test][workdir] created", str(work_dir))

            # SANDBOX: always fresh instance / adapter
            sandbox_exec = None
            used_exec_mode = "native"
            debug("[parser.test][exec] requested exec_mode:", exec_mode)
            if exec_mode.lower() == "wasm":
                try:
                    debug("[parser.test][exec][wasm] creating fresh wasmtime sandbox")
                    from core.core_run_customs import make_wasmtime_sandbox
                    sandbox_exec = make_wasmtime_sandbox(python_wasm)
                    used_exec_mode = "wasm"
                    debug("[parser.test][exec][wasm] sandbox created OK (fresh)")
                except Exception as se:
                    debug("[parser.test][exec][wasm][error] sandbox unavailable:", repr(se))
                    return JSONResponse(
                        status_code=400,
                        content={"ok": False, "error": f"WASM mode requested but unavailable: {se}"}
                    )
            else:
                import inspect, os as _os
                try:
                    run_fn = getattr(module, "run", None)
                    if callable(run_fn):
                        try:
                            sig = inspect.signature(run_fn)
                        except Exception:
                            sig = None
                        if sig and len(sig.parameters) == 0:
                            debug("[parser.test][compat] Detected zero-arg run(); installing fresh adapter for native execution")
                            _orig_run = run_fn
                            def _adapted_run(ctx):
                                # IMPORTANT: ensure we set a fresh mapping every run
                                env_map = {
                                    "kind": iospec.kind,
                                    "inputs": ctx.inputs,
                                    "outputs": ctx.outputs,
                                    "params": ctx.params or {},
                                }
                                debug("[parser.test][compat] setting GIMS_IO_JSON for adapter (fresh) with keys:", list(env_map.keys()))
                                _os.environ["GIMS_IO_JSON"] = json.dumps(env_map)
                                return _orig_run()
                            module.run = _adapted_run
                except Exception as ce:
                    debug("[parser.test][compat][warn] adapter install failed:", repr(ce))

            # ---- Run orchestrator ----
            debug("[parser.test][run] orchestrator call begin (fresh sandbox/module)")
            result = run_custom_tool(
                tool_module=module,
                iospec=iospec,
                verb_schema=effective_schema,   # {} for pphrase
                db_map=db_map,
                context=ctx_dict,               # includes canonical base
                layout_resolver=layout_resolver,
                work_dir=work_dir,
                executor=None,
                predigest=None,
                sandbox_exec=sandbox_exec,
            )
            if not result.get("ok"):
                debug("[parser.test][error]", result.get("error"))
                if result.get("logs"):
                    debug("[parser.test][logs]", result.get("logs"))

            # --------- If PPHRASE: mirror outputs to S3 and delete local (CUT & PASTE) ----------
            # We rely on the manifest that postdoc_render writes: _postdoc_outputs.json
            if is_pphrase and result.get("ok"):
                try:
                    # A) Determine output root where postdoc wrote files (from our resolved root)
                    out_root = _resolved_pphrase_output_root
                    debug("[pphrase.mirror] resolved output root captured:", str(out_root) if out_root else None)

                    # B) Locate manifest (stable filename)
                    manifest_path = (out_root / "_postdoc_outputs.json") if out_root else None
                    if not manifest_path or not Path(manifest_path).exists():
                        debug("[pphrase.mirror][warn] manifest not found at captured root, scanning for fallback...")
                        # Fallback scan: look in canonical base / parser_name
                        try:
                            cand_base = (resolve_path(project_path, "prepositional_phrase_output_dir") / (parser_name or "pphrase"))
                            cand_manifest = cand_base / "_postdoc_outputs.json"
                            if Path(cand_manifest).exists():
                                manifest_path = cand_manifest
                                debug("[pphrase.mirror] found manifest at canonical base:", str(manifest_path))
                        except Exception as se:
                            debug("[pphrase.mirror][warn] canonical base check failed:", repr(se))

                    if not manifest_path or not Path(manifest_path).exists():
                        debug("[pphrase.mirror][abort] no manifest; skip mirroring")
                    else:
                        debug("[pphrase.mirror] reading manifest:", str(manifest_path))
                        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
                        created = manifest.get("created") or []
                        debug("[pphrase.mirror] created entries:", len(created))

                        # Destination base is the canonical S3 phrase output base:
                        # s3_base = prepositional_phrase_output_dir / <pphrase_name> (and the same date/client subpaths)
                        s3_phrase_base = resolve_path(project_path, "prepositional_phrase_output_dir") / (parser_name or "pphrase")
                        debug("[pphrase.mirror] S3 phrase base:", str(s3_phrase_base))

                        for idx, entry in enumerate(created, start=1):
                            src_path = Path(entry.get("path", ""))
                            if not src_path.exists():
                                debug(f"[pphrase.mirror][{idx}] SKIP missing src:", str(src_path))
                                continue

                            # Compute relative path from local out_root to preserve identical subfolders
                            try:
                                rel = src_path.relative_to(out_root) if out_root else src_path.name
                            except Exception:
                                # If we can't relativize, just use file name
                                rel = src_path.name
                            dest_path = s3_phrase_base / Path(rel)
                            debug(f"[pphrase.mirror][{idx}] src ->", str(src_path))
                            debug(f"[pphrase.mirror][{idx}] dst ->", str(dest_path))

                            # Ensure S3 parent exists, then write bytes
                            parent = dest_path.parent
                            try:
                                debug(f"[pphrase.mirror][{idx}] ensuring parent exists:", str(parent))
                                i_o.fs_makedirs(parent, exist_ok=True)
                            except Exception as me:
                                debug(f"[pphrase.mirror][{idx}][warn] fs_makedirs failed:", repr(me))

                            data = None
                            try:
                                data = src_path.read_bytes()
                                debug(f"[pphrase.mirror][{idx}] read {len(data)} bytes from local")
                            except Exception as re:
                                debug(f"[pphrase.mirror][{idx}][error] reading local src failed:", repr(re))
                                continue

                            try:
                                i_o.fs_write_bytes(dest_path, data)
                                debug(f"[pphrase.mirror][{idx}] wrote {len(data)} bytes to S3:", str(dest_path))
                            except Exception as we:
                                debug(f"[pphrase.mirror][{idx}][error] write to S3 failed:", repr(we))
                                continue

                            # CUT: delete local file after successful S3 write
                            try:
                                if is_rds_mode():
                                    # RDS ON → delete local file directly
                                    if unlink_local(src_path):
                                        debug(f"[pphrase.mirror][{idx}] deleted local file (RDS mode): {src_path}")
                                    else:
                                        debug(f"[pphrase.mirror][{idx}][warn] failed to delete local file (RDS mode): {src_path}")
                                else:
                                    # Local mode → keep the local output
                                    debug(f"[pphrase.mirror][{idx}] local mode; skipping delete: {src_path}")

                            except Exception as de:
                                debug(f"[pphrase.mirror][{idx}][fatal] unexpected error during delete:", repr(de))

                        # Delete manifest (always local)
                        try:
                            if unlink_local(manifest_path):
                                debug("[pphrase.mirror] deleted local manifest:", str(manifest_path))
                            else:
                                debug("[pphrase.mirror][warn] failed to delete manifest:", str(manifest_path))

                            # Also delete timestamped manifest snapshots
                            for snap in Path(manifest_path).parent.glob("_postdoc_outputs_*.json"):
                                if unlink_local(snap):
                                    debug("[pphrase.mirror] deleted manifest snapshot:", str(snap))

                        except Exception as me:
                            debug("[pphrase.mirror][warn] manifest deletion failed:", repr(me))

                        # Best-effort: remove now-empty directories under out_root
                        try:
                            def _prune_empty_dirs(root: Path):
                                # Walk bottom-up
                                for p in sorted(root.rglob("*"), key=lambda x: len(str(x)), reverse=True):
                                    try:
                                        if p.is_dir() and not any(p.iterdir()):
                                            p.rmdir()
                                            debug("[pphrase.mirror] pruned empty dir:", str(p))
                                    except Exception:
                                        pass
                            if out_root and out_root.exists():
                                _prune_empty_dirs(out_root)
                                # Try to remove the root if now empty
                                try:
                                    if not any(out_root.iterdir()):
                                        out_root.rmdir()
                                        debug("[pphrase.mirror] removed now-empty out_root:", str(out_root))
                                except Exception:
                                    pass
                        except Exception as pr:
                            debug("[pphrase.mirror][warn] pruning dirs failed:", repr(pr))

                except Exception as mirror_e:
                    debug("[pphrase.mirror][fatal] unexpected error during S3 mirror:", repr(mirror_e))

            # --------- If PARSER: mirror interpretation outputs to S3 (IDENTICAL STYLE TO PPHRASE) ----------
            if not is_pphrase and result.get("ok"):
                try:
                    produced_files = result.get("produced", [])
                    if not produced_files:
                        debug("[parser.mirror][warn] no produced files to mirror")
                    else:
                        debug("[parser.mirror] produced files:", [str(p) for p in produced_files])

                        for idx, src_path in enumerate(produced_files, start=1):
                            src_path = Path(src_path)

                            if not src_path.exists():
                                debug(f"[parser.mirror][{idx}] SKIP missing src:", str(src_path))
                                continue

                            # Destination is <run_root>/interpretation/<file>
                            dest_path = src_path.parent / src_path.name

                            debug(f"[parser.mirror][{idx}] src -> {src_path}")
                            debug(f"[parser.mirror][{idx}] dst -> {dest_path}")

                            # Ensure parent exists (json_proxy handles S3 or local)
                            try:
                                i_o.fs_makedirs(dest_path.parent, exist_ok=True)
                            except Exception as me:
                                debug(f"[parser.mirror][{idx}][warn] fs_makedirs failed:", repr(me))

                            # Read from local filesystem
                            try:
                                data = src_path.read_bytes()
                                debug(f"[parser.mirror][{idx}] read {len(data)} bytes from local")
                            except Exception as re:
                                debug(f"[parser.mirror][{idx}][error] reading local src failed:", repr(re))
                                continue

                            # Write to S3 (or local if RDS disabled)
                            try:
                                i_o.fs_write_bytes(dest_path, data)
                                debug(f"[parser.mirror][{idx}] wrote {len(data)} bytes to S3:", str(dest_path))
                            except Exception as we:
                                debug(f"[parser.mirror][{idx}][error] write to S3 failed:", repr(we))
                                continue

                            # CUT local file (only in RDS mode)
                            try:
                                if is_rds_mode():
                                    try:
                                        src_path.unlink()
                                        debug(f"[parser.mirror][{idx}] deleted local file (RDS mode): {src_path}")
                                    except Exception:
                                        debug(f"[parser.mirror][{idx}][warn] failed to delete local file (RDS mode): {src_path}")
                                else:
                                    debug(f"[parser.mirror][{idx}] local mode; skipping delete: {src_path}")

                            except Exception as de:
                                debug(f"[parser.mirror][{idx}][fatal] unexpected error during delete:", repr(de))

                except Exception as mirror_e:
                    debug("[parser.mirror][fatal] unexpected error during S3 mirror:", repr(mirror_e))

            payload = {
                "ok": bool(result.get("ok")),
                "exec_mode": used_exec_mode,
                "produced": result.get("produced", []),
                "error": result.get("error"),
                "post_doc": result.get("post_doc"),
                "logs": result.get("logs", []),
            }
            debug("[parser.test][return] payload keys:", list(payload.keys()))
            return JSONResponse(status_code=200 if payload["ok"] else 500, content=payload)

    except HTTPException:
        raise
    except Exception as e:
        debug("[parser.test][fatal]", repr(e))
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@router.post("/test_prep_phrase/{project}")
async def test_prep_phrase_parser(project: str, request: TestParserRequest):
    """Test the prepositional phrase parser"""
    return await test_custom_parser(project, "prep_phrase_parser", request)

@router.get("/list_custom_parsers")
async def list_custom_parsers(
    project: Optional[str] = FastAPIQuery(None, description="Project name")
):
    """
    List available custom parsers for a project by using the layout map.
    Uses S3-aware fs_* helpers for directory enumeration.
    """
    try:
        parsers: List[Dict[str, Any]] = []

        # Resolve the project path
        if project:
            project_path = get_project_path(project)
            if not i_o.fs_exists(project_path):
                raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
        else:
            project_path = resolve_path(Path(), "project_root")

        # Preferred: mapped custom parsers dir inside the project
        try:
            parser_root = resolve_path(project_path, "custom_parser_dir")
        except Exception:
            parser_root = None

        if parser_root and i_o.fs_exists(parser_root) and i_o.fs_is_dir(parser_root):
            for d in sorted(i_o.fs_iterdir(parser_root), key=lambda p: p.name.lower()):
                if i_o.fs_is_dir(d):
                    mod = d / f"{d.name}.py"
                    if _path_exists_via_io(mod):
                        parsers.append({"name": d.name, "path": str(mod), "exists": True})

        return JSONResponse(content={"parsers": parsers})

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/list_prepositional_phrases")
async def list_prepositional_phrases(
    project: Optional[str] = FastAPIQuery(None, description="Project name")
):
    """
    List available prepositional phrases for a project by using the layout map.
    Uses S3-aware fs_* helpers for directory enumeration.
    """
    try:
        pphrases: List[Dict[str, Any]] = []

        # Resolve the project path
        if project:
            project_path = get_project_path(project)
            if not i_o.fs_exists(project_path):
                raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
        else:
            project_path = resolve_path(Path(), "project_root")

        # Preferred: mapped custom pphrases dir inside the project
        try:
            pphrase_root = resolve_path(project_path, "prepositional_phrases_dir")
        except Exception:
            pphrase_root = None

        if pphrase_root and i_o.fs_exists(pphrase_root) and i_o.fs_is_dir(pphrase_root):
            for d in sorted(i_o.fs_iterdir(pphrase_root), key=lambda p: p.name.lower()):
                if i_o.fs_is_dir(d):
                    mod = d / f"{d.name}.py"
                    if _path_exists_via_io(mod):
                        pphrases.append({"name": d.name, "path": str(mod), "exists": True})

        return JSONResponse(content={"pphrases": pphrases})

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/project_info/{project}")
async def get_project_info(project: str):
    """Get basic project information (S3-aware existence checks)."""
    try:
        project_path = get_project_path(project)

        info = {
            "project": project,
            "project_path": str(project_path),
            "custom_dir": str(CUSTOM_DIR),
            "project_exists": bool(i_o.fs_exists(project_path)),
            "custom_dir_exists": CUSTOM_DIR.exists(),  # dev-only local
        }

        # Try to load some basic schemas (S3-aware via i_o)
        try:
            verb_schema = i_o.load_schema(project_path, "verb")
            info["verb_count"] = len(verb_schema)
        except Exception:
            info["verb_count"] = 0

        try:
            noun_schema = i_o.load_schema(project_path, "noun")
            info["noun_count"] = len(noun_schema)
        except Exception:
            info["noun_count"] = 0

        return JSONResponse(content=info)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/check_parser/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return i_o.io_list_projects()
    except Exception as e:
        print(f"[list_projects] list_projects failed: {e!r}")
        return []

@router.get("/check_parser/get_runs/{project}/{parser_name}")
def get_runs_for_parser(
    project: str = FastAPIPath(..., description="Project name"),
    parser_name: str = FastAPIPath(..., description="Parser name"),
) -> List[Dict[str, Any]]:
    """
    Find all runs for verbs that use the specified parser.

    Returns:
        List of dictionaries containing run information.
    """
    try:
        project_root = resolve_path(Path(), "project_root")
        project_path = project_root / project
        if not i_o.fs_exists(project_path):
            raise HTTPException(status_code=404, detail=f"Project '{project}' not found.")

        # Step 1: Load verb schema and find verbs that use the specified parser
        verb_schema = i_o.load_schema(project_path, "verb")
        matching_verbs: List[Dict[str, str]] = []

        for verb_name, verb_config in verb_schema.items():
            parsers = (
                verb_config.get("data_entry_schema", {}).get("interpretation", {}).get("parsers", [])
            )
            if parser_name in parsers:
                verb_group_name = verb_config.get("verb_group", "Tests")
                matching_verbs.append({"verb_name": verb_name, "verb_group": verb_group_name})

        if not matching_verbs:
            return []

        # Step 2: For each matching verb, get runs from its verb group log
        all_runs: List[Dict[str, Any]] = []
        processed_verb_groups = set()

        for verb_info in matching_verbs:
            verb_group_name = verb_info["verb_group"]

            if verb_group_name in processed_verb_groups:
                continue
            processed_verb_groups.add(verb_group_name)

            try:
                log_config = i_o.get_verb_group_log_config(project_path, verb_group_name)
                primary_id_field = log_config.get("primary_id", "run_ID")

                log_entries = i_o.load_verb_group_log(project_path, verb_group_name)

                verb_names_in_group = {v["verb_name"] for v in matching_verbs if v["verb_group"] == verb_group_name}
                for entry in log_entries:
                    test_type = entry.get("test_type")
                    if test_type in verb_names_in_group:
                        run_data = {
                            "run_id": entry.get(primary_id_field),
                            "verb": test_type,
                            "verb_group": verb_group_name,
                        }
                        for key, value in entry.items():
                            if key != primary_id_field:
                                run_data[key] = value
                        all_runs.append(run_data)

            except (FileNotFoundError, json.JSONDecodeError) as e:
                print(f"Warning: Could not process verb group '{verb_group_name}': {e}")
                continue

        return all_runs

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in get_runs_for_parser: {e}")
        return []


def get_available_parsers(project_path: Path) -> List[str]:
    """
    Get a list of all unique parsers defined across all verbs.
    """
    try:
        verb_schema = i_o.load_schema(project_path, "verb")
        parsers = set()

        for verb_config in verb_schema.values():
            verb_parsers = (
                verb_config.get("data_entry_schema", {}).get("interpretation", {}).get("parsers", [])
            )
            parsers.update(verb_parsers)

        return sorted(list(parsers))

    except Exception as e:
        print(f"Error in get_available_parsers: {e}")
        return []

@router.post("/prephrase/expand/{project}")
def expand_prephrase_endpoint(project: str, body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        pphrase_name = body.get("pphrase_name")
        if not pphrase_name:
            raise HTTPException(status_code=400, detail="Missing pphrase_name in request body")

        projects_root = resolve_path(Path(), "project_root")
        project_path = projects_root / project
        if not i_o.fs_exists(project_path):
            raise HTTPException(status_code=404, detail=f"Project '{project}' not found.")

        # resolve path to custom/prepositional phrases/{pphrase_name}/{pphrase_name}.py
        pphrase_root = resolve_path(project_path, "prepositional_phrases_dir")
        module_path = pphrase_root / pphrase_name / f"{pphrase_name}.py"
        if not _path_exists_via_io(module_path):
            raise HTTPException(status_code=404, detail=f"Prephrase '{pphrase_name}' not found")

        settings = _load_prephrase_settings(module_path)
        user_values = body.get("user_values", {})

        # Inject providers
        def _schema(noun: str):
            return i_o.get_noun_schema(project_path, noun)

        def _items(noun: str):
            return i_o.get_noun_items(project_path, noun)

        expanded = expand_prephrase_settings_dynamic(
            settings, user_values,
            fetch_noun_schema=_schema,
            fetch_noun_items=_items,
        )
        return {"ok": True, "expanded": expanded, "pphrase_name": pphrase_name}

    except HTTPException:
        raise
    except Exception as e:
        debug("[prephrase.router][error] expand failed:", repr(e))
        raise HTTPException(status_code=400, detail=f"Expand failed: {e}")

# --------------------------
# Helpers
# --------------------------

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
                            debug(f"[prephrase.router] PREPHRASE_SETTINGS literal found ({len(val)} fields)")
                            return val
                    except Exception:
                        pass
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "PREPHRASE_SETTINGS":
            try:
                val = ast.literal_eval(node.value)
                if isinstance(val, list):
                    debug(f"[prephrase.router] PREPHRASE_SETTINGS annotated literal found ({len(val)} fields)")
                    return val
            except Exception:
                pass

    # Fallback: exec source in a fresh temporary module and read settings
    tmp_name = f"pphrase_mod_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}"
    mod = _module_from_source(tmp_name, src, module_path)

    if hasattr(mod, "get_PREPHRASE_SETTINGS"):
        settings = mod.get_PREPHRASE_SETTINGS()
        debug(f"[prephrase.router] PREPHRASE_SETTINGS via function ({len(settings)} fields)")
        return settings
    if hasattr(mod, "PREPHRASE_SETTINGS"):
        settings = getattr(mod, "PREPHRASE_SETTINGS")
        if isinstance(settings, list):
            debug(f"[prephrase.router] PREPHRASE_SETTINGS via global ({len(settings)} fields)")
            return settings

    raise RuntimeError("No PREPHRASE_SETTINGS found in module")

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
    return True if v else False

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

def _should_apply(op: str, value_from_filter: Any) -> bool:
    # NO-OP rules on empty inputs
    if op == "in":
        return bool(value_from_filter) and isinstance(value_from_filter, (list, tuple, set))
    if op == "between":
        if isinstance(value_from_filter, (list, tuple)) and len(value_from_filter) == 2:
            return _is_nonempty(value_from_filter[0]) or _is_nonempty(value_from_filter[1])
        return False
    if op in {"contains", "=", "!="}:
        return _is_nonempty(value_from_filter)
    if op in {"exists", "missing", "has_pair"}:
        return True
    return False

def _apply_filter(rows: List[Dict[str, Any]], flt: Dict[str, Any], uv: Dict[str, Any], fi: int, ki: int) -> List[Dict[str, Any]]:
    op = flt.get("op")
    field = flt.get("field")
    value = flt.get("value", None)
    ref_val = _get_ref_values(flt, uv)
    value = ref_val if ref_val is not None else value

    if op in {"in", "=", "!=", "contains", "between"} and not field:
        debug(f"[expand.helpers] field[{fi}] filter[{ki}] op={op!r} missing 'field' -> NO-OP")
        return rows

    if not _should_apply(op, value):
        debug(f"[expand.helpers] field[{fi}] filter[{ki}] NO-OP (empty value) | op={op!r} value={value!r}")
        return rows

    before = len(rows)
    debug(f"[expand.helpers] field[{fi}] filter[{ki}] apply | op={op!r} field={field!r} value={value!r} rows={before}")

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
            if not _is_nonempty(x):
                return False
            sx = str(x)
            ok_lo = (not _is_nonempty(lo)) or (sx >= str(lo))
            ok_hi = (not _is_nonempty(hi)) or (sx <= str(hi))
            return ok_lo and ok_hi
        rows = [r for r in rows if in_range(r.get(field))]
    elif op == "has_pair":
        keys = value if isinstance(value, (list, tuple)) else []
        rows = [r for r in rows if all(_is_nonempty(r.get(k)) for k in keys)]
    else:
        debug(f"[expand.helpers] field[{fi}] filter[{ki}] unsupported op={op!r} -> NO-OP")
        return rows

    after = len(rows)
    debug(f"[expand.helpers] field[{fi}] filter[{ki}] done | {before} -> {after}")
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

def _sort_rows(rows: List[Dict[str, Any]], sort_specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for s in reversed(sort_specs):
        fld = s.get("field")
        direction = s.get("dir", "asc")
        reverse = direction == "desc"
        rows.sort(key=lambda r: _sort_key(r.get(fld)), reverse=reverse)
    return rows

def _sort_options(options: List[Dict[str, Any]], sort_specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for s in reversed(sort_specs):
        fld = s.get("field")
        direction = s.get("dir", "asc")
        reverse = direction == "desc"
        options.sort(key=lambda o: _sort_key(o.get(fld)), reverse=reverse)
    return options

def _sort_key(x: Any) -> Tuple[int, str]:
    """Ensure stable comparisons: None < '' < other, compare as str."""
    if x is None:
        return (0, "")
    s = str(x)
    return (1, s)

class _SafeDict(dict):
    def __missing__(self, key):  # allows "{missing}" -> ""
        return ""

def _format_template(tpl: str, row: Dict[str, Any]) -> str:
    try:
        return tpl.format_map(_SafeDict({k: "" if v is None else v for k, v in row.items()}))
    except Exception:
        return str(row)


@router.get("/schema/verb/{project}/{verb_name}/parsers")
def api_get_verb_parsers(project: str, verb_name: str) -> Dict[str, Any]:
    """
    Return the parsers declared for a verb (from data_entry_schema.interpretation.parsers).
    """
    project_root = resolve_path(Path(), "project_root")
    project_path = (project_root / project)
    if not i_o.fs_exists(project_path):
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    # S3-aware schema access
    schema = i_o.get_verb_schema(project_path, verb_name)
    if not schema:
        raise HTTPException(status_code=404, detail=f"Verb '{verb_name}' not found")

    raw = (
        schema.get("data_entry_schema", {})
              .get("interpretation", {})
              .get("parsers", [])
    )

    parsers: List[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                parsers.append(item)
            elif isinstance(item, dict):
                name = item.get("name") or item.get("parser") or item.get("id")
                if isinstance(name, str):
                    parsers.append(name)

    return {
        "project": project,
        "verb": verb_name,
        "parsers": sorted(set(parsers)),
    }

# --------------------------
# Phrase outputs browsing (S3-aware)
# --------------------------

def _pphrase_outputs_root(project: str) -> Path:
    project_path = get_project_path(project)
    if not i_o.fs_exists(project_path):
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    base = resolve_path(project_path, "prepositional_phrase_output_dir")
    # Do NOT mkdir here; tool writes/creates.
    return base

def _safe_join(base: Path, rel: str) -> Path:
    """
    Join a user-supplied path under base and refuse escapes.
    We avoid Path.resolve() (not S3-friendly). Instead, reject
    absolute paths and any '..' segments.
    """
    rel_path = Path(rel)
    if rel_path.is_absolute() or any(part == ".." for part in rel_path.parts):
        raise HTTPException(status_code=400, detail="Invalid path segment")
    return base / rel_path

def _stat_dict(p: Path) -> dict:
    statf = getattr(i_o, "fs_stat", None)
    if not callable(statf):
        # Fallback for local-only; should not hit in S3 mode
        s = Path(p).stat()
        return {
            "size": s.st_size,
            "mtime": datetime.fromtimestamp(s.st_mtime).isoformat(timespec="seconds"),
        }
    s = statf(p)
    size = s.get("st_size") if isinstance(s, dict) else getattr(s, "st_size", None)
    # mtime is optional on S3; omit if not available
    out = {"size": int(size) if size is not None else 0}
    try:
        mtime = s.get("st_mtime") if isinstance(s, dict) else getattr(s, "st_mtime", None)
        if mtime:
            out["mtime"] = datetime.fromtimestamp(float(mtime)).isoformat(timespec="seconds")
    except Exception:
        pass
    return out

def _tree(dir_path: Path, base: Path, depth: int) -> dict:
    """
    Return a JSON-friendly tree:
      { name, path, type: 'dir'|'file', size?, mtime?, children?[] }
    S3-aware via i_o.fs_iterdir / fs_is_dir / fs_is_file.
    """
    node = {
        "name": dir_path.name if dir_path != base else dir_path.name,
        "path": str(Path("") if dir_path == base else dir_path.relative_to(base)),
        "type": "dir",
        "children": [],
    }
    if depth < 0:
        return node

    entries = []
    try:
        entries = list(i_o.fs_iterdir(dir_path))
    except Exception:
        return node

    dirs = sorted([e for e in entries if i_o.fs_is_dir(e)], key=lambda p: p.name.lower())
    files = sorted([e for e in entries if i_o.fs_is_file(e)], key=lambda p: p.name.lower())

    for d in dirs:
        node["children"].append(_tree(d, base, depth - 1))

    for f in files:
        child = {
            "name": f.name,
            "path": str(f.relative_to(base)),
            "type": "file",
            **_stat_dict(f),
        }
        node["children"].append(child)

    return node

@router.get("/pphrase_outputs/{project}/tree")
def list_pphrase_outputs_tree(
    project: str,
    subpath: str | None = FastAPIQuery(None, description="Optional folder inside the outputs root"),
    depth: int = FastAPIQuery(8, ge=0, le=32, description="Max recursion depth")
):
    """
    Return a nested tree of the 'prepositional phrase output dir' for the project.
    """
    base = _pphrase_outputs_root(project)
    root = base if not subpath else _safe_join(base, subpath)
    if not i_o.fs_exists(root) or not i_o.fs_is_dir(root):
        raise HTTPException(status_code=404, detail="Folder not found")

    payload = {
        "project": project,
        "root": str(base),
        "subpath": "" if not subpath else str(Path(subpath)),
        "tree": _tree(root, base, depth),
    }
    return JSONResponse(content=payload)

@router.get("/pphrase_outputs/{project}/download")
def download_pphrase_output(project: str, path: str = FastAPIQuery(..., description="Path relative to outputs root")):
    """
    Download a file from the 'prepositional phrase output dir' (S3-aware).
    """
    base = _pphrase_outputs_root(project)
    target = _safe_join(base, path)
    if not i_o.fs_exists(target) or not i_o.fs_is_file(target):
        raise HTTPException(status_code=404, detail="File not found")

    # Open file handle for streaming (S3-aware)
    fh = i_o.open_file(target, mode="rb")
    
    # Guess media type from filename
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if target.suffix.lower() == ".xlsx":
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif target.suffix.lower() == ".pdf":
        media_type = "application/pdf"
    elif target.suffix.lower() in [".txt", ".csv"]:
        media_type = "text/plain"

    return StreamingResponse(
        fh,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{target.name}"'}
    )
