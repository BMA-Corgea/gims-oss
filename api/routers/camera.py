# api/routers/camera.py

from fastapi import APIRouter, UploadFile, Form
from pathlib import Path
import json
from datetime import datetime
import sqlite3

from core.errors import AppError

from api.manifest.resolver import resolve_path, get_db_uri
from api import i_o

# Pull the table sanitizer used across the project
from core.handlers.noun import _sanitize_table_name
from api.storage_aws import normalize_pg_dsn as _normalize_for_psycopg

router = APIRouter()

# ──────────────────────────────────────────────────────────────────────────────
# Debug
# ──────────────────────────────────────────────────────────────────────────────
from utils.logger import get_logger
log = get_logger(__name__)

# Optional Postgres (RDS) via psycopg v3
try:
    import psycopg  # pip install psycopg[binary]
    _PSYCOPG_AVAILABLE = True
except Exception as e:
    _PSYCOPG_AVAILABLE = False
    log.debug("psycopg not available:", repr(e))


# ──────────────────────────────────────────────────────────────────────────────
# Backend selection (SQLite vs RDS Postgres) — mirrors core_noun behavior
# ──────────────────────────────────────────────────────────────────────────────
def _effective_nouns_target(project_path: Path) -> tuple[str, str]:
    """
    Returns (kind, target):
      - ("pg", DSN) if resolver returns a Postgres URI for object_sql_db
      - ("sqlite", /abs/path/to/objects.db) otherwise
    """
    try:
        uri = get_db_uri("object_sql_db")
    except Exception:
        uri = None
    if uri and uri.startswith("postgresql"):
        return ("pg", _normalize_for_psycopg(uri))
    db_path = resolve_path(project_path, "object_sql_db")
    return ("sqlite", db_path.as_posix())

def _project_name(project_path: Path) -> str:
    return project_path.name

def _prefixed(project: str, noun_table: str) -> str:
    # noun_Sample -> <Project>_noun_Sample  (RDS shared DB layout)
    base = noun_table
    if base.startswith("noun_"):
        base = base[len("noun_"):]
    return f"{project}_noun_{base}"


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/camera/projects")
def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    return i_o.list_projects_safe()

@router.get("/camera/project/{project}/noun_types")
def list_noun_types(project: str):
    """
    Return only noun types that have image-related adjectives or fields.
    """
    project_root = resolve_path(Path(), "project_root")
    project_path = project_root / project
    if not project_path.exists():
        raise AppError("PROJECT_NOT_FOUND", f"Project '{project}' not found", status=404, details={"project": project})

    try:
        noun_schemas = i_o.load_schema(project_path, "noun")
        try:
            adjective_schemas = i_o.load_schema(project_path, "adjective")
        except Exception:
            adjective_schemas = []

        # Identify potentially image-related adjectives
        image_adjectives = []
        for adj in adjective_schemas:
            if isinstance(adj, dict):
                adj_name = adj.get("adjective", "").lower()
                adj_type = adj.get("type", "").lower()
                if any(term in adj_name or term in adj_type for term in ["image", "photo", "picture", "file", "camera"]):
                    image_adjectives.append(adj)

        # Filter nouns with image adjectives or image-ish fields
        filtered_nouns = {}
        for noun_name, noun_schema in noun_schemas.items():
            applies_to_this_noun = False
            for adj in image_adjectives:
                applies_to = adj.get("applies_to", [])
                if noun_name in applies_to:
                    applies_to_this_noun = True
                    break

            has_image_field = False
            for field_name in noun_schema.get("fields", {}).keys():
                if any(term in field_name.lower() for term in ["image", "photo", "picture", "file"]):
                    has_image_field = True
                    break

            if applies_to_this_noun or has_image_field:
                filtered_nouns[noun_name] = noun_schema

        if not filtered_nouns:
            log.debug("No nouns with image fields found; returning all nouns")
            return noun_schemas

        log.debug(f"Filtered {len(noun_schemas)} nouns to {len(filtered_nouns)} with image capabilities")
        return filtered_nouns

    except Exception as e:
        raise AppError("NOUN_TYPES_LOAD_FAILED", f"Error loading noun types: {str(e)}", status=500, details={"project": project})


@router.get("/camera/project/{project}/noun/{noun}/items")
def list_noun_items(project: str, noun: str):
    """
    Return all items for a noun type.
    """
    project_root = resolve_path(Path(), "project_root")
    project_path = project_root / project
    if not project_path.exists():
        raise AppError("PROJECT_NOT_FOUND", f"Project '{project}' not found", status=404, details={"project": project})

    try:
        # Verify noun exists in schema
        noun_schema = i_o.get_noun_schema(project_path, noun)
        if not noun_schema:
            raise AppError("NOUN_NOT_FOUND", f"Noun '{noun}' not found in schema", status=404, details={"project": project, "noun": noun})

        # Delegate to i_o (your i_o layer is already RDS-aware elsewhere)
        items = i_o.get_noun_items(project_path, noun)
        return items

    except FileNotFoundError as e:
        log.debug("FileNotFoundError while loading items:", str(e))
        noun_dir = project_path / "nouns" / noun
        if not noun_dir.exists():
            raise AppError("NOUN_DIRECTORY_NOT_FOUND", f"Noun directory for '{noun}' not found", status=404, details={"project": project, "noun": noun})
        return []  # No items.jsonl yet → empty list

    except Exception as e:
        log.debug(f"Error loading items for {noun}:", str(e))
        raise AppError("NOUN_ITEMS_LOAD_FAILED", f"Error loading items: {str(e)}", status=500, details={"project": project, "noun": noun})


@router.get("/camera/project/{project}/noun_types/{noun}")
def get_noun_schema(project: str, noun: str):
    """
    Return the schema for a specific noun type.
    """
    project_root = resolve_path(Path(), "project_root")
    project_path = project_root / project
    if not project_path.exists():
        raise AppError("PROJECT_NOT_FOUND", f"Project '{project}' not found", status=404, details={"project": project})

    schema = i_o.get_noun_schema(project_path, noun)
    if not schema:
        raise AppError("NOUN_NOT_FOUND", f"Noun '{noun}' not found in schema", status=404, details={"project": project, "noun": noun})

    return schema


@router.post("/camera/upload/{project}/{noun}")
async def upload_camera_image(
    project: str,
    noun: str,
    item_id: str = Form(...),
    run_id: str | None = Form(None),
    file: UploadFile = Form(...),
    storage_backend: str = Form("both")  # "jsonl", "sql", or "both"
):
    """
    Upload an image for a noun instance.
    Validates with core_camera, then applies updates to items.jsonl and/or SQL database.

    Args:
        storage_backend: "jsonl" (legacy), "sql" (database only), or "both" (default)
    """
    project_root = resolve_path(Path(), "project_root")
    project_path = project_root / project
    if not project_path.exists():
        raise AppError("PROJECT_NOT_FOUND", f"Project '{project}' not found", status=404, details={"project": project})

    # Load schema
    noun_schema = i_o.get_noun_schema(project_path, noun)
    if not noun_schema:
        raise AppError("NOUN_NOT_FOUND", f"Noun '{noun}' not found in schema", status=404, details={"project": project, "noun": noun})

    primary_id_field = noun_schema.get("primary_id_field")
    if not primary_id_field:
        raise AppError("PRIMARY_ID_FIELD_MISSING", f"No primary_id_field defined for noun '{noun}'", status=400, details={"project": project, "noun": noun})

    # Determine backend + table names
    kind, target = _effective_nouns_target(project_path)
    project_name = _project_name(project_path)
    table_sqlite = _sanitize_table_name(noun)
    table_pg = _prefixed(project_name, table_sqlite)

    log.debug("Backend selection:", {"kind": kind, "target": target, "table_sqlite": table_sqlite, "table_pg": table_pg})

    # Verify item exists (depending on chosen storage_backend)
    noun_item = None

    # JSONL/legacy check (allowed when storage_backend includes jsonl or both)
    if storage_backend in ["jsonl", "both"]:
        try:
            items = i_o.get_noun_items(project_path, noun)
            noun_item = next((i for i in items if i.get(primary_id_field) == item_id), None)
        except Exception as e:
            log.debug("JSONL items lookup failed (non-fatal):", str(e))

    # SQL check (only if not found yet and storage_backend permits sql)
    if not noun_item and storage_backend in ["sql", "both"]:
        if kind == "pg":
            if not _PSYCOPG_AVAILABLE:
                raise AppError(
                    "PSYCOPG_UNAVAILABLE",
                    "RDS Postgres is configured but psycopg is not available on this server.",
                    status=500,
                )
            try:
                with psycopg.connect(target, autocommit=True) as conn:
                    with conn.cursor() as cur:
                        cur.execute(f'SELECT * FROM public."{table_pg}" WHERE "{primary_id_field}" = %s', (item_id,))
                        row = cur.fetchone()
                        if row is not None:
                            colnames = [d[0] for d in cur.description]
                            noun_item = dict(zip(colnames, row))
            except Exception as e:
                log.debug("Postgres lookup error:", repr(e))
        else:
            # SQLite
            sql_db_path = resolve_path(project_path, "object_sql_db")
            if sql_db_path.exists():
                try:
                    with sqlite3.connect(sql_db_path) as conn:
                        conn.row_factory = sqlite3.Row
                        cur = conn.cursor()
                        cur.execute(f'SELECT * FROM "{table_sqlite}" WHERE "{primary_id_field}" = ?', (item_id,))
                        row = cur.fetchone()
                        if row:
                            noun_item = dict(row)
                except Exception as e:
                    log.debug("SQLite lookup error:", repr(e))

    if not noun_item:
        raise AppError("NOUN_ITEM_NOT_FOUND", f"Item '{item_id}' not found for noun '{noun}'", status=404, details={"project": project, "noun": noun, "item_id": item_id})

    # Read the file contents
    contents = await file.read()

    # Generate a timestamp-based filename for blob/empty names
    if not file.filename or file.filename == "blob":
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        extension = "jpg" if file.content_type == "image/jpeg" else "png"
        file.filename = f"{timestamp}_webcam_capture.{extension}"

    # Store the image in its own folder (images/<noun>/<file>) and reference it from the SQL record.
    from core.storage.images import put_image
    image_key = put_image(project_path, noun, file.filename, contents)
    rel_path = image_key  # the noun record + DataEntry snapshot reference this key

    updates = {"jsonl_updated": False, "sql_updated": False, "dataentry_updated": False}

    # Update the noun record's image field in the unified instances store.
    try:
        from core.storage.factory import collection_for_noun, get_record_store
        store = get_record_store(project_path)
        coll = collection_for_noun(noun)
        rec = store.get_record(coll, primary_id_field, item_id)
        if rec is not None:
            rec["image"] = image_key
            store.put_record(coll, primary_id_field, rec)
            updates["sql_updated"] = True
    except Exception as e:
        log.debug("instances image update error:", repr(e))

    # Update DataEntry.json if run_id provided
    if run_id:
        verbs_dir = project_path / "verbs"
        for verb_group in verbs_dir.iterdir():
            if not verb_group.is_dir():
                continue
            data_dump_path = verb_group / "data_dumps" / run_id / "DataEntry.json"
            if data_dump_path.exists():
                try:
                    data = json.loads(data_dump_path.read_text())
                    changed = False
                    if isinstance(data, list):
                        for rec in data:
                            if rec.get(primary_id_field) == item_id:
                                rec["image"] = rel_path
                                changed = True
                    elif isinstance(data, dict):
                        if data.get(primary_id_field) == item_id:
                            data["image"] = rel_path
                            changed = True
                    if changed:
                        data_dump_path.write_text(json.dumps(data, indent=2))
                        updates["dataentry_updated"] = True
                        break
                except Exception as e:
                    log.debug("Error updating DataEntry.json:", repr(e))

    return {
        "message": "✅ Image uploaded successfully",
        "noun": noun,
        "item_id": item_id,
        "file_path": str(rel_path),
        "relative_path": rel_path,
        "storage_backend": storage_backend,
        **updates,
        "backend_kind": kind,
        "table_used": table_pg if kind == "pg" else table_sqlite,
    }
