# gui/camera_gui.py

from fastapi import APIRouter, UploadFile, Form, HTTPException
from pathlib import Path
import json
import os
from datetime import datetime
import sqlite3

from api.manifest.resolver import resolve_path, get_db_uri
from api import i_o
from core import core_camera  # Corrected import from camera to core_camera

# Pull the table sanitizer used across the project
from core.handlers.core_noun import _sanitize_table_name

router = APIRouter()

# ──────────────────────────────────────────────────────────────────────────────
# Debug
# ──────────────────────────────────────────────────────────────────────────────
DEBUG_ENABLED = False

def debug(*args, **kwargs):
    if DEBUG_ENABLED:
        print("[camera_gui]", *args, **kwargs)

# Optional Postgres (RDS) via psycopg v3
try:
    import psycopg  # pip install psycopg[binary]
    _PSYCOPG_AVAILABLE = True
except Exception as e:
    _PSYCOPG_AVAILABLE = False
    if DEBUG_ENABLED:
        print("[camera_gui] psycopg not available:", repr(e))


# ──────────────────────────────────────────────────────────────────────────────
# Backend selection (SQLite vs RDS Postgres) — mirrors core_noun behavior
# ──────────────────────────────────────────────────────────────────────────────
def _normalize_for_psycopg(url: str) -> str:
    # 'postgresql+asyncpg://' → 'postgresql://'
    # '?ssl=require' → '?sslmode=require'
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("postgresql+", 1)[1]
    if "?ssl=require" in url:
        url = url.replace("?ssl=require", "?sslmode=require")
    return url.replace("postgresql://asyncpg://", "postgresql://")

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
    try:
        return i_o.io_list_projects()
    except Exception as e:
        # Optional: return empty list on failure instead of 500
        print(f"[list_projects] list_projects failed: {e!r}")
        return []

@router.get("/camera/project/{project}/noun_types")
def list_noun_types(project: str):
    """
    Return only noun types that have image-related adjectives or fields.
    """
    project_root = resolve_path(Path(), "project_root")
    project_path = project_root / project
    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    
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
            debug("No nouns with image fields found; returning all nouns")
            return noun_schemas

        debug(f"Filtered {len(noun_schemas)} nouns to {len(filtered_nouns)} with image capabilities")
        return filtered_nouns

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading noun types: {str(e)}")


@router.get("/camera/project/{project}/noun/{noun}/items")
def list_noun_items(project: str, noun: str):
    """
    Return all items for a noun type.
    """
    project_root = resolve_path(Path(), "project_root")
    project_path = project_root / project
    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    
    try:
        # Verify noun exists in schema
        noun_schema = i_o.get_noun_schema(project_path, noun)
        if not noun_schema:
            raise HTTPException(status_code=404, detail=f"Noun '{noun}' not found in schema")

        # Delegate to i_o (your i_o layer is already RDS-aware elsewhere)
        items = i_o.get_noun_items(project_path, noun)
        return items

    except FileNotFoundError as e:
        debug("FileNotFoundError while loading items:", str(e))
        noun_dir = project_path / "nouns" / noun
        if not noun_dir.exists():
            raise HTTPException(status_code=404, detail=f"Noun directory for '{noun}' not found")
        return []  # No items.jsonl yet → empty list

    except Exception as e:
        debug(f"Error loading items for {noun}:", str(e))
        raise HTTPException(status_code=500, detail=f"Error loading items: {str(e)}")


@router.get("/camera/project/{project}/noun_types/{noun}")
def get_noun_schema(project: str, noun: str):
    """
    Return the schema for a specific noun type.
    """
    project_root = resolve_path(Path(), "project_root")
    project_path = project_root / project
    if not project_path.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    schema = i_o.get_noun_schema(project_path, noun)
    if not schema:
        raise HTTPException(status_code=404, detail=f"Noun '{noun}' not found in schema")

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
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    # Load schema
    noun_schema = i_o.get_noun_schema(project_path, noun)
    if not noun_schema:
        raise HTTPException(status_code=404, detail=f"Noun '{noun}' not found in schema")

    primary_id_field = noun_schema.get("primary_id_field")
    if not primary_id_field:
        raise HTTPException(status_code=400, detail=f"No primary_id_field defined for noun '{noun}'")

    # Determine backend + table names
    kind, target = _effective_nouns_target(project_path)
    project_name = _project_name(project_path)
    table_sqlite = _sanitize_table_name(noun)
    table_pg = _prefixed(project_name, table_sqlite)

    debug("Backend selection:", {"kind": kind, "target": target, "table_sqlite": table_sqlite, "table_pg": table_pg})

    # Verify item exists (depending on chosen storage_backend)
    noun_item = None

    # JSONL/legacy check (allowed when storage_backend includes jsonl or both)
    if storage_backend in ["jsonl", "both"]:
        try:
            items = i_o.get_noun_items(project_path, noun)
            noun_item = next((i for i in items if i.get(primary_id_field) == item_id), None)
        except Exception as e:
            debug("JSONL items lookup failed (non-fatal):", str(e))

    # SQL check (only if not found yet and storage_backend permits sql)
    if not noun_item and storage_backend in ["sql", "both"]:
        if kind == "pg":
            if not _PSYCOPG_AVAILABLE:
                raise HTTPException(
                    status_code=500,
                    detail="RDS Postgres is configured but psycopg is not available on this server."
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
                debug("Postgres lookup error:", repr(e))
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
                    debug("SQLite lookup error:", repr(e))

    if not noun_item:
        raise HTTPException(status_code=404, detail=f"Item '{item_id}' not found for noun '{noun}'")

    # Read the file contents
    contents = await file.read()

    # Generate a timestamp-based filename for blob/empty names
    if not file.filename or file.filename == "blob":
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        extension = "jpg" if file.content_type == "image/jpeg" else "png"
        file.filename = f"{timestamp}_webcam_capture.{extension}"

    # Image storage path
    images_dir = resolve_path(project_path, "noun_images", noun_type=noun)
    images_dir.mkdir(parents=True, exist_ok=True)

    file_path = images_dir / file.filename
    file_path.write_bytes(contents)

    # Relative path for DB fields
    rel_path = f"nouns/{noun}/images/{file.filename}"

    updates = {
        "jsonl_updated": False,
        "sql_updated": False,
        "dataentry_updated": False,
    }

    # Update JSONL (if selected and file exists)
    if storage_backend in ["jsonl", "both"]:
        try:
            items_path = resolve_path(project_path, "noun_items", noun_type=noun)
            if items_path.exists():
                lines = [json.loads(line) for line in items_path.read_text().splitlines() if line.strip()]
                changed = False
                for entry in lines:
                    if entry.get(primary_id_field) == item_id:
                        entry["image"] = rel_path
                        changed = True
                        break
                if changed:
                    items_path.write_text("".join(json.dumps(line) + "\n" for line in lines))
                    updates["jsonl_updated"] = True
        except Exception as e:
            debug("JSONL update error (non-fatal):", repr(e))

    # Update SQL backend (SQLite or Postgres/RDS)
    if storage_backend in ["sql", "both"]:
        if kind == "pg":
            if not _PSYCOPG_AVAILABLE:
                raise HTTPException(
                    status_code=500,
                    detail="RDS Postgres is configured but psycopg is not available on this server."
                )
            try:
                with psycopg.connect(target, autocommit=True) as conn:
                    with conn.cursor() as cur:
                        # Ensure table exists (harmless if already exists)
                        cur.execute(f'''
                            CREATE TABLE IF NOT EXISTS public."{table_pg}" (
                                "_rowid" BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY
                            )
                        ''')
                        # Ensure "image" column exists
                        cur.execute("""
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema='public' AND table_name=%s AND column_name='image'
                        """, (table_pg,))
                        if cur.fetchone() is None:
                            cur.execute(f'ALTER TABLE public."{table_pg}" ADD COLUMN "image" TEXT')

                        # Perform the update
                        cur.execute(
                            f'UPDATE public."{table_pg}" SET "image" = %s WHERE "{primary_id_field}" = %s',
                            (rel_path, item_id)
                        )
                        if cur.rowcount and cur.rowcount > 0:
                            updates["sql_updated"] = True
            except Exception as e:
                debug("Postgres update error:", repr(e))
        else:
            # SQLite
            sql_db_path = resolve_path(project_path, "object_sql_db")
            if sql_db_path.exists():
                try:
                    with sqlite3.connect(sql_db_path) as conn:
                        cur = conn.cursor()
                        # Ensure image column exists
                        cur.execute(f'PRAGMA table_info("{table_sqlite}")')
                        columns = [row[1] for row in cur.fetchall()]
                        if "image" not in columns:
                            cur.execute(f'ALTER TABLE "{table_sqlite}" ADD COLUMN image TEXT')
                        # Update the record
                        cur.execute(
                            f'UPDATE "{table_sqlite}" SET image = ? WHERE "{primary_id_field}" = ?',
                            (rel_path, item_id)
                        )
                        if cur.rowcount and cur.rowcount > 0:
                            updates["sql_updated"] = True
                        conn.commit()
                except sqlite3.Error as e:
                    debug("SQLite update error:", repr(e))

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
                    debug("Error updating DataEntry.json:", repr(e))

    return {
        "message": "✅ Image uploaded successfully",
        "noun": noun,
        "item_id": item_id,
        "file_path": str(file_path),
        "relative_path": rel_path,
        "storage_backend": storage_backend,
        **updates,
        "backend_kind": kind,
        "table_used": table_pg if kind == "pg" else table_sqlite,
    }
