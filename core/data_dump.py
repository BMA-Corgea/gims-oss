# core/core_data_dump.py

from typing import List, Dict, Any
from pathlib import Path
from core.status import get_status_breakdown_core

def prepare_data_dump(
    project_path: Path,
    run_id: str,
    run_entry: Dict[str, Any],
    verb_def: Dict[str, Any],
    noun_schema: Dict[str, Any],
    data_entry_data: List[Dict[str, Any]],
    adverb_data: Dict[str, Any],
    interpretation_data: Dict[str, Any],
    overrides: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Pure core function: given all inputs for a specific run,
    return a structured summary for downstream rendering/UI.
    """

    # 1. Calculate status breakdown
    if "breakdown" in run_entry:
        status_breakdown = run_entry["breakdown"]
    else:
        status_breakdown = get_status_breakdown_core(project_path, run_id)

    # 2. Gather simplified setup instructions
    instructions = []
    setup = verb_def.get("data_entry_schema", {}).get("set_up_inputs", {})
    if setup and "instructions" in setup:
        instructions = setup["instructions"]

    # 3. Organize data entry fields by category
    data_entry_categories = {}
    data_entry_schema = verb_def.get("data_entry_schema", {})
    if data_entry_schema:
        categories = data_entry_schema.get("categories", [])
        if categories:
            for cat in categories:
                name = cat.get("category", "General")
                fields = cat.get("fields", [])
                data_entry_categories[name] = fields
        else:
            data_entry_categories["General"] = []

    # 4. Return structured result
    return {
        "run_entry": run_entry,
        "status_breakdown": status_breakdown,
        "instructions": instructions,
        "data_entry": {
            "schema": noun_schema,
            "rows": data_entry_data,
            "categories": data_entry_categories,
        },
        "interpretation": {
            "schema": data_entry_schema.get("interpretation", {}),
            "data": interpretation_data,
        },
        "adverbs": adverb_data,
        "overrides": overrides,
    }
