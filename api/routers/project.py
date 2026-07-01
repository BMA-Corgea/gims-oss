from fastapi import APIRouter, Query, Body
from pathlib import Path
import json

from core.errors import AppError

router = APIRouter()
BASE_PATH = Path("projects")


def load_json_file(path: Path):
    if not path.exists():
        raise AppError("FILE_NOT_FOUND", f"{path.name} not found", status=404,
                       details={"file": path.name})
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl_file(path: Path):
    if not path.exists():
        raise AppError("FILE_NOT_FOUND", f"{path.name} not found", status=404,
                       details={"file": path.name})
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
        raise AppError("ENTRY_NOT_FOUND", "Entry to update not found", status=404)
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return {"status": "updated", "path": str(path)}

def delete_jsonl_entry(path: Path, match: callable):
    lines = load_jsonl_file(path)
    new_lines = [line for line in lines if not match(line)]
    if len(new_lines) == len(lines):
        raise AppError("ENTRY_NOT_FOUND", "Entry to delete not found", status=404)
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
        raise AppError("WORD_TYPE_NOT_FOUND", f"{entry_name} not found in {word_type}_types.json",
                       status=404, details={"entry": entry_name, "word_type": word_type})
    return data[entry_name]

# ────────────────────────────────────────────────────────────────────────────────
# NOUNS
# ────────────────────────────────────────────────────────────────────────────────

@router.get("/project/{project}/noun/{noun_type}/items")
def get_noun_items(project: str, noun_type: str):
    from api import i_o  # the one store-backed reader (unified instances table)
    return i_o.get_noun_items(BASE_PATH / project, noun_type)


@router.get("/project/{project}/noun/{noun_type}/item")
def get_noun_item(project: str, noun_type: str,
                  primary_id: str = Query(...),
                  run_id: str = Query(...)):
    from api import i_o
    items = i_o.get_noun_items(BASE_PATH / project, noun_type)
    for item in items:
        if item.get(noun_type.lower() + "_id") == primary_id and item.get("_runID") == run_id:
            return item
    raise AppError("NOUN_ITEM_NOT_FOUND", "Item not found for given ID and run", status=404,
                   details={"noun_type": noun_type, "primary_id": primary_id, "run_id": run_id})


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
    raise AppError("RUN_ID_NOT_FOUND", "Run ID not found in verb log", status=404,
                   details={"verb_group": verb_group, "run_id": run_id})


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
        raise AppError("VERB_TYPE_NOT_FOUND", "Verb type not found", status=404,
                       details={"test_type": test_type})
    return verb_types[test_type]


@router.get("/project/{project}/verb/{verb_group}/run/{run_id}/output/{output_file}")
def get_output_file(project: str, verb_group: str, run_id: str, output_file: str):
    path = get_data_dump_path(project, verb_group, run_id) / output_file
    if not path.exists():
        raise AppError("OUTPUT_FILE_NOT_FOUND", "Output file not found", status=404,
                       details={"output_file": output_file})
    return {"path": str(path), "filename": path.name}


@router.get("/project/{project}/verb/{verb_group}/run/{run_id}/input/{input_type}")
def get_input_files(project: str, verb_group: str, run_id: str, input_type: str):
    folder = get_data_dump_path(project, verb_group, run_id) / input_type
    if not folder.exists() or not folder.is_dir():
        raise AppError("INPUT_FOLDER_NOT_FOUND", "Input folder not found", status=404,
                       details={"input_type": input_type})
    return {"files": [f.name for f in folder.iterdir() if f.is_file()]}


@router.get("/project/{project}/verb/{verb_group}/run/{run_id}/bundle")
def get_full_verb_bundle(project: str, verb_group: str, run_id: str):
    base = get_data_dump_path(project, verb_group, run_id)
    # 404 only when the run itself is absent. A partially-populated run (e.g. data entered but
    # status not yet computed) must still return what it has — the Inspector renders each section
    # conditionally — rather than 404ing the whole bundle on a single missing file.
    if not base.exists():
        raise AppError("RUN_NOT_FOUND", f"Run not found: {run_id}", status=404,
                       details={"project": project, "verb_group": verb_group, "run_id": run_id})
    de_path, st_path = base / "DataEntry.json", base / "Status.json"
    data_entry = load_json_file(de_path) if de_path.exists() else {}
    status = load_json_file(st_path) if st_path.exists() else {}
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
        raise AppError("MISSING_ENTRY_KEY", f"Missing key '{word_type}' in entry", status=400,
                       details={"word_type": word_type})
    path = BASE_PATH / project / f"{word_type}_types.json"
    data = load_json_file(path)
    if name in data:
        raise AppError("ENTRY_ALREADY_EXISTS", "Entry already exists", status=400,
                       details={"word_type": word_type, "name": name})
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
    from api import i_o  # write to the unified instances store (SQL-only)
    i_o.put_noun_item(BASE_PATH / project, noun_type, item)
    return {"status": "saved"}

@router.put("/project/{project}/noun/{noun_type}/item")
def put_noun_item(project: str, noun_type: str,
                  primary_id: str = Query(...),
                  run_id: str = Query(...),
                  item: dict = Body(...)):
    from api import i_o  # upsert by primary id into the unified instances store
    i_o.put_noun_item(BASE_PATH / project, noun_type, item)
    return {"status": "saved"}


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