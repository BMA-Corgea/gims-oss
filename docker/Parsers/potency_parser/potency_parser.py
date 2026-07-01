# docker/potency_parser/potency_parser.py

from pathlib import Path
import pandas as pd
import json
import csv
import numpy as np

def run_parser(inputs: dict[str, Path], output_dir: Path | None = None):
    print("👋 Hello from inside the Docker jail!")

    import os

    cwd = Path.cwd()
    print(f"🔍 Current working directory: {cwd}")

    # 🔧 Flip this to enable/disable directory debug mode
    directory_mode = False

    if directory_mode:
        print("📂 Full directory tree:")
        for root, dirs, files in os.walk(".", topdown=True):
            level = root.replace(os.getcwd(), '').count(os.sep)
            indent = ' ' * 4 * level
            print(f"{indent}{root}/")
            subindent = ' ' * 4 * (level + 1)
            for f in files:
                print(f"{subindent}{f}")

        # ❌ TEMPORARY EXIT to inspect mount correctness
        raise RuntimeError("💥 DEBUG STOP – inspect directory tree above before proceeding.")

    if not output_dir or not output_dir.exists():
        print("❌ No output_dir provided or it doesn't exist.")
        return

    # 1) Locate the HPLC Output workbook
    hplc_file = inputs.get("HPLC Output")
    if not hplc_file or not hplc_file.exists():
        print("❌ HPLC Output file not found.")
        return

    # 2) Read both sheets without assuming a header row
    if hplc_file.suffix in [".xls"]:
        xls = pd.ExcelFile(hplc_file, engine="xlrd")
    else:
        xls = pd.ExcelFile(hplc_file, engine="openpyxl")
    labels_raw = pd.read_excel(xls, sheet_name="Labels", header=None)
    data_raw   = pd.read_excel(xls, sheet_name="Data",   header=None)

    # --- A1 Formula in Python ---
    # Original labels in E3:E (zero-based: row 2 down, col 4)
    labels_all = labels_raw.iloc[2:, 4].astype(str).tolist()
    # Corresponding codes in C3:C (col 2)
    codes_all  = labels_raw.iloc[2:, 2].astype(str).tolist()

    # Filter out blank, anything containing "|RT", and the four meta-labels
    labels_series = pd.Series(labels_all)
    mask = (
        labels_series.str.strip().astype(bool)
        & ~labels_series.str.contains(r"\|RT", na=False)
        & ~labels_series.str.match(r"^(Location|Inj|SampleType|Run)$", na=False)
    )
    # Remove the literal "|Amount" suffix and use these as our headers
    headers = labels_series[mask].str.replace(r"\|Amount", "", regex=True).tolist()

    # --- A2 Formula in Python ---
    # Data headers (codes) in row 1 of "Data"
    data_codes = data_raw.iloc[0, :].astype(str).tolist()
    # Data values from row 2 down, trimming any rows where column C is empty
    data_vals = data_raw.iloc[1:, :].copy()
    data_vals = data_vals[data_vals.iloc[:, 2].notna()].reset_index(drop=True)

    # Build the table: rows × headers (with headers as first row)
    table = [headers]  # prepend headers as the first row

    for row_idx in range(len(data_vals)):
        row = []
        for header in headers:
            # MATCH(IF(header=="Sample",header,header+"|Amount"), Labels!E3:E)
            search_label = header if header == "Sample" else f"{header}|Amount"
            try:
                label_idx = labels_all.index(search_label)
            except ValueError:
                raise ValueError(f"Label '{search_label}' not found in Labels sheet")
            label_code = codes_all[label_idx]

            # MATCH(label_code, Data!C1:1)
            try:
                data_col_idx = data_codes.index(label_code)
            except ValueError:
                raise ValueError(f"Code '{label_code}' not in Data header row")

            # Grab the cell at (row_idx, data_col_idx)
            value = data_vals.iat[row_idx, data_col_idx]
            row.append(value)
        table.append(row)

    # Write out Table.csv with no DataFrame header (our first row is headers)
    out_path = output_dir / "Table.csv"
    pd.DataFrame(table).to_csv(out_path, index=False, header=False)
    print(f"✅ Table.csv generated at: {out_path}")

    # --- Load Table.csv into DataFrame ---
    table_path = output_dir / "Table.csv"
    table_df = pd.read_csv(table_path, header=None)
    headers = table_df.iloc[0].tolist()
    table_df = table_df[1:]  # drop header row
    table_df.columns = headers

    # --- Load DataEntry.json ---
    data_entry_path = inputs.get("data_entry")
    if not data_entry_path or not data_entry_path.exists():
        print("❌ DataEntry.json not found.")
        return

    data_entry = json.loads(data_entry_path.read_text())

    # --- Build Sample ID → (sample_weight, dilution_weight) map ---
    sample_weights = {}
    duplicates = set()
    for entry in data_entry:
        sid = entry.get("Sample ID")
        if sid in sample_weights:
            duplicates.add(sid)
        try:
            sample_weight = float(entry.get("Sample Weight (g)", np.nan))
            dilution_weight = float(entry.get("Dilution Weight (g)", np.nan))
            sample_weights[sid] = (sample_weight, dilution_weight)
        except Exception:
            sample_weights[sid] = (np.nan, np.nan)

    # --- Build final rows ---
    final_rows = [headers]
    for idx, row in table_df.iterrows():
        sample_id = str(row.get("Sample", "")).strip()
        new_row = []

        if sample_id in duplicates:
            # Entire row = #err if duplicated
            new_row = ["#err"] * len(headers)
            new_row[0] = sample_id  # keep sample_id in first column
        elif sample_id not in sample_weights:
            # No weights → n/a for all analytes
            new_row = [sample_id] + ["n/a"] * (len(headers) - 1)
        else:
            sample_weight, dilution_weight = sample_weights[sample_id]

            for h in headers:
                if h == "Sample":
                    new_row.append(sample_id)
                else:
                    try:
                        # Pull the cell; if pandas gives back a Series (duplicate headers), take its first element
                        val = row[h]
                        if isinstance(val, pd.Series):
                            val = val.iloc[0]

                        instrument_conc = float(val)

                        if np.isnan(sample_weight) or np.isnan(dilution_weight) or np.isnan(instrument_conc):
                            result = "NaN"
                        elif sample_weight == 0 or dilution_weight == 0:
                            result = "n/a"
                        else:
                            result = round(
                                (((instrument_conc / 1_000_000) * (dilution_weight / 0.786)) / sample_weight) * 100,
                                3
                            )
                    except Exception:
                        result = "NaN"
                    new_row.append(result)

        final_rows.append(new_row)

    # --- Write final Weights.csv ---
    weights_path = output_dir / "Weights.csv"
    with open(weights_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(final_rows)

    print(f"✅ Weights.csv generated at: {weights_path}")

def get_metadata():
    return {
        "name": "potency_parser",
        "entrypoint": "potency_parser.py",
        "verb": "Potency_Test",
        "dependencies": [
            "pandas",
            "numpy",
            "openpyxl"
        ]
    }

def get_io_manifest():
    return {
        "HPLC Output": {"mode": "read"},
        "data_entry": {"mode": "read"},
        "Table.csv": {"mode": "readwrite", "type": "csv"},
        "Weights.csv": {"mode": "write",     "type": "csv"},
    }

if __name__ == "__main__":
    manifest = get_io_manifest()

    # Only collect declared read-mode inputs that specify a 'path'
    inputs = {}
    for alias, spec in manifest.items():
        if spec.get("mode") == "read":
            path_str = spec.get("path")
            if path_str:
                inputs[alias] = Path(path_str)

    # Determine if we need an output folder
    output_dir = Path("/app/output") if any(
        spec.get("mode") in ("write", "readwrite") for spec in manifest.values()
    ) else None

    # Create output dir if needed
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    run_parser(inputs, output_dir)