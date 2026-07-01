# custom_template.py – one file usable as either a Custom Parser or a Prepositional Phrase
# Edit SECTION A and SECTION B only. SECTION Z is glue; do not edit.

from __future__ import annotations
import os, json, csv
from pathlib import Path
from typing import Dict, List, Any, Optional

# =============================================================================
# SECTION A – DEV CONFIG (EDIT THIS)
# =============================================================================

TOOL_NAME: str = "Potency_Parser"         # change me
TOOL_VERSION: str = "1.0.0"

# Choose mode: "parser" | "pphrase"
TOOL_KIND: str = "parser"

# RAW folder inputs (schema aliases). Backend ensures one file per folder and pre-digests if needed.
RAW_FOLDERS: List[str] = [
    # e.g., "HPLC Output", "PCR Output"
    "HPLC Output"
]

# Exact file inputs besides raw folders (schema names/aliases).
FILE_INPUTS: List[str] = [
    # e.g., "DataEntry.json", "adverbs.json"
    "DataEntry.json"
]

# Parser outputs: filenames that MUST match interpretation tabs per schema.
PARSER_OUTPUT_FILES: List[str] = [
    # e.g., "Weight.csv", "Summary.json"
    "Table.csv",
    "Weights.csv"
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


# =============================================================================
# SECTION B – DEV WORK (EDIT THIS): define how inputs -> outputs
# =============================================================================
# inputs: dict[str, list[str] | str]      (raw folders appear as list[str] of pre-digested files)
# outputs (parser): dict[str, str]        (each declared file -> absolute path to write)
# outputs (pphrase): {"OUTPUT_FOLDER": str} (directory to write any artifacts into)
# params: dict[str, Any]                  (runtime parameters passed by the orchestrator)

def parse_excel_csv(file_path: str, sheet_name: str = None) -> List[List[Any]]:
    """
    Parse Excel file that has been pre-converted to CSV by the backend.
    Since raw folders are pre-digested, Excel files will be CSV format.
    Returns a list of rows, where each row is a list of values.
    """
    rows = []
    with open(file_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)
    return rows

def work_parser(inputs: Dict[str, Any], outputs: Dict[str, str], params: Dict[str, Any]) -> None:
    """
    Implement your parsing/transformation here and write to outputs[...] paths.
    """
    # 1) Get HPLC Output files (should be pre-digested CSVs)
    hplc_files = inputs.get("HPLC Output", [])
    if not hplc_files:
        raise ValueError("No HPLC Output files found")
    
    # Handle single file or list
    if isinstance(hplc_files, str):
        hplc_files = [hplc_files]
    
    # Since the backend pre-digests Excel to CSV, we expect separate files for each sheet
    # Look for Labels and Data sheets
    labels_file = None
    data_file = None
    
    for file_path in hplc_files:
        if 'Labels' in file_path or 'labels' in file_path:
            labels_file = file_path
        elif 'Data' in file_path or 'data' in file_path:
            data_file = file_path
    
    if not labels_file or not data_file:
        # If not found by name, assume first two files are Labels and Data
        if len(hplc_files) >= 2:
            labels_file = hplc_files[0]
            data_file = hplc_files[1]
        else:
            raise ValueError("Could not find both Labels and Data sheets")
    
    # 2) Read both sheets
    labels_raw = parse_excel_csv(labels_file)
    data_raw = parse_excel_csv(data_file)
    
    # 3) Process Labels sheet
    # Original labels in E3:E (zero-based: row 2 down, col 4)
    labels_all = []
    codes_all = []
    
    for i in range(2, len(labels_raw)):
        if len(labels_raw[i]) > 4:
            labels_all.append(str(labels_raw[i][4]))
        else:
            labels_all.append("")
        
        if len(labels_raw[i]) > 2:
            codes_all.append(str(labels_raw[i][2]))
        else:
            codes_all.append("")
    
    # Filter out blank, anything containing "|RT", and the four meta-labels
    headers = []
    valid_indices = []
    
    for i, label in enumerate(labels_all):
        label = label.strip()
        if (label and 
            '|RT' not in label and 
            label not in ['Location', 'Inj', 'SampleType', 'Run']):
            # Remove the literal "|Amount" suffix
            header = label.replace('|Amount', '')
            headers.append(header)
            valid_indices.append(i)
    
    # 4) Process Data sheet
    # Data headers (codes) in row 1 of "Data"
    data_codes = []
    if len(data_raw) > 0:
        data_codes = [str(val) for val in data_raw[0]]
    
    # Data values from row 2 down, trimming any rows where column C is empty
    data_vals = []
    for row in data_raw[1:]:
        if len(row) > 2 and row[2] and str(row[2]).strip():
            data_vals.append(row)
    
    # 5) Build the table
    table = [headers]  # prepend headers as the first row
    
    for row_idx in range(len(data_vals)):
        row = []
        for header in headers:
            # MATCH(IF(header=="Sample",header,header+"|Amount"), Labels!E3:E)
            search_label = header if header == "Sample" else f"{header}|Amount"
            
            try:
                label_idx = labels_all.index(search_label)
            except ValueError:
                row.append("")
                continue
            
            label_code = codes_all[label_idx]
            
            # MATCH(label_code, Data!C1:1)
            try:
                data_col_idx = data_codes.index(label_code)
            except ValueError:
                row.append("")
                continue
            
            # Grab the cell at (row_idx, data_col_idx)
            if row_idx < len(data_vals) and data_col_idx < len(data_vals[row_idx]):
                value = data_vals[row_idx][data_col_idx]
            else:
                value = ""
            
            row.append(value)
        table.append(row)
    
    # 6) Write Table.csv
    table_path = outputs["Table.csv"]
    _write_csv(table_path, [dict(zip(headers, row)) for row in table[1:]])
    
    # 7) Load DataEntry.json
    data_entry_path = inputs.get("DataEntry.json")
    if not data_entry_path:
        raise ValueError("DataEntry.json not found")
    
    with open(data_entry_path, 'r') as f:
        data_entry = json.load(f)
    
    # 8) Build Sample ID → (sample_weight, dilution_weight) map
    sample_weights = {}
    duplicates = set()
    
    for entry in data_entry:
        sid = entry.get("Sample ID", "")
        if sid in sample_weights:
            duplicates.add(sid)
        
        try:
            sample_weight = float(entry.get("Sample Weight (g)", 0))
            dilution_weight = float(entry.get("Dilution Weight (g)", 0))
            if sample_weight == 0:
                sample_weight = None
            if dilution_weight == 0:
                dilution_weight = None
            sample_weights[sid] = (sample_weight, dilution_weight)
        except (ValueError, TypeError):
            sample_weights[sid] = (None, None)
    
    # 9) Build final rows for Weights.csv
    final_rows = []
    
    # Process each data row
    for row in table[1:]:  # Skip header row
        sample_id = str(row[0]).strip() if row else ""
        new_row = {}
        
        if sample_id in duplicates:
            # Entire row = #err if duplicated
            for i, h in enumerate(headers):
                if h == "Sample":
                    new_row[h] = sample_id
                else:
                    new_row[h] = "#err"
        elif sample_id not in sample_weights:
            # No weights → n/a for all analytes
            for i, h in enumerate(headers):
                if h == "Sample":
                    new_row[h] = sample_id
                else:
                    new_row[h] = "n/a"
        else:
            sample_weight, dilution_weight = sample_weights[sample_id]
            
            for i, h in enumerate(headers):
                if h == "Sample":
                    new_row[h] = sample_id
                else:
                    try:
                        instrument_conc = float(row[i])
                        
                        if sample_weight is None or dilution_weight is None:
                            result = "NaN"
                        elif sample_weight == 0 or dilution_weight == 0:
                            result = "n/a"
                        else:
                            # Calculate: (((instrument_conc / 1_000_000) * (dilution_weight / 0.786)) / sample_weight) * 100
                            result = round(
                                (((instrument_conc / 1_000_000) * (dilution_weight / 0.786)) / sample_weight) * 100,
                                3
                            )
                            result = str(result)
                    except (ValueError, TypeError, IndexError):
                        result = "NaN"
                    
                    new_row[h] = result
        
        final_rows.append(new_row)
    
    # 10) Write Weights.csv
    weights_path = outputs["Weights.csv"]
    _write_csv(weights_path, final_rows)


def work_pphrase(inputs: Dict[str, Any], outputs: Dict[str, Any], params: Dict[str, Any]) -> None:
    """
    Implement your document/package creation here and write into outputs["OUTPUT_FOLDER"].
    Replace the example with real logic (DOCX/PDF/ZIP/etc.).
    """
    # Not used for this parser
    pass


# =============================================================================
# SECTION Z – BACKEND GLUE (DO NOT EDIT BELOW)
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