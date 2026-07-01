#!/usr/bin/env python3
"""
tools/migrate_nouns_to_rds.py
──────────────────────────────────────────────
Migrate a single project's local nouns.db → centralized RDS nouns_sql_db.

What this does
--------------
• Creates all project-prefixed noun tables on RDS if missing
• Ensures centralized meta_tables/meta_columns exist and have a `project` column
• Copies noun_* table data to <PROJECT>_noun_* tables in RDS
• Copies meta_tables/meta_columns rows with project awareness
• Rewrites meta_tables.table_name to include the <PROJECT>_ prefix
"""

from __future__ import annotations
import sys
import time
from pathlib import Path
from sqlalchemy import create_engine, MetaData, Table, text
from sqlalchemy.exc import SQLAlchemyError

# ──────────────────────────────────────────────────────────────────────────────
# Configuration — edit these few lines only
# ──────────────────────────────────────────────────────────────────────────────
PROJECT_NAME = "LIMS-System"
LOCAL_DB_PATH = Path(f"projects/{PROJECT_NAME}/nouns/sql/nouns.db")  # source
RDS_KEY = "noun_sql_db"      # resolver key for the nouns RDS (e.g., noun_sql_db)
RDS_URI_OVERRIDE = None      # optional manual DSN override (string)
# ──────────────────────────────────────────────────────────────────────────────

# Ensure project root is importable
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from api.manifest import resolver


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_target_uri() -> str:
    """Resolve RDS DSN via resolver or override."""
    if RDS_URI_OVERRIDE:
        return RDS_URI_OVERRIDE
    uri_async = resolver.get_db_uri(RDS_KEY)
    uri_sync = uri_async.replace("postgresql+asyncpg", "postgresql+psycopg2")
    uri_sync = uri_sync.replace("?ssl=require", "?sslmode=require")
    return uri_sync


def _prefixed_table_name(noun_table: str) -> str:
    """noun_Sample → LIMS-System_noun_Sample"""
    base = noun_table
    if base.startswith("noun_"):
        base = base[len("noun_"):]
    return f"{PROJECT_NAME}_noun_{base}"


def _ensure_schema_and_meta(dst_engine):
    """
    Ensure: search_path, meta tables exist, and both have required columns.
    This function ONLY handles the schema of the two meta tables.
    """
    print("\n[+] Ensuring RDS meta table schema...")
    with dst_engine.begin() as conn:
        try:
            conn.execute(text("SET search_path TO public"))
        except Exception:
            pass

        # Ensure meta_tables exists
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.meta_tables (
                project    TEXT,
                noun_name  TEXT,
                table_name TEXT,
                primary_id TEXT
            )
        """))
        # Ensure meta_columns exists
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS public.meta_columns (
                project     TEXT,
                noun_name   TEXT,
                column_name TEXT,
                column_type TEXT
            )
        """))

        # Add `project` column if it's missing (for legacy compatibility)
        for table in ["meta_tables", "meta_columns"]:
            cols = conn.execute(
                text("""
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_schema = 'public' 
                    AND table_name = :table AND column_name = 'project'
                """),
                {"table": table},
            ).fetchall()
            if not cols:
                print(f"  • Adding missing column 'project' to {table}")
                conn.execute(text(f'ALTER TABLE public."{table}" ADD COLUMN project TEXT'))


def _create_all_tables_on_rds_like_source(src_meta: MetaData, dst_engine):
    """Create all source `noun_*` tables on the destination with a project prefix."""
    print("\n[+] Creating prefixed noun tables on RDS (if missing)...")
    
    dst_meta = MetaData()
    noun_tables = [t for t in src_meta.tables.keys() if t.startswith("noun_")]

    for table_name in noun_tables:
        src_table = src_meta.tables[table_name]
        new_name = _prefixed_table_name(table_name)
        
        # Create a new table object for the destination with the prefixed name
        Table(new_name, dst_meta, *[c.copy() for c in src_table.columns])
    
    # Create all defined tables in one go
    if dst_meta.tables:
        dst_meta.create_all(bind=dst_engine, checkfirst=True)
        print(f"  • Ensured {len(dst_meta.tables)} prefixed noun tables exist.")
    else:
        print("  • No noun_* tables found in source to create.")


def _migrate_meta_tables_and_columns(src_engine, dst_engine):
    """
    Copy meta data from local SQLite to RDS with project awareness.

    - Deletes existing entries for the current project to ensure idempotency.
    - Copies all columns from source meta tables.
    - Adds the `project` name to each row.
    - Rewrites `meta_tables.table_name` to the prefixed `<PROJECT>_noun_*` format.
    """
    print("\n[+] Migrating meta_tables and meta_columns with project prefix...")

    with src_engine.connect() as src_conn, dst_engine.begin() as dst_conn:
        try:
            dst_conn.execute(text("SET search_path TO public"))
        except Exception:
            pass

        # META TABLES MIGRATION
        try:
            rows = src_conn.execute(text("SELECT * FROM meta_tables")).fetchall()
            print(f"  • Found {len(rows)} rows in source meta_tables.")
            
            # Clear previous entries for this project
            dst_conn.execute(text("DELETE FROM public.meta_tables WHERE project = :p"), {"p": PROJECT_NAME})

            for r in rows:
                # Copy all data from the source row
                d_out = dict(r._mapping)
                if not d_out.get("noun_name") or not d_out.get("table_name"):
                    continue

                # Add/overwrite required fields
                d_out["project"] = PROJECT_NAME
                d_out["table_name"] = _prefixed_table_name(d_out["table_name"])

                # Dynamically build insert statement to handle any schema
                columns = ", ".join(f'"{k}"' for k in d_out.keys())
                placeholders = ", ".join(f":{k}" for k in d_out.keys())
                sql = f"INSERT INTO public.meta_tables ({columns}) VALUES ({placeholders})"
                
                dst_conn.execute(text(sql), d_out)
            print(f"  • Migrated {len(rows)} rows to RDS meta_tables for project '{PROJECT_NAME}'.")

        except SQLAlchemyError as e:
            if "no such table: meta_tables" in str(e):
                 print("  • meta_tables not found in source; skipping.")
            else:
                raise e

        # META COLUMNS MIGRATION
        try:
            rows = src_conn.execute(text("SELECT * FROM meta_columns")).fetchall()
            print(f"  • Found {len(rows)} rows in source meta_columns.")

            # Clear previous entries for this project
            dst_conn.execute(text("DELETE FROM public.meta_columns WHERE project = :p"), {"p": PROJECT_NAME})

            for r in rows:
                d_out = dict(r._mapping)
                if not d_out.get("noun_name") or not d_out.get("column_name"):
                    continue
                
                d_out["project"] = PROJECT_NAME

                columns = ", ".join(f'"{k}"' for k in d_out.keys())
                placeholders = ", ".join(f":{k}" for k in d_out.keys())
                sql = f"INSERT INTO public.meta_columns ({columns}) VALUES ({placeholders})"
                
                dst_conn.execute(text(sql), d_out)
            print(f"  • Migrated {len(rows)} rows to RDS meta_columns for project '{PROJECT_NAME}'.")

        except SQLAlchemyError as e:
            if "no such table: meta_columns" in str(e):
                print("  • meta_columns not found in source; skipping.")
            else:
                raise e


def _migrate_noun_tables(src_meta: MetaData, src_engine, dst_engine):
    """Copy data from local noun_* tables to remote <PROJECT>_noun_* tables."""
    print("\n[+] Migrating data for noun tables...\n")
    noun_tables = [t for t in src_meta.tables.keys() if t.startswith("noun_")]
    if not noun_tables:
        print("  ⚠️ No noun_* tables found in local nouns.db to migrate.")
        return

    for table_name in noun_tables:
        new_table_name = _prefixed_table_name(table_name)
        print(f"[→] {table_name}  →  {new_table_name}")

        src_table = src_meta.tables[table_name]

        # Autoload the destination table which must already exist
        dst_meta = MetaData()
        dst_table = Table(new_table_name, dst_meta, autoload_with=dst_engine)

        with src_engine.connect() as src_conn, dst_engine.begin() as dst_conn:
            rows = src_conn.execute(src_table.select()).fetchall()
            if not rows:
                print("    (no rows to migrate)")
                continue
            try:
                # Clear existing data and insert fresh from source
                dst_conn.execute(dst_table.delete())
                dst_conn.execute(dst_table.insert(), [row._mapping for row in rows])
                print(f"    → Copied {len(rows)} rows.")
            except Exception as e:
                print(f"    ⚠️ Skipped due to insert error: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Migration driver
# ──────────────────────────────────────────────────────────────────────────────

def migrate_nouns():
    """Main function to drive the migration from SQLite to RDS."""
    print(f"\n[GIMS Migration] Starting migration of nouns.db for project '{PROJECT_NAME}'")

    if not LOCAL_DB_PATH.exists():
        print(f"❌ Source nouns.db not found: {LOCAL_DB_PATH}")
        return
        
    source_uri = f"sqlite+pysqlite:///{LOCAL_DB_PATH}"

    try:
        target_uri = get_target_uri()
        print(f"  • Source (SQLite): {source_uri}")
        print(f"  • Target (RDS):    {target_uri}")

        src_engine = create_engine(source_uri)
        dst_engine = create_engine(target_uri, echo=False)

        # Reflect the complete source database schema
        src_meta = MetaData()
        src_meta.reflect(bind=src_engine)
        print(f"\n  • Found {len(src_meta.tables)} tables in local nouns.db.")

        # Normalize SQLite specific defaults for Postgres compatibility
        for table in src_meta.tables.values():
            for column in table.columns:
                if column.server_default:
                    default_val = str(getattr(column.server_default, "arg", ""))
                    if "datetime('now')" in default_val.lower():
                        column.server_default = text("CURRENT_TIMESTAMP")

        # Step 1: Ensure meta tables have the correct schema on RDS.
        _ensure_schema_and_meta(dst_engine)

        # Step 2: Create prefixed noun_* tables on RDS based on source schema.
        _create_all_tables_on_rds_like_source(src_meta, dst_engine)

        # Step 3: Migrate metadata, adding project context and rewriting table names.
        _migrate_meta_tables_and_columns(src_engine, dst_engine)

        # Step 4: Migrate the actual data into the newly created noun tables.
        _migrate_noun_tables(src_meta, src_engine, dst_engine)

        print("\n✅ Noun migration complete!\n")

    except SQLAlchemyError as e:
        print(f"\n❌ SQLAlchemy error: {e}")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    t0 = time.time()
    migrate_nouns()
    print(f"[Finished in {time.time() - t0:.2f}s]")