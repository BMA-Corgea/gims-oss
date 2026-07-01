#!/usr/bin/env python3
"""
Debug tool to print the status breakdown of a specific run using core/status.py
Usage: python debug_status_breakdown.py <project> <verb_group> <run_id>
Example: python debug_status_breakdown.py LIMS-System Tests run002
"""

import sys
from pathlib import Path
from os import listdir

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.manifest.resolver import resolve_path
from api.i_o import load_data, load_schema, load_verb_group_log, list_verb_groups
from core.status import get_status_breakdown_core

def debug_status_breakdown(project: str, verb_group: str, run_id: str):
    print(f"\n🔍 DEBUG STATUS TOOL: Analyzing run '{run_id}' in {project}/{verb_group}")
    
    # Get project path
    project_root = resolve_path(Path(), "project_root")
    project_path = project_root / project
    print(f"📁 Project path: {project_path}")

    try:
        # Load verb group log and locate the run
        log_entries = load_verb_group_log(project_path, verb_group)
        print(f"📋 Found {len(log_entries)} entries in the log")
        all_run_ids = [entry.get("run_ID") for entry in log_entries if entry.get("run_ID")]
        print(f"📋 Available run IDs in {verb_group}:")
        for i, available_id in enumerate(all_run_ids):
            print(f"  {i+1}. '{available_id}'")
        
        run_entry = next((e for e in log_entries if e.get("run_ID") == run_id), None)

        if not run_entry:
            print(f"⚠️ Run ID not found. Searching other verb groups...")
            for other_group in list_verb_groups(project_path):
                if other_group == verb_group:
                    continue
                try:
                    other_entries = load_verb_group_log(project_path, other_group)
                    run_entry = next((e for e in other_entries if e.get("run_ID") == run_id), None)
                    if run_entry:
                        print(f"✅ Found run '{run_id}' in group '{other_group}'. Please rerun with that group.")
                        return
                except Exception:
                    continue
            print(f"❌ Run ID '{run_id}' not found in any group.")
            return

        print(f"✅ Found run entry: {run_entry}")
        verb_key = run_entry.get("test_type") or run_entry.get("verb", "")
        print(f"🔑 Verb key: {verb_key}")

        # Load schemas
        print(f"📚 Loading schemas...")
        verb_schemas = load_schema(project_path, "verb")
        noun_schemas = load_schema(project_path, "noun")
        adverb_schemas = load_schema(project_path, "adverb")

        # Resolve paths
        data_entry_path = resolve_path(project_path, "data_entry", verb_group=verb_group, run_id=run_id)
        status_path = resolve_path(project_path, "status_file", verb_group=verb_group, run_id=run_id)
        adverb_path = resolve_path(project_path, "adverb_file", verb_group=verb_group, run_id=run_id)
        data_dump_dir = resolve_path(project_path, "data_dump_dir", verb_group=verb_group, run_id=run_id)

        print(f"📄 Data entry path: {data_entry_path} (exists: {data_entry_path.exists()})")
        print(f"📄 Status path: {status_path} (exists: {status_path.exists()})")
        print(f"📄 Adverb path: {adverb_path} (exists: {adverb_path.exists()})")
        print(f"📂 Data dump dir: {data_dump_dir} (exists: {data_dump_dir.exists()})")

        # Load data files
        data_entry_data = load_data(data_entry_path) or []
        status_data = load_data(status_path) or {}
        adverb_data = load_data(adverb_path) or {}
        present_files = listdir(data_dump_dir) if data_dump_dir.exists() else []

        print(f"📊 Data summary:")
        print(f"  - Data entry: {type(data_entry_data)} with {len(data_entry_data)} rows")
        print(f"  - Status data: {type(status_data)} with {len(status_data)} keys")
        print(f"  - Adverb data: {type(adverb_data)} with {len(adverb_data)} keys")
        print(f"  - Files in data dump dir: {present_files}")

        # Get noun schema and raw inputs
        verb_def = verb_schemas.get(verb_key, {})
        noun_type = verb_def.get("data_entry_schema", {}).get("set_up_inputs", {}).get("noun_type_ref")
        noun_schema = noun_schemas.get(noun_type, {})
        raw_inputs = verb_def.get("data_entry_schema", {}).get("raw_data_inputs", [])

        # Fix uploaded_tabs if missing but present in file system
        if 'interpretation' in status_data and 'uploaded_tabs' not in status_data['interpretation']:
            tabs = verb_def.get("data_entry_schema", {}).get("interpretation", {}).get("tabs", [])
            status_data['interpretation']['uploaded_tabs'] = tabs

        # Correct: Only count pocket if its folder exists AND is non-empty
        raw_uploaded = []
        for pocket in raw_inputs:
            pocket_path = data_dump_dir / pocket
            if pocket_path.is_dir() and any(pocket_path.iterdir()):
                raw_uploaded.append(pocket)
                print(f"✅ RAW POCKET PRESENT AND NON-EMPTY: {pocket}")
            else:
                print(f"❌ RAW POCKET MISSING OR EMPTY: {pocket}")

        status_data["raw_uploaded"] = raw_uploaded

        print(f"\n🧪 Calling get_status_breakdown_core...")

        breakdown = get_status_breakdown_core(
            noun_schema=noun_schema,
            raw_inputs=raw_inputs,
            data_entry_data=data_entry_data,
            adverb_schema=adverb_schemas,
            adverb_data=adverb_data,
            verb_key=verb_key,
            verb_types=verb_schemas,
            status_data=status_data,
            present_files=present_files,
        )

        print("\n📊 STATUS BREAKDOWN")
        print("=" * 60)
        for k, v in breakdown.items():
            print(f"{k.ljust(20)}: {v}")
        print("=" * 60)

    except Exception as e:
        import traceback
        print(f"❌ ERROR: {e}")
        print(traceback.format_exc())

if __name__ == "__main__":
    default_project = "LIMS-System"
    default_verb_group = "Tests"
    default_run_id = "supa hottttt001"

    project = sys.argv[1] if len(sys.argv) > 1 else default_project
    verb_group = sys.argv[2] if len(sys.argv) > 2 else default_verb_group
    run_id = sys.argv[3] if len(sys.argv) > 3 else default_run_id

    debug_status_breakdown(project, verb_group, run_id)
