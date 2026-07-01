from pathlib import Path
import json
from typing import Optional

def get_status_breakdown(
    run_path: Path,
    noun_schema: dict,
    raw_inputs: list[str],
    adverb_schema: dict = None,
    verb_key: Optional[str] = None,
    project_path: Optional[Path] = None
):
    """
    Check and persist the status of each section of a data dump.
    Saves to Status.json as: { "breakdown": {...} }
    """
    breakdown = {}
    status_path = run_path / "Status.json"

    # --- 1. Raw Data ---
    raw_list = raw_inputs or []
    if not raw_list:
        breakdown["raw_data"] = "Not Uploaded"
    else:
        missing = []
        for pocket in raw_list:
            pocket_path = run_path / pocket
            all_files = list(pocket_path.iterdir()) if pocket_path.exists() else []
            if not any(f.suffix.lower() in [".csv", ".xlsx"] for f in all_files):
                missing.append(pocket)
        breakdown["raw_data"] = "Uploaded" if not missing else "Missing → " + ", ".join(missing)

    # --- 2. Data Entry ---
    data_entry_path = run_path / "DataEntry.json"
    if not data_entry_path.exists():
        breakdown["data_entry"] = "Pending"
    else:
        try:
            with open(data_entry_path) as f:
                data = json.load(f)
            if not isinstance(data, list) or len(data) == 0:
                breakdown["data_entry"] = "Missing Required Fields"
            else:
                primary_ids = set()
                missing_fields = False
                for row in data:
                    if not isinstance(row, dict) or not any((v.strip() if isinstance(v, str) else bool(v)) for v in row.values()):
                        continue
                    if noun_schema:
                        for field, props in noun_schema.get("fields", {}).items():
                            if props.get("required") and (row.get(field, "").strip() == ""):
                                missing_fields = True
                    primary_key = noun_schema.get("primary_id_field") if noun_schema else None
                    pid = row.get(primary_key, "").strip() if primary_key else None
                    if pid:
                        if pid in primary_ids:
                            missing_fields = True
                        primary_ids.add(pid)
                breakdown["data_entry"] = "Missing Required Fields" if missing_fields else "Complete"
        except Exception:
            breakdown["data_entry"] = "Missing Required Fields"

    # --- 3. Interpretation ---
    manual_approved = False
    status_data = {}
    if status_path.exists():
        try:
            status_data = json.loads(status_path.read_text())
            manual_approved = status_data.get("interpretation", {}).get("manual_approval", False)
        except json.JSONDecodeError:
            pass

    # Determine expected interpretation tabs
    verb_config = json.loads((project_path / "verb_types.json").read_text())
    interp_tabs = verb_config.get(verb_key, {}).get("data_entry_schema", {}) \
                            .get("interpretation", {}).get("tabs", [])

    all_present = True
    for tab in interp_tabs:
        path = run_path / f"{tab}.csv"
        if not path.exists() or path.read_text().strip() == "":
            all_present = False
            break

    interp_method = verb_config.get(verb_key, {}) \
        .get("data_entry_schema", {}) \
        .get("interpretation", {}) \
        .get("method", "parsed")  # Default to "parsed" if missing

    if all_present and interp_tabs:
        breakdown["interpretation"] = "Uploaded" if interp_method == "uploaded" else "Parsed"
    elif manual_approved:
        breakdown["interpretation"] = "Manually Completed"
    else:
        breakdown["interpretation"] = "Pending"

    # --- 4. Adverb Info ---
    adv_path = run_path / "adverbs.json"

    if not adverb_schema:
        breakdown["adverb_info"] = "Complete"
    else:
        # Extract required adverbs
        if isinstance(adverb_schema, dict):
            required = [k for k, v in adverb_schema.items() if v.get("required")]
        elif isinstance(adverb_schema, list):
            required = [entry["adverb"] for entry in adverb_schema if entry.get("required")]
        else:
            required = []

        if not adv_path.exists():
            breakdown["adverb_info"] = "Pending"
        else:
            try:
                with open(adv_path) as f:
                    data = json.load(f)
                # Check for missing required fields
                missing = [adverb for adverb in required if not data.get(adverb)]
                breakdown["adverb_info"] = "Complete" if not missing else "Pending"
            except Exception:
                breakdown["adverb_info"] = "Pending"

    # --- 5. Conjunction (Override) Status ---
    override_statuses = []
    overrides = status_data.get("conjunctions", [])

    if overrides:
        # Load verb config
        verb_config_path = run_path.parents[2] / "verb_types.json"
        try:
            with open(verb_config_path) as f:
                verb_defs = json.load(f)
            this_cfg = verb_defs.get(verb_key, {})
            override_defs = this_cfg.get("status_overrides", [])
        except Exception:
            override_defs = []

        # Build mapping: name → status
        status_map = {
            o["name"]: o["status"].upper()
            for o in override_defs
            if "name" in o and "status" in o
        }

        # For each applied override, mark resolved or show status
        for ovr in overrides:
            otype = ovr.get("type", "Unknown")
            if "resolution" in ovr:
                override_statuses.append(f"RESOLVED: {otype}")
            else:
                # Use status from the actual override if available
                st = ovr.get("status", status_map.get(otype, "EXCEPTION"))
                override_statuses.append(f"{st.upper()}: {otype}")

    if override_statuses:
        breakdown["override_status"] = "\n".join(override_statuses)

    # --- Write back to Status.json ---
    try:
        status_data["breakdown"] = breakdown
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(json.dumps(status_data, indent=2))
    except Exception as e:
        print(f"[red]⚠️ Failed to write status breakdown: {e}[/red]")

    return breakdown

def render_status_bar(breakdown: dict, blocks_per_zone: int = 3) -> str:
    """
    Render a visual status bar from a status breakdown.
    Completed states are: Uploaded, Complete, Parsed, Manually Completed.
    """
    complete_states = {"Uploaded", "Complete", "Parsed", "Manually Completed"}
    completed_count = sum(1 for s in breakdown.values() if s in complete_states)
    total = len(breakdown)
    max_blocks = total * blocks_per_zone
    filled = completed_count * blocks_per_zone
    bar = "█" * filled + "░" * (max_blocks - filled)
    percent = int((completed_count / total) * 100)
    return f"Progress: [{bar}] {percent}%"