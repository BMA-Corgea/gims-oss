#!/usr/bin/env python3
"""
tools/migrate_one_db_to_rds.py
──────────────────────────────────────────────
Generic single-database migration: copy one local SQLite DB into its RDS
(PostgreSQL) counterpart.

How to use:
  1. Edit CONFIG below to point to your local .db and the RDS target.
  2. Run:  python tools/migrate_one_db_to_rds.py
"""

from __future__ import annotations
import sys
import time
from pathlib import Path
from sqlalchemy import create_engine, MetaData, Table, text
from sqlalchemy.exc import SQLAlchemyError

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — EDIT THIS SECTION
# ──────────────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Local SQLite file to migrate
LOCAL_SQLITE_PATH = PROJECT_ROOT / "logins" / "logins.db"

# One of:
#   1) RDS_KEY = "logins_db"   (ask resolver.get_db_uri)
#   2) RDS_URI = "postgresql+psycopg2://user:pass@host:5432/dbname"
RDS_KEY = "logins_db"
RDS_URI_OVERRIDE = None  # if you want to hardcode a DSN, set it here

# Optional: explicit copy order (for foreign key dependencies)
DEPENDENCY_ORDER = ["users", "projects", "accounts_projects"]
# ──────────────────────────────────────────────────────────────────────────────

# Ensure repo root importable
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.manifest import resolver


def get_target_uri() -> str:
    """Return a psycopg2-compatible DSN for the target RDS database."""
    if RDS_URI_OVERRIDE:
        return RDS_URI_OVERRIDE

    try:
        uri_async = resolver.get_db_uri(RDS_KEY, project_path=PROJECT_ROOT)
        uri_sync = uri_async.replace("postgresql+asyncpg", "postgresql+psycopg2")
        uri_sync = uri_sync.replace("?ssl=require", "?sslmode=require")
        return uri_sync
    except Exception as e:
        raise RuntimeError(f"Could not resolve RDS URI for {RDS_KEY}: {e}")


def migrate_sqlite_to_rds(local_path: Path, target_uri: str, dep_order: list[str]):
    """Perform migration from SQLite to Postgres (RDS)."""
    if not local_path.exists():
        print(f"❌ Local database not found: {local_path}")
        return

    source_uri = f"sqlite+pysqlite:///{local_path}"
    print(f"\n──────────────────────────────────────────────")
    print(f"[GIMS Migration] SQLite → RDS for {local_path.name}")
    print(f"──────────────────────────────────────────────")
    print(f"  • Source: {source_uri}")
    print(f"  • Target: {target_uri}\n")

    try:
        src_engine = create_engine(source_uri)
        dst_engine = create_engine(target_uri, echo=False)

        # Reflect source schema
        src_meta = MetaData()
        src_meta.reflect(bind=src_engine)
        print(f"  • Found {len(src_meta.tables)} tables in source.")

        # Normalize defaults (datetime('now') → CURRENT_TIMESTAMP)
        for table in src_meta.tables.values():
            for column in table.columns:
                try:
                    default_val = str(getattr(column.server_default, "arg", "")) if column.server_default else ""
                except Exception:
                    default_val = ""
                if "datetime('now')" in default_val.lower():
                    column.server_default = text("CURRENT_TIMESTAMP")

        print("  • Ensuring all tables exist on RDS…")
        src_meta.create_all(bind=dst_engine, checkfirst=True)

        # Determine copy order
        remaining = [t for t in src_meta.tables.keys() if t not in dep_order]
        sequence = [t for t in dep_order if t in src_meta.tables] + remaining
        print(f"  • Copy order: {sequence}\n")

        # Copy table by table
        for table_name in sequence:
            print(f"[→] Migrating table: {table_name}")
            table = Table(table_name, src_meta, autoload_with=src_engine)
            target_table = Table(table_name, MetaData(), autoload_with=dst_engine)

            with src_engine.connect() as src_conn, dst_engine.begin() as dst_conn:
                dst_conn.execute(target_table.delete())
                rows = src_conn.execute(table.select()).fetchall()
                if not rows:
                    print("    (no rows to migrate)")
                    continue

                try:
                    dst_conn.execute(target_table.insert(), [row._mapping for row in rows])
                    print(f"    → Copied {len(rows)} rows.")
                except Exception as e:
                    print(f"    ⚠️  Skipped {table_name} due to error: {e}")

        print("\n✅ Migration complete!\n")

    except SQLAlchemyError as e:
        print(f"\n❌ Database error: {e}")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")


def main():
    start = time.time()
    try:
        target_uri = get_target_uri()
    except Exception as e:
        print(f"❌ Failed to resolve RDS target: {e}")
        return
    migrate_sqlite_to_rds(LOCAL_SQLITE_PATH, target_uri, DEPENDENCY_ORDER)
    print(f"[Finished in {time.time() - start:.2f}s]")


if __name__ == "__main__":
    main()
