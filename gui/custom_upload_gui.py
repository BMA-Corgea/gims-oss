# gui/custom_upload_gui.py
from __future__ import annotations

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import JSONResponse
from pathlib import Path
from typing import Optional, List, Dict, Any
import re
import shutil

# Project utilities
from api.manifest.resolver import resolve_path  # used only to compute repo root from project_path
from api.i_o import load_schema, save_schema, io_list_projects

router = APIRouter(prefix="/custom_upload", tags=["custom"])

# -----------------------------
# Helpers
# -----------------------------

_valid_name = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def _sanitize_parser_name(name: str) -> str:
    """
    Ensure a safe Python module-ish filename (no extension).
    """
    stem = Path(name).stem  # strip any extension the client sent
    stem = stem.strip()
    if not _valid_name.match(stem):
        raise HTTPException(
            status_code=400,
            detail="Invalid parser name. Use letters, digits, and underscores; must not start with a digit.",
        )
    return stem

def _repo_root_from_project(project: str) -> Path:
    """
    We use the resolver's 'project_root' to get .../projects, then take the parent for repo root.
    """
    project_path = Path("projects") / project
    projects_dir = resolve_path(project_path, "project_root")  # .../GIMS-Project/projects
    repo_root = projects_dir.parent  # .../GIMS-Project
    return repo_root

def _project_path(project: str) -> Path:
    return Path("projects") / project

def _custom_dir(repo_root: Path) -> Path:
    d = repo_root / "custom"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _append_parser_to_verb(project_path: Path, verb: str, parser_name: str) -> Dict[str, Any]:
    vt = load_schema(project_path, "verb")  # dict of verb_name -> schema
    if verb not in vt:
        raise HTTPException(status_code=404, detail=f"Verb '{verb}' not found in verb_types.json")

    block = vt[verb]
    des = block.setdefault("data_entry_schema", {})
    interp = des.setdefault("interpretation", {})
    # If the method isn't set, default to 'parsed' for parser workflows
    interp.setdefault("method", "parsed")
    parsers = interp.setdefault("parsers", [])

    if parser_name not in parsers:
        parsers.append(parser_name)

    save_schema(project_path, "verb", vt)
    return {"verb": verb, "parsers": parsers}

def _remove_parser_from_verbs(project_path: Path, parser_name: str, verb: Optional[str] = None) -> Dict[str, Any]:
    vt = load_schema(project_path, "verb")
    modified = False
    touched: Dict[str, List[str]] = {}

    def _prune_list(lst: list) -> list:
        return [x for x in lst if x != parser_name]

    if verb:
        block = vt.get(verb)
        if not block:
            raise HTTPException(status_code=404, detail=f"Verb '{verb}' not found in verb_types.json")

        interp = block.get("data_entry_schema", {}).get("interpretation", {})
        parsers = interp.get("parsers", [])
        new_parsers = _prune_list(parsers)
        if new_parsers != parsers:
            block.setdefault("data_entry_schema", {}).setdefault("interpretation", {})["parsers"] = new_parsers
            touched[verb] = new_parsers
            modified = True
    else:
        for vname, block in vt.items():
            interp = block.get("data_entry_schema", {}).get("interpretation", {})
            parsers = interp.get("parsers", [])
            if not parsers:
                continue
            new_parsers = _prune_list(parsers)
            if new_parsers != parsers:
                block.setdefault("data_entry_schema", {}).setdefault("interpretation", {})["parsers"] = new_parsers
                touched[vname] = new_parsers
                modified = True

    if modified:
        save_schema(project_path, "verb", vt)

    return {"modified": modified, "touched": touched}

# -----------------------------
# Endpoints (project moved into path)
# -----------------------------

@router.post("/{project}/upload_parser")
async def upload_parser(
    project: str,
    verb: Optional[str] = Form(None, description="Verb (test type) to assign this parser to"),
    kind: str = Form("parser", description="One of: 'parser' | 'pphrase'. Only 'parser' is verb-bound."),
    overwrite: bool = Form(False, description="Allow overwriting an existing file"),
    file: UploadFile = File(..., description="Python script (.py)"),
    explicit_name: Optional[str] = Form(None, description="Optional explicit parser name (without .py)"),
):
    """
    Save the uploaded .py into:
      projects/{project}/custom/parsers/{name}/{name}.py  (if kind='parser')
      projects/{project}/custom/prepositional phrases/{name}/{name}.py  (if kind='pphrase')

    If kind='parser' and verb is supplied, append to that verb's interpretation.parsers list.
    """
    if not file.filename.lower().endswith(".py"):
        raise HTTPException(status_code=400, detail="Only .py files are accepted.")

    parser_name = _sanitize_parser_name(explicit_name or Path(file.filename).stem)

    # Resolve the correct base dir using layout map
    project_path = _project_path(project)
    if kind == "parser":
        base_dir = resolve_path(project_path, "custom_parser_dir")
    elif kind == "pphrase":
        base_dir = resolve_path(project_path, "prepositional_phrases_dir")
    else:
        raise HTTPException(status_code=400, detail=f"Invalid kind '{kind}' — must be 'parser' or 'pphrase'.")

    target_dir = base_dir / parser_name
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{parser_name}.py"

    if target_file.exists() and not overwrite:
        raise HTTPException(status_code=409, detail=f"{kind.title()} '{parser_name}' already exists. Pass overwrite=true to replace.")

    tmp_file = target_file.with_suffix(".py.tmp")
    with tmp_file.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    tmp_file.replace(target_file)

    result: Dict[str, Any] = {
        "status": "ok",
        "saved": str(target_file),
        "parser_name": parser_name,
        "kind": kind,
    }

    if kind == "parser" and verb:
        appended = _append_parser_to_verb(project_path, verb, parser_name)
        result["assigned"] = appended

    return JSONResponse(result)

@router.get("/{project}/list")
def list_custom_scripts(project: str):
    """
    List parser and prepositional phrase folders separately under:
      projects/{project}/custom/parsers/
      projects/{project}/custom/prepositional phrases/
    """
    project_path = _project_path(project)

    # Resolve paths from layout map
    parser_base = resolve_path(project_path, "custom_parser_dir")
    pphrase_base = resolve_path(project_path, "prepositional_phrases_dir")

    def list_dir(base_dir: Path):
        items = []
        if base_dir.exists():
            for folder in sorted(base_dir.iterdir()):
                if folder.is_dir():
                    py_files = list(folder.glob("*.py"))
                    total_size = sum(f.stat().st_size for f in py_files)
                    items.append({
                        "name": folder.name,
                        "path": str(folder),
                        "size": total_size,
                        "files": [f.name for f in py_files]
                    })
        return items

    return {
        "count_parsers": len(list_dir(parser_base)),
        "parsers": list_dir(parser_base),
        "count_pphrases": len(list_dir(pphrase_base)),
        "pphrases": list_dir(pphrase_base),
    }

@router.post("/{project}/assign")
def assign_parser_to_verb(
    project: str,
    verb: str,
    parser_name: str,
):
    """
    Assign an existing parser to a verb (no file upload).
    Accepts verb & parser_name via query params (or form-encoded), project in path.
    """
    _ = _sanitize_parser_name(parser_name)
    project_path = _project_path(project)
    appended = _append_parser_to_verb(project_path, verb, parser_name)
    return {"status": "ok", **appended}

@router.delete("/{project}/unassign")
def unassign_parser_from_verb(
    project: str,
    parser_name: str,
    verb: str,
):
    """
    Remove a parser from a specific verb (does not delete the file).
    Accepts verb & parser_name via query params (or form-encoded), project in path.
    """
    _ = _sanitize_parser_name(parser_name)
    project_path = _project_path(project)
    res = _remove_parser_from_verbs(project_path, parser_name, verb=verb)
    return {"status": "ok", **res}

@router.get("/{project}/assignments")
def get_verb_assignments(project: str):
    """Return which parsers are assigned to which verbs."""
    try:
        project_path = _project_path(project)
        verb_types = load_schema(project_path, "verb")
        
        assignments = {}
        for verb_name, verb_data in verb_types.items():
            parsers = verb_data.get("data_entry_schema", {}).get("interpretation", {}).get("parsers", [])
            if parsers:
                assignments[verb_name] = parsers
        
        return {"assignments": assignments}
    except FileNotFoundError:
        return {"assignments": {}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not load verb assignments: {str(e)}")

@router.delete("/{project}/{parser_name}")
def delete_parser(
    project: str,
    parser_name: str,
):
    """
    Schema-only removal: scrub parser from all verb schemas.
    Files in /custom are NEVER deleted by this endpoint.
    """
    name = _sanitize_parser_name(parser_name)
    project_path = _project_path(project)

    # Scrub from all verbs
    scrub = _remove_parser_from_verbs(project_path, name, verb=None)

    return {
        "status": "ok",
        "file_deleted": False,
        "scrubbed": scrub,
    }

@router.get("/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        return io_list_projects()
    except Exception as e:
        # Optional: return empty list on failure instead of 500
        print(f"[list_projects] list_projects failed: {e!r}")
        return []

@router.get("/{project}/verbs")
def list_verbs(project: str):
    """Return a list of available verbs for the given project."""
    try:
        project_path = _project_path(project)
        verb_types = load_schema(project_path, "verb")
        return {"verbs": list(verb_types.keys())}
    except FileNotFoundError:
        # Return empty list if verb_types.json doesn't exist yet
        return {"verbs": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not load verbs for project '{project}': {str(e)}")

@router.get("/rds_mode")
def get_rds_mode():
    """
    Return whether RDS mode is enabled in this deployment.
    The frontend uses this to disable upload/hotload features
    when running under Elastic Beanstalk or RDS-based instances.
    """
    from api.manifest import resolver
    return {
        "rds_enabled": bool(getattr(resolver, "RDS_ENABLED", False)),
    }