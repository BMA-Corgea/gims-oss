# custom_template.py — one file usable as either a Custom Parser or a Prepositional Phrase
# Edit SECTION A and SECTION B only. SECTION Z is glue; do not edit.

from __future__ import annotations
import os, json, csv
from pathlib import Path
from typing import Dict, List, Any, Optional

# =============================================================================
# SECTION A — DEV CONFIG (EDIT THIS)
# =============================================================================

TOOL_NAME: str = "Example_Parser"         # change me
TOOL_VERSION: str = "0.3.0"

# Choose mode: "parser" | "pphrase"
TOOL_KIND: str = "parser"                  # <-- set to "pphrase" to use PREPHRASE_SETTINGS

# RAW folder inputs (schema aliases). Backend ensures one file per folder and pre-digests if needed.
RAW_FOLDERS: List[str] = [
    # e.g., "HPLC Output", "PCR Output"
    "HPLC Output"
]

# Exact file inputs besides raw folders (schema names/aliases).
FILE_INPUTS: List[str] = [
    # e.g., "DataEntry.json", "adverbs.json"
]

# Parser outputs: filenames that MUST match interpretation tabs per schema.
PARSER_OUTPUT_FILES: List[str] = [
    # e.g., "Weight.csv", "Summary.json"
    "Weight.csv"
]

# Pphrase output: single folder name to write into (single segment, no slashes).
PPHRASE_OUTPUT_FOLDER: Optional[str] = None  # e.g., "COA"

# Optional DB inputs (pphrase). Example:
# DB_INPUTS = [{"endpoint": "noun_items", "params": {"noun_type": "Sample"}, "alias": "sample_items"}]
DB_INPUTS: List[Dict[str, Any]] = []

# Optional host-side post-doc step (pphrase). Example:
# POST_DOC = {
#   "entry": "my_reports.pphrase_reports:make_document",
#   "args": {"template": "/path/to/coa_template.docx", "title": "COA"},
#   "safety": "light",   # "light" (default) | "off"
#   "timeout_s": 30
# }
POST_DOC: Optional[Dict[str, Any]] = None

# ------- NEW: Pre-phrase UI schema (used when TOOL_KIND == "pphrase") -------
# The backend/GUI read this to render a settings UI and to server-side expand dynamic options.
# Supported kinds in the current core: "bool" | "single" | "multi" | "text" | "number"
# Options may be:
#   - explicit list: [{"label": str, "value": Any}, ...]
#   - dynamic: {"source": "<provider.name>", ...extra hints...}
#
# Dynamic options extra keys (interpreted server-side by the expander):
#   - complete: <bool>                         # NEW — when using a noun source ("noun: <Type>"), only include instances
#                                              #       that satisfy the noun schema's required fields (non-empty / non-null).
#   - filters:  [                              # fielded/pair predicates
#       {"field": "...", "op": "in",       "value": X} |
#       {"field": "...", "op": "=",        "value": X} |
#       {"field": "...", "op": "!=",       "value": X} |
#       {"field": "...", "op": "contains", "value": <str>} |
#       {"field": "...", "op": "between",  "value": [start, end]} |
#       {"field": "...", "op": "between",  "ref":   ["start_id","end_id"]} |
#       {"op": "has_pair", "value": ["A","B", ...]} |              # pairwise presence (row-group semantics; expander-specific)
#       {"field": "...", "op": "exists"} |
#       {"field": "...", "op": "missing"}
#     ]
#   - unique_by: ["fieldA", "fieldB"]         # composite de-dupe
#   - map:       {"label": "...", "value": "..."}  # format strings against each row
#   - sort:      [{"field":"...", "dir":"asc|desc"}, ...]
#   - limit:     int
#   - ref:       <str>|[<str>, ...]           # read user-selected values from other fields by id
#   - allow_duplicates: <bool>                # default False
#
# NOTE: If you need a date range, keep using two "text" fields or a single field with a "between" + "ref" pair.
PREPHRASE_SETTINGS: List[Dict[str, Any]] = [
    {
        "id": "include_watermark",
        "label": "Include watermark",
        "kind": "bool",
        "default": True
    },
    {
        "id": "output_format",
        "label": "Output format",
        "kind": "single",
        "options": [
            {"label": "PDF",  "value": "pdf"},
            {"label": "DOCX", "value": "docx"}
        ],
        "default": "docx"
    },

    # Optional pre-filters that influence the dynamic noun list
    {
        "id": "clients",
        "label": "Client filter",
        "kind": "multi",
        "options": {
            # Pull unique client names from complete Sample nouns
            "source": "noun: Sample",
            "complete": True,                         # NEW — only 100% complete Sample nouns
            "filters": [
                {"field": "Client", "op": "exists"}   # only rows that actually have a client field present
            ],
            "unique_by": ["Client"],
            "map": { "label": "{Client}", "value": "{Client}" },
            "sort": [ { "field": "label", "dir": "asc" } ]
        },
        "default": []
    },
    {
        "id": "date_start",
        "label": "Received date (start, YYYY-MM-DD)",
        "kind": "text",
        "default": ""
    },
    {
        "id": "date_end",
        "label": "Received date (end, YYYY-MM-DD)",
        "kind": "text",
        "default": ""
    },

    # The main selection list — built from noun instances with "complete" gating + user filters.
    {
        "id": "samples",
        "label": "Samples (complete noun records)",
        "kind": "multi",
        "options": {
            "source": "noun: Sample",
            "complete": True,   # NEW — require every noun instance to satisfy required fields
            "filters": [
                # If user picked clients, filter by them
                {"field": "Client", "op": "in", "ref": "clients"},
                # Received date window when provided (inclusive)
                {"field": "received_date", "op": "between", "ref": ["date_start", "date_end"]},
                # Ensure core identifiers exist
                {"field": "Sample ID", "op": "exists"},
                {"field": "Client",    "op": "exists"}
            ],
            # De-dupe by Sample ID (adjust if you want a different uniqueness policy)
            "unique_by": ["Sample ID"],
            # Pretty label + stable machine value
            "map": {
                "label": "{Sample ID} — {Client} — {Sample Name}",
                "value": "{Sample ID}"
            },
            "sort": [
                {"field": "Client",    "dir": "asc"},
                {"field": "Sample ID", "dir": "asc"}
            ],
            "limit": 1000
        },
        "default": []
    }
]


# =============================================================================
# SECTION B — DEV WORK (EDIT THIS): define how inputs -> outputs
# =============================================================================
# inputs: dict[str, list[str] | str]      (raw folders appear as list[str] of pre-digested files)
# outputs (parser): dict[str, str]        (each declared file -> absolute path to write)
# outputs (pphrase): {"OUTPUT_FOLDER": str} (directory to write any artifacts into)
# params: dict[str, Any]                  (runtime parameters passed by the orchestrator)

def work_parser(inputs: Dict[str, Any], outputs: Dict[str, str], params: Dict[str, Any]) -> None:
    """
    Implement your parsing/transformation here and write to outputs[...] paths.
    Replace the example with real logic.
    """
    # ---- Example (safe to delete) -----------------------------------
    rows: List[Dict[str, Any]] = []
    if "HPLC Output" in inputs:
        srcs = inputs["HPLC Output"]
        files = srcs if isinstance(srcs, list) else [srcs]
        for p in files:
            rows.extend(_read_csv(p))
    result = [{"metric": "rows_seen", "value": len(rows)}]

    # Write the primary interpretation file(s)
    for fname in PARSER_OUTPUT_FILES:
        out_path = outputs[fname]
        # Demo: only "Weight.csv" gets the metric, others get a placeholder
        if Path(fname).name.lower() == "weight.csv":
            _write_csv(out_path, result)
        else:
            _write_csv(out_path, [{"metric": "not_implemented", "value": 0}])


def work_pphrase(inputs: Dict[str, Any], outputs: Dict[str, Any], params: Dict[str, Any]) -> None:
    """
    Implement your document/package creation here and write into outputs["OUTPUT_FOLDER"].
    Replace the example with real logic (DOCX/PDF/ZIP/etc.).
    """
    out_dir = Path(outputs["OUTPUT_FOLDER"])
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- Example (safe to delete) -----------------------------------
    rows_seen = 0
    if "HPLC Output" in inputs:
        srcs = inputs["HPLC Output"]
        files = srcs if isinstance(srcs, list) else [srcs]
        for p in files:
            rows_seen += len(_read_csv(p))

    _write_csv(out_dir / "Weight.csv", [{"metric": "rows_seen", "value": rows_seen}])


# =============================================================================
# SECTION Z — BACKEND GLUE (DO NOT EDIT BELOW)
# =============================================================================

TOOL = {
    "name": TOOL_NAME,
    "version": TOOL_VERSION,
    "kind": TOOL_KIND,
    "about": (
        "Parses raw outputs to produce interpretation files."
        if TOOL_KIND == "parser"
        else "Generates a document/package from run artifacts."
    ),
}

def get_metadata() -> dict:
    return dict(TOOL)

def get_PREPHRASE_SETTINGS() -> List[Dict[str, Any]]:
    return list(PREPHRASE_SETTINGS)

def get_io_spec() -> dict:
    if TOOL["kind"] not in ("parser", "pphrase"):
        raise ValueError("TOOL.kind must be 'parser' or 'pphrase'")

    spec: Dict[str, Any] = {
        "kind": TOOL["kind"],
        "raw_folders": list(RAW_FOLDERS),
        "file_inputs": list(FILE_INPUTS),
    }
    if TOOL["kind"] == "parser":
        spec["outputs"] = {"files": list(PARSER_OUTPUT_FILES)}
    else:
        spec["outputs"] = {"folder": PPHRASE_OUTPUT_FOLDER or ""}
        extra: Dict[str, Any] = {}
        if DB_INPUTS:
            extra["db_inputs"] = list(DB_INPUTS)
        if POST_DOC:
            extra["post_doc"] = dict(POST_DOC)
        if extra:
            spec["extra"] = extra
    return spec

# ---- tiny IO helpers for dev section ----
def _read_csv(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", newline="") as f:
        return [row for row in csv.DictReader(f)]

def _write_csv(path: str | Path, rows: List[Dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

# ---- runtime entrypoint (sandbox calls this) ----
def run() -> None:
    mapping = os.environ.get("GIMS_IO_JSON")
    if not mapping:
        raise RuntimeError("GIMS_IO_JSON not provided")
    io_map = json.loads(mapping)

    kind = io_map.get("kind")
    inputs = io_map.get("inputs", {})
    outputs = io_map.get("outputs", {})
    params = io_map.get("params", {}) or {}

    # Normalize: ensure raw folder values are lists for dev ergonomics
    norm_inputs: Dict[str, Any] = {}
    for k, v in inputs.items():
        if isinstance(v, list):
            norm_inputs[k] = v
        elif isinstance(v, str):
            norm_inputs[k] = v  # keep strings for exact files; dev can cast to list if helpful
        else:
            norm_inputs[k] = v

    if kind == "parser":
        work_parser(norm_inputs, outputs, params)
    elif kind == "pphrase":
        work_pphrase(norm_inputs, outputs, params)
    else:
        raise RuntimeError(f"Unknown tool kind: {kind}")

if __name__ == "__main__":
    run()
