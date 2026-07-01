# core/run_custom/predigest.py
from __future__ import annotations
from typing import Dict, List, Any, Optional, Callable
from pathlib import Path
from .schema import IoSpec
from ._common import fs_read_bytes, fs_open_readbin, log


# ============================================================
# SECTION 2 — PRE-DIGESTION ADAPTERS (OPTIONAL HEAVY DEPS HERE)
# ============================================================

class PreDigestRegistry:
    """Registry of handlers mapping file extensions to pre-digestion callables."""
    def __init__(self) -> None:
        self._reg: Dict[str, Callable[[Path, Path], List[Path]]] = {}
        log.debug("[predigest.registry] init")

    def register(self, ext: str, fn: Callable[[Path, Path], List[Path]]) -> None:
        self._reg[ext.lower()] = fn
        log.debug("[predigest.registry] register", ext, "->", getattr(fn, "__name__", str(fn)))

    def get(self, ext: str) -> Optional[Callable[[Path, Path], List[Path]]]:
        fn = self._reg.get(ext.lower())
        log.debug("[predigest.registry] get", ext, "->", getattr(fn, "__name__", None))
        return fn

# ---- Example handlers ----
def predigest_passthrough(input_file: Path, out_dir: Path) -> List[Path]:
    """
    For already-friendly formats (e.g., .csv, .json): copy to local out_dir.
    S3-aware read (fs_read_bytes) + local write.
    """
    log.debug("[predigest.passthrough] src=", str(input_file), "out_dir=", str(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / Path(input_file).name
    try:
        data = fs_read_bytes(input_file)
        target.write_bytes(data)  # local/write is ephemeral by design
        log.debug("[predigest.passthrough] wrote ->", str(target))
    except Exception as e:
        # fallback if fs_read_bytes fails on local
        log.debug("[predigest.passthrough][warn] fs_read_bytes failed; fallback .read_bytes()", repr(e))
        if str(Path(input_file).resolve()) != str(target.resolve()):
            target.write_bytes(Path(input_file).read_bytes())
            log.debug("[predigest.passthrough] copied via local ->", str(target))
    return [target]

def predigest_xlsx_to_csvs(input_file: Path, out_dir: Path) -> List[Path]:
    """
    Convert .xlsx workbook into one CSV per sheet using pandas/openpyxl.
    S3-aware: use fs_open_readbin to stream the workbook; write CSVs locally.
    """
    log.debug("[predigest.xlsx] src=", str(input_file), "out_dir=", str(out_dir))
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
                log.debug("[predigest.xlsx] sheet ->", sheet, "file=", str(out_file))
        return produced
    except ModuleNotFoundError as e:
        log.debug("[predigest.xlsx][error] pandas/openpyxl missing")
        raise RuntimeError("Pre-digestion for .xlsx requires pandas and openpyxl") from e

def default_predigest_registry() -> PreDigestRegistry:
    reg = PreDigestRegistry()
    # friendly formats
    for ext in (".csv", ".json", ".txt"):
        reg.register(ext, predigest_passthrough)
    # excel
    reg.register(".xlsx", predigest_xlsx_to_csvs)
    log.debug("[predigest.default] handlers=", list(reg._reg.keys()))
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
            log.debug(
                "[runner][inject] data_dump_dir params -> verb_group=",
                params.get("verb_group"),
                "run_id=",
                params["run_id"],
            )
        else:
            log.debug(
                "[runner][inject] no run_ids available; leaving data_dump_dir params unchanged"
            )

    return injected
