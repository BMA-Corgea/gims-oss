from fastapi import APIRouter, HTTPException, Query, Body
from pathlib import Path
import json
import os
from typing import Optional

router = APIRouter()
BASE_PATH = Path("projects")


def load_json_file(path: Path):
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{path.name} not found")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl_file(path: Path):
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{path.name} not found")
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f]

def write_json_file(path: Path, data: dict):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return {"status": "saved", "path": str(path)}

def append_jsonl(path: Path, entry: dict):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return {"status": "appended", "path": str(path)}

def replace_jsonl_entry(path: Path, match: callable, new_entry: dict):
    lines = load_jsonl_file(path)
    updated = False
    for i, line in enumerate(lines):
        if match(line):
            lines[i] = new_entry
            updated = True
            break
    if not updated:
        raise HTTPException(status_code=404, detail="Entry to update not found")
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return {"status": "updated", "path": str(path)}

def delete_jsonl_entry(path: Path, match: callable):
    lines = load_jsonl_file(path)
    new_lines = [line for line in lines if not match(line)]
    if len(new_lines) == len(lines):
        raise HTTPException(status_code=404, detail="Entry to delete not found")
    with path.open("w", encoding="utf-8") as f:
        for line in new_lines:
            f.write(json.dumps(line) + "\n")
    return {"status": "deleted", "path": str(path)}


# ────────────────────────────────────────────────────────────────────────────────
# TYPE FILES
# ────────────────────────────────────────────────────────────────────────────────

@router.get("/project/{project}/{word_type}_types")
def get_word_types(project: str, word_type: str):
    path = BASE_PATH / project / f"{word_type}_types.json"
    return load_json_file(path)


@router.get("/project/{project}/{word_type}_types/{entry_name}")
def get_single_word_type(project: str, word_type: str, entry_name: str):
    data = get_word_types(project, word_type)
    if entry_name not in data:
        raise HTTPException(status_code=404, detail=f"{entry_name} not found in {word_type}_types.json")
    return data[entry_name]

# ────────────────────────────────────────────────────────────────────────────────
# NOUNS
# ────────────────────────────────────────────────────────────────────────────────

@router.get("/project/{project}/noun/{noun_type}/items")
def get_noun_items(project: str, noun_type: str):
    path = BASE_PATH / project / "nouns" / noun_type / "items.jsonl"
    return load_jsonl_file(path)


@router.get("/project/{project}/noun/{noun_type}/item")
def get_noun_item(project: str, noun_type: str,
                  primary_id: str = Query(...),
                  run_id: str = Query(...)):
    path = BASE_PATH / project / "nouns" / noun_type / "items.jsonl"
    items = load_jsonl_file(path)
    for item in items:
        if item.get(noun_type.lower() + "_id") == primary_id and item.get("_runID") == run_id:
            return item
    raise HTTPException(status_code=404, detail="Item not found for given ID and run")


# ────────────────────────────────────────────────────────────────────────────────
# VERB LOGS + TEST TYPE
# ────────────────────────────────────────────────────────────────────────────────

@router.get("/project/{project}/verb/{verb_group}/log")
def get_verb_log(project: str, verb_group: str):
    path = BASE_PATH / project / "verbs" / verb_group / f"{verb_group}_log.jsonl"
    return load_jsonl_file(path)


@router.get("/project/{project}/verb/{verb_group}/log_config")
def get_verb_log_config(project: str, verb_group: str):
    path = BASE_PATH / project / "verbs" / verb_group / f"{verb_group}_log_config.json"
    return load_json_file(path)


@router.get("/project/{project}/verb/{verb_group}/test_type/{run_id}")
def get_test_type_for_run(project: str, verb_group: str, run_id: str):
    config = get_verb_log_config(project, verb_group)
    primary_id_key = config.get("primary_id")
    logs = get_verb_log(project, verb_group)
    for entry in logs:
        if entry.get(primary_id_key) == run_id:
            return {"test_type": entry.get("test_type")}
    raise HTTPException(status_code=404, detail="Run ID not found in verb log")


# ────────────────────────────────────────────────────────────────────────────────
# VERB DATA DUMPS
# ────────────────────────────────────────────────────────────────────────────────

def get_data_dump_path(project: str, verb_group: str, run_id: str):
    return BASE_PATH / project / "verbs" / verb_group / "data_dumps" / run_id


@router.get("/project/{project}/verb/{verb_group}/run/{run_id}/data_entry")
def get_data_entry(project: str, verb_group: str, run_id: str):
    path = get_data_dump_path(project, verb_group, run_id) / "DataEntry.json"
    return load_json_file(path)


@router.get("/project/{project}/verb/{verb_group}/run/{run_id}/status")
def get_status(project: str, verb_group: str, run_id: str):
    path = get_data_dump_path(project, verb_group, run_id) / "Status.json"
    return load_json_file(path)


@router.get("/project/{project}/verb_type/{test_type}")
def get_verb_type_definition(project: str, test_type: str):
    path = BASE_PATH / project / "verb_types.json"
    verb_types = load_json_file(path)
    if test_type not in verb_types:
        raise HTTPException(status_code=404, detail="Verb type not found")
    return verb_types[test_type]


@router.get("/project/{project}/verb/{verb_group}/run/{run_id}/output/{output_file}")
def get_output_file(project: str, verb_group: str, run_id: str, output_file: str):
    path = get_data_dump_path(project, verb_group, run_id) / output_file
    if not path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")
    return {"path": str(path), "filename": path.name}


@router.get("/project/{project}/verb/{verb_group}/run/{run_id}/input/{input_type}")
def get_input_files(project: str, verb_group: str, run_id: str, input_type: str):
    folder = get_data_dump_path(project, verb_group, run_id) / input_type
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="Input folder not found")
    return {"files": [f.name for f in folder.iterdir() if f.is_file()]}


@router.get("/project/{project}/verb/{verb_group}/run/{run_id}/bundle")
def get_full_verb_bundle(project: str, verb_group: str, run_id: str):
    base = get_data_dump_path(project, verb_group, run_id)
    data_entry = load_json_file(base / "DataEntry.json")
    status = load_json_file(base / "Status.json")
    outputs = [f.name for f in base.glob("*") if f.is_file() and f.name.endswith((".csv", ".tsv"))]
    input_dirs = [d.name for d in base.iterdir() if d.is_dir()]

    input_files = {}
    for dir_name in input_dirs:
        files = [f.name for f in (base / dir_name).iterdir() if f.is_file()]
        input_files[dir_name] = files

    return {
        "data_entry": data_entry,
        "status": status,
        "outputs": outputs,
        "inputs": input_files
    }


# ────────────────────────────────────────────────────────────────────────────────
# GLOBAL PROJECT FILES
# ────────────────────────────────────────────────────────────────────────────────

@router.get("/project/{project}/overrides")
def get_overrides(project: str):
    path = BASE_PATH / project / "override.json"
    return load_json_file(path)


@router.get("/project/{project}/autogen_counters")
def get_autogen_counters(project: str):
    path = BASE_PATH / project / "autogen_counter.json"
    return load_json_file(path)

# ────────────────────────────────────────────────────────────────────────────────
# TYPE FILES - POST + PUT
# ────────────────────────────────────────────────────────────────────────────────

@router.post("/project/{project}/{word_type}_types")
def post_word_type(project: str, word_type: str, entry: dict = Body(...)):
    name = entry.get(word_type)
    if not name:
        raise HTTPException(status_code=400, detail=f"Missing key '{word_type}' in entry")
    path = BASE_PATH / project / f"{word_type}_types.json"
    data = load_json_file(path)
    if name in data:
        raise HTTPException(status_code=400, detail="Entry already exists")
    data[name] = entry
    return write_json_file(path, data)

@router.put("/project/{project}/{word_type}_types/{entry_name}")
def put_word_type(project: str, word_type: str, entry_name: str, entry: dict = Body(...)):
    path = BASE_PATH / project / f"{word_type}_types.json"
    data = load_json_file(path)
    data[entry_name] = entry
    return write_json_file(path, data)


# ────────────────────────────────────────────────────────────────────────────────
# NOUN ITEMS - POST + PUT
# ────────────────────────────────────────────────────────────────────────────────

@router.post("/project/{project}/noun/{noun_type}/item")
def post_noun_item(project: str, noun_type: str, item: dict = Body(...)):
    path = BASE_PATH / project / "nouns" / noun_type / "items.jsonl"
    return append_jsonl(path, item)

@router.put("/project/{project}/noun/{noun_type}/item")
def put_noun_item(project: str, noun_type: str,
                  primary_id: str = Query(...),
                  run_id: str = Query(...),
                  item: dict = Body(...)):
    path = BASE_PATH / project / "nouns" / noun_type / "items.jsonl"
    return replace_jsonl_entry(
        path,
        match=lambda x: x.get(noun_type.lower() + "_id") == primary_id and x.get("_runID") == run_id,
        new_entry=item
    )


# ────────────────────────────────────────────────────────────────────────────────
# VERB LOG - POST + PUT
# ────────────────────────────────────────────────────────────────────────────────

@router.post("/project/{project}/verb/{verb_group}/log")
def post_verb_log_entry(project: str, verb_group: str, entry: dict = Body(...)):
    path = BASE_PATH / project / "verbs" / verb_group / f"{verb_group}_log.jsonl"
    return append_jsonl(path, entry)

@router.put("/project/{project}/verb/{verb_group}/log")
def put_verb_log_entry(project: str, verb_group: str,
                       run_id: str = Query(...),
                       entry: dict = Body(...)):
    config_path = BASE_PATH / project / "verbs" / verb_group / f"{verb_group}_log_config.json"
    primary_id_key = load_json_file(config_path).get("primary_id")
    path = BASE_PATH / project / "verbs" / verb_group / f"{verb_group}_log.jsonl"
    return replace_jsonl_entry(
        path,
        match=lambda x: x.get(primary_id_key) == run_id,
        new_entry=entry
    )

@router.put("/project/{project}/verb/{verb_group}/log_config")
def put_verb_log_config(project: str, verb_group: str, data: dict = Body(...)):
    path = BASE_PATH / project / "verbs" / verb_group / f"{verb_group}_log_config.json"
    return write_json_file(path, data)


# ────────────────────────────────────────────────────────────────────────────────
# VERB DATA DUMPS - POST + PUT
# ────────────────────────────────────────────────────────────────────────────────

@router.put("/project/{project}/verb/{verb_group}/run/{run_id}/data_entry")
def put_data_entry(project: str, verb_group: str, run_id: str, data: dict = Body(...)):
    path = get_data_dump_path(project, verb_group, run_id) / "DataEntry.json"
    return write_json_file(path, data)

@router.put("/project/{project}/verb/{verb_group}/run/{run_id}/status")
def put_status(project: str, verb_group: str, run_id: str, data: dict = Body(...)):
    path = get_data_dump_path(project, verb_group, run_id) / "Status.json"
    return write_json_file(path, data)

@router.put("/project/{project}/verb/{verb_group}/run/{run_id}/output/{filename}")
def put_output_file(project: str, verb_group: str, run_id: str, filename: str, data: str = Body(...)):
    path = get_data_dump_path(project, verb_group, run_id) / filename
    with path.open("w", encoding="utf-8") as f:
        f.write(data)
    return {"status": "written", "path": str(path)}

@router.put("/project/{project}/verb/{verb_group}/run/{run_id}/input/{input_type}/{filename}")
def put_input_file(project: str, verb_group: str, run_id: str, input_type: str, filename: str, data: str = Body(...)):
    folder = get_data_dump_path(project, verb_group, run_id) / input_type
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    with path.open("w", encoding="utf-8") as f:
        f.write(data)
    return {"status": "written", "path": str(path)}


# ────────────────────────────────────────────────────────────────────────────────
# GLOBAL PROJECT FILES - PUT
# ────────────────────────────────────────────────────────────────────────────────

@router.put("/project/{project}/overrides")
def put_overrides(project: str, data: dict = Body(...)):
    path = BASE_PATH / project / "override.json"
    return write_json_file(path, data)

@router.put("/project/{project}/autogen_counters")
def put_autogen_counters(project: str, data: dict = Body(...)):
    path = BASE_PATH / project / "autogen_counter.json"
    return write_json_file(path, data)

# ─────────────────────────────────────────────────────────────────────────────
# PROJECT DISCOVERY
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/projects")
def list_projects():
    """
    Return a simple list of folder names under the global projects path.
    """
    return [p.name for p in BASE_PATH.iterdir() if p.is_dir()]