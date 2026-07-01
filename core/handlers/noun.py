# core/handlers/core_noun.py
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Optional, Tuple

# RDS resolver + DSN utilities
from api.manifest.resolver import resolve_path, get_db_uri

# Debug control
from utils.logger import get_logger
log = get_logger(__name__)

# Optional Postgres (RDS)
try:
    import psycopg  # psycopg v3
    _PSYCOPG_AVAILABLE = True
except Exception as e:
    _PSYCOPG_AVAILABLE = False
    log.debug("psycopg not available:", repr(e))

TYPE_MAP = {
    "string": "string",
    "number": "float",
    "date": "date",
    "datetime": "datetime",   # a date WITH a time-of-day (ISO-8601 instant)
    "adjective": "adjective",
}
VALID_FIELD_TYPES = set(TYPE_MAP.values())

_SANITIZE_RE = re.compile(r"[^0-9a-zA-Z_]")


# ──────────────────────────────────────────────────────────────────────────────
# Backend selection (SQLite offline vs RDS Postgres)
# ──────────────────────────────────────────────────────────────────────────────
def _normalize_for_psycopg(url: str) -> str:
    from api.storage_aws import normalize_pg_dsn
    return normalize_pg_dsn(url)

def _effective_nouns_target(project_path: Path) -> Tuple[str, str]:
    """
    Returns (kind, target):
      - ("pg", DSN) if resolver returns a Postgres URI for object_sql_db
      - ("sqlite", /abs/path/to/nouns.db) otherwise
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


# ──────────────────────────────────────────────────────────────────────────────
# Table / name helpers (RDS-aware)
# ──────────────────────────────────────────────────────────────────────────────
def _sanitize_table_name(noun: str) -> str:
    base = _SANITIZE_RE.sub("_", noun).strip("_")
    if not base or not base[0].isalpha():
        base = f"T_{base}"
    return f"noun_{base}"

def _prefixed(project: str, noun_table: str) -> str:
    # noun_Sample -> <Project>_noun_Sample
    base = noun_table
    if base.startswith("noun_"):
        base = base[len("noun_"):]
    return f'{project}_noun_{base}'


# ──────────────────────────────────────────────────────────────────────────────
# Column typing helpers
# ──────────────────────────────────────────────────────────────────────────────
def _schema_sql_type(ftype: str) -> str:
    if ftype in ("float", "number"):
        return "REAL"      # SQLite
    return "TEXT"          # default (TEXT for date/string/adjective)

def _pytype_to_sqlite(ftype: str) -> str:
    if ftype in ("string", "date", "datetime", "adjective"):
        return "TEXT"
    if ftype in ("float", "number"):
        return "REAL"
    return "TEXT"

def _pytype_to_pg(ftype: str) -> str:
    if ftype in ("string", "adjective", "date", "datetime"):
        return "TEXT"
    if ftype in ("float", "number"):
        return "DOUBLE PRECISION"
    return "TEXT"


# ──────────────────────────────────────────────────────────────────────────────
# Adjective field detection
# ──────────────────────────────────────────────────────────────────────────────
def _is_adjective_fielddef(fdef: dict) -> bool:
    if not isinstance(fdef, dict):
        return False
    if fdef.get("type") == "adjective":
        return True
    return "adjective_class" in fdef

def _preserve_adjective_attrs(dst: dict, src: dict) -> None:
    if not isinstance(dst, dict) or not isinstance(src, dict):
        return
    if "adjective_class" in src:
        dst["adjective_class"] = src["adjective_class"]
    for k, v in src.items():
        if k.startswith("adjective_"):
            dst[k] = v


# ──────────────────────────────────────────────────────────────────────────────
# NounType
# ──────────────────────────────────────────────────────────────────────────────
class NounType:
    """
    Schema-focused handler for a noun type.

    JSONL is no longer used for items. We reflect schema into:
      - meta_tables / meta_columns (RDS adds `project` column)
      - per-noun SQL tables:
          * SQLite: "_rowid" INTEGER PRIMARY KEY AUTOINCREMENT
          * Postgres: "_rowid" BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY
        plus one column per schema field (non-unique, non-PK) and an index on primary_id_field.
    """

    def __init__(self, name: str, schema: dict, project_path: Path):
        self.name = name
        self.schema = schema
        self.project_path = project_path
        log.debug(f"Initialized NounType(name={name})")
        try:
            self._sync_sql_table_non_destructive()
        except Exception as e:
            log.debug(f"Table ensure skipped (init): {e}")

    # -----------------------------
    # Validation / structure
    # -----------------------------
    def validate_field_structure(self):
        fields = self.schema.get("fields", {})
        for field_name, field in fields.items():
            ftype = field.get("type")
            if ftype not in VALID_FIELD_TYPES:
                raise ValueError(f"Invalid type '{ftype}' for field '{field_name}'")
        pid = self.schema.get("primary_id_field")
        if pid and pid not in fields:
            raise ValueError(f"primary_id_field '{pid}' not found in fields")

    # -----------------------------
    # Field operations
    # -----------------------------
    def add_field(self, field_name: str, field_type: str, required: bool = False, format: Optional[str] = None):
        internal_type = TYPE_MAP.get(field_type, field_type)
        entry = {"type": internal_type}
        if required:
            entry["required"] = True
        if internal_type == "date" and format:
            entry["format"] = format
        self.schema.setdefault("fields", {})[field_name] = entry
        self._sync_sql_table_non_destructive(new_field=(field_name, internal_type))
        self._sync_sql_meta_columns()

    def has_field(self, field_name: str) -> bool:
        return field_name in self.schema.get("fields", {})

    def edit_field(
        self,
        field_name: str,
        new_type: Optional[str] = None,
        required: Optional[bool] = None,
        format_override: Optional[str] = None
    ):
        fields = self.schema.get("fields", {})
        if field_name not in fields:
            raise KeyError(f"Field '{field_name}' not found")

        original = dict(fields[field_name])  # preserve adjective_*
        is_adj = _is_adjective_fielddef(original)

        if new_type:
            if is_adj and TYPE_MAP.get(new_type, new_type) != original.get("type"):
                raise ValueError(
                    f"Cannot change type of adjective field '{field_name}'. "
                    f"Adjective attributes are immutable (rename only)."
                )
            internal_type = TYPE_MAP.get(new_type, new_type)
            fields[field_name]["type"] = internal_type
            if internal_type == "date":
                if format_override:
                    fields[field_name]["format"] = format_override
                else:
                    fields[field_name].pop("format", None)
            else:
                fields[field_name].pop("format", None)

        if required is not None:
            if required:
                fields[field_name]["required"] = True
            else:
                fields[field_name].pop("required", None)

        _preserve_adjective_attrs(fields[field_name], original)

        self._sync_sql_meta_columns()
        self._sync_sql_table_non_destructive()

    def delete_field(self, field_name: str) -> dict:
        fields = self.schema.get("fields", {})
        spec = fields.get(field_name) or {}
        was_adjective = _is_adjective_fielddef(spec)
        fields.pop(field_name, None)
        self._sync_sql_meta_columns()
        # NOTE: we do NOT drop physical columns (non-destructive)
        return {"deleted": field_name, "was_adjective": was_adjective}

    def rename_field(self, old_name: str, new_name: str) -> dict:
        fields = self.schema.get("fields", {})
        if old_name not in fields:
            raise KeyError(f"Field '{old_name}' not found in noun type '{self.name}'")
        if new_name in fields:
            raise KeyError(f"Field '{new_name}' already exists in noun type '{self.name}'")

        was_adjective = _is_adjective_fielddef(fields[old_name])

        fields[new_name] = fields.pop(old_name)
        new_type = self.schema.get("fields", {}).get(new_name, {}).get("type", "string")
        self._sync_sql_table_non_destructive(rename=(old_name, new_name, new_type))

        self._sync_sql_meta_columns()

        primary_changed = False
        if self.schema.get("primary_id_field") == old_name:
            self.schema["primary_id_field"] = new_name
            primary_changed = True
            self._update_sql_meta_primary_id(new_name)

        return {
            "noun_type": self.name,
            "old_field": old_name,
            "new_field": new_name,
            "primary_id_changed": primary_changed,
            "was_adjective": was_adjective,
        }

    # -----------------------------
    # Primary ID configuration
    # -----------------------------
    def configure_primary_id(self, field_name: str, autogenerate: str, segments: Optional[list[dict]] = None):
        if field_name not in self.schema.get("fields", {}):
            raise ValueError(f"Field '{field_name}' not found in fields")
        self.schema["primary_id_field"] = field_name

        if autogenerate == "yes":
            if not segments:
                raise ValueError("Autogeneration is enabled but no segments provided.")
            self.schema["autogenerate_id"] = True
            self.schema["autogenerate_segments"] = segments
        elif autogenerate == "no":
            self.schema["autogenerate_id"] = False
            self.schema["autogenerate_segments"] = []
        elif autogenerate == "keep":
            pass
        else:
            raise ValueError("Invalid autogenerate option. Must be 'yes', 'no', or 'keep'.")

        self._update_sql_meta_primary_id(field_name)
        self._sync_sql_meta_columns()

    # -----------------------------
    # ID preview
    # -----------------------------
    def preview_autogenerated_id(self, existing_ids: set[str] = set()) -> str:
        from core.id_generator import generate_autogenerated_id
        noun_types_path = resolve_path(self.project_path, "noun_schema").parent
        return generate_autogenerated_id(
            noun_type_name=self.name,
            noun_schema=self.schema,
            noun_types_path=noun_types_path,
            existing_ids=existing_ids,
        )

    # -----------------------------
    # Describe
    # -----------------------------
    def describe(self) -> dict:
        return {
            "name": self.name,
            "fields": self.schema.get("fields", {}),
            "primary_id_field": self.schema.get("primary_id_field"),
            "autogenerate_id": self.schema.get("autogenerate_id", False),
            "autogenerate_segments": self.schema.get("autogenerate_segments", []),
        }

    @staticmethod
    def primary_id_field_conflicts_with_existing(noun_name: str, new_schema: dict, existing_schemas: dict) -> bool:
        new_pid = new_schema.get("primary_id_field")
        if not new_pid:
            raise ValueError("Missing primary_id_field in schema")
        for other_noun, schema in existing_schemas.items():
            if other_noun == noun_name:
                continue
            if schema.get("primary_id_field") == new_pid:
                return True
        return False

    # -----------------------------
    # SQL META (SQLite + RDS)
    # -----------------------------
    def _sql_db_path(self) -> Path:
        return resolve_path(self.project_path, "object_sql_db")

    # Meta tables (SQLite)
    def _ensure_meta_tables_sqlite(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_tables (
                noun_name        TEXT PRIMARY KEY,
                table_name       TEXT NOT NULL,
                source_path      TEXT NOT NULL,
                row_count        INTEGER NOT NULL,
                primary_id_field TEXT,
                created_at       TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_columns (
                noun_name   TEXT NOT NULL,
                column_name TEXT NOT NULL,
                column_type TEXT NOT NULL,
                PRIMARY KEY (noun_name, column_name)
            )
        """)

    # Meta upsert (RDS)
    def _upsert_meta_pg(self, conn, project: str, noun: str,
                        table_name: str, primary_field: Optional[str]) -> None:
        source_path = f"nouns/{noun}/items.jsonl"  # legacy compat; will be phased out

        with conn.cursor() as cur:
            # Ensure tables exist — created_at has a default now
            cur.execute("""
                CREATE TABLE IF NOT EXISTS public.meta_tables (
                    project TEXT,
                    noun_name TEXT,
                    table_name TEXT NOT NULL,
                    source_path TEXT NOT NULL DEFAULT '',
                    primary_id_field TEXT,
                    row_count BIGINT DEFAULT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS public.meta_columns (
                    project TEXT,
                    noun_name TEXT,
                    column_name TEXT,
                    column_type TEXT
                );
            """)

            # Normalize legacy schemas safely
            cur.execute("""
                DO $$
                BEGIN
                -- Ensure source_path exists
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='meta_tables' AND column_name='source_path'
                ) THEN
                    ALTER TABLE public.meta_tables
                    ADD COLUMN source_path TEXT NOT NULL DEFAULT '';
                END IF;

                -- Ensure row_count exists and is nullable
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='meta_tables' AND column_name='row_count'
                ) THEN
                    ALTER TABLE public.meta_tables
                    ADD COLUMN row_count BIGINT DEFAULT NULL;
                END IF;
                BEGIN
                    ALTER TABLE public.meta_tables ALTER COLUMN row_count DROP NOT NULL;
                EXCEPTION WHEN others THEN
                    -- ignore if already nullable
                END;

                -- Ensure created_at exists and has default NOW()
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='meta_tables' AND column_name='created_at'
                ) THEN
                    ALTER TABLE public.meta_tables
                    ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT NOW();
                END IF;
                BEGIN
                    ALTER TABLE public.meta_tables ALTER COLUMN created_at SET DEFAULT NOW();
                EXCEPTION WHEN others THEN
                    -- ignore if already has default
                END;
                END$$;
            """)

            # Ensure indexes
            cur.execute("""
                DO $$
                BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE schemaname='public' AND indexname='meta_tables_proj_noun_uq'
                ) THEN
                    CREATE UNIQUE INDEX meta_tables_proj_noun_uq
                    ON public.meta_tables(project, noun_name);
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE schemaname='public' AND indexname='meta_columns_proj_noun_col_uq'
                ) THEN
                    CREATE UNIQUE INDEX meta_columns_proj_noun_col_uq
                    ON public.meta_columns(project, noun_name, column_name);
                END IF;
                END$$;
            """)

            # UPDATE existing entry
            cur.execute("""
                UPDATE public.meta_tables
                SET table_name       = %s,
                    source_path      = %s,
                    primary_id_field = %s,
                    created_at       = NOW()
                WHERE project = %s AND noun_name = %s;
            """, (table_name, source_path, primary_field, project, noun))

            # INSERT new one if missing
            if cur.rowcount == 0:
                cur.execute("""
                    INSERT INTO public.meta_tables
                        (project, noun_name, table_name, source_path, primary_id_field, created_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (project, noun_name) DO UPDATE
                        SET table_name       = EXCLUDED.table_name,
                            source_path      = EXCLUDED.source_path,
                            primary_id_field = EXCLUDED.primary_id_field,
                            created_at       = NOW();
                """, (project, noun, table_name, source_path, primary_field))

    def _sync_sql_meta_columns(self) -> None:
        # Phase 6/R17: the meta_columns catalog is retired — nouns live in the unified
        # `instances` store and no live reader consults meta_*. No-op so noun register/edit
        # stops (re)writing the catalog. (Body below kept for history; unreachable.)
        return
        fields = self.schema.get("fields", {})
        kind, target = _effective_nouns_target(self.project_path)
        project = _project_name(self.project_path)
        noun = self.name

        if kind == "pg" and _PSYCOPG_AVAILABLE:
            with psycopg.connect(target, autocommit=True) as conn:
                with conn.cursor() as cur:
                    # Ensure meta tables & upsert meta_tables row
                    tname = _prefixed(project, _sanitize_table_name(noun))
                    self._upsert_meta_pg(conn, project, noun, tname, self.schema.get("primary_id_field"))

                    # Replace meta_columns rows for this (project, noun)
                    cur.execute("""DELETE FROM meta_columns WHERE project=%s AND noun_name=%s""", (project, noun))
                    if fields:
                        rows = [
                            (project, noun, fname, _pytype_to_pg(fdef.get("type", "string")))
                            for fname, fdef in fields.items()
                        ]
                        cur.executemany("""
                            INSERT INTO meta_columns (project, noun_name, column_name, column_type)
                            VALUES (%s, %s, %s, %s)
                        """, rows)
            return

        # SQLite fallback
        db_path = self._sql_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path.as_posix())
        try:
            self._ensure_meta_tables_sqlite(conn)
            conn.execute("DELETE FROM meta_columns WHERE noun_name = ?", (noun,))
            if fields:
                rows = [(noun, fname, _schema_sql_type(fdef.get("type", "string")))
                        for fname, fdef in fields.items()]
                conn.executemany("""
                    INSERT INTO meta_columns (noun_name, column_name, column_type)
                    VALUES (?, ?, ?)
                """, rows)
            conn.commit()
        finally:
            conn.close()

    def _update_sql_meta_primary_id(self, primary_field: Optional[str]) -> None:
        # Phase 6/R17: meta_tables retired (see _sync_sql_meta_columns). No-op.
        return
        if not primary_field:
            return
        kind, target = _effective_nouns_target(self.project_path)
        project = _project_name(self.project_path)
        noun = self.name

        if kind == "pg" and _PSYCOPG_AVAILABLE:
            with psycopg.connect(target, autocommit=True) as conn:
                tname = _prefixed(project, _sanitize_table_name(noun))
                self._upsert_meta_pg(conn, project, noun, tname, primary_field)
            return

        # SQLite fallback
        db_path = self._sql_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path.as_posix())
        try:
            self._ensure_meta_tables_sqlite(conn)
            table_name = _sanitize_table_name(self.name)
            source_path = f"nouns/{self.name}/items.jsonl"  # legacy field; harmless placeholder
            conn.execute("""
                INSERT INTO meta_tables (noun_name, table_name, source_path, row_count, primary_id_field)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(noun_name) DO UPDATE SET
                  table_name=excluded.table_name,
                  source_path=excluded.source_path,
                  primary_id_field=excluded.primary_id_field,
                  created_at=datetime('now')
            """, (noun, table_name, source_path, 0, primary_field))
            conn.commit()
        finally:
            conn.close()

    # -----------------------------
    # Table ensure / column add / rename
    # -----------------------------
    def _current_columns_sqlite(self, conn: sqlite3.Connection, table_name: str) -> dict:
        cols = {}
        for _cid, name, ctype, _notnull, _dflt, _pk in conn.execute(f'PRAGMA table_info("{table_name}")'):
            cols[name] = ctype or ""
        return cols

    def _ensure_table_sqlite(self, conn: sqlite3.Connection, table_name: str, fields: dict) -> None:
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS "{table_name}" (
                "_rowid" INTEGER PRIMARY KEY AUTOINCREMENT
            )
        ''')
        existing = self._current_columns_sqlite(conn, table_name)
        for fname, spec in fields.items():
            if fname not in existing:
                sqlt = _pytype_to_sqlite(spec.get("type", "string"))
                conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{fname}" {sqlt}')

    def _ensure_primary_index_sqlite(self, conn: sqlite3.Connection, table_name: str, primary_field: str) -> None:
        idx = f'{table_name}__primary_id_idx'
        conn.execute(f'CREATE INDEX IF NOT EXISTS "{idx}" ON "{table_name}"("{primary_field}")')

    def _sqlite_supports_rename_column(self, conn: sqlite3.Connection) -> bool:
        try:
            v = conn.execute("select sqlite_version()").fetchone()[0]
            major, minor, patch = [int(x) for x in v.split(".")]
            return (major, minor, patch) >= (3, 25, 0)
        except Exception:
            return False

    def _sync_sql_table_non_destructive(self, *, rename: Optional[tuple]=None, new_field: Optional[tuple]=None) -> None:
        # Phase 6/R17: per-noun SQL tables are retired — noun instances live in the unified
        # `instances` store (noun_workbench create/update/bulk write there via the record store).
        # No-op so noun register/edit stops creating/altering legacy noun_<X> tables.
        # (Body below kept for history; unreachable.)
        return
        kind, target = _effective_nouns_target(self.project_path)
        project = _project_name(self.project_path)
        table_sqlite = _sanitize_table_name(self.name)
        table_pg = _prefixed(project, table_sqlite)
        fields = self.schema.get("fields", {})
        primary = self.schema.get("primary_id_field")

        if kind == "pg" and _PSYCOPG_AVAILABLE:
            with psycopg.connect(target, autocommit=True) as conn:
                with conn.cursor() as cur:
                    # Create table if missing with _rowid identity PK
                    cur.execute(f'''
                        CREATE TABLE IF NOT EXISTS public."{table_pg}" (
                            "_rowid" BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY
                        )
                    ''')
                    # Add any missing columns
                    cur.execute("""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema='public' AND table_name=%s
                    """, (table_pg,))
                    existing = {r[0] for r in cur.fetchall()}
                    if rename:
                        old_name, new_name, new_type = rename
                        if new_name not in existing:
                            # If old exists, try rename; else just add
                            if old_name in existing:
                                try:
                                    cur.execute(f'ALTER TABLE public."{table_pg}" RENAME COLUMN "{old_name}" TO "{new_name}"')
                                except Exception:
                                    # fallback: add and copy
                                    cur.execute(f'ALTER TABLE public."{table_pg}" ADD COLUMN "{new_name}" {_pytype_to_pg(new_type)}')
                                    cur.execute(f'UPDATE public."{table_pg}" SET "{new_name}" = "{old_name}" WHERE "{new_name}" IS NULL')
                            else:
                                cur.execute(f'ALTER TABLE public."{table_pg}" ADD COLUMN "{new_name}" {_pytype_to_pg(new_type)}')
                        existing.add(new_name)
                    elif new_field:
                        fname, ftype = new_field
                        if fname not in existing:
                            cur.execute(f'ALTER TABLE public."{table_pg}" ADD COLUMN "{fname}" {_pytype_to_pg(ftype)}')
                            existing.add(fname)
                    else:
                        for fname, spec in fields.items():
                            if fname not in existing:
                                cur.execute(f'ALTER TABLE public."{table_pg}" ADD COLUMN "{fname}" {_pytype_to_pg(spec.get("type","string"))}')
                                existing.add(fname)

                    # Non-unique index for primary id field
                    if primary:
                        idx = f'{table_pg}__primary_id_idx'
                        cur.execute("""
                            SELECT indexname FROM pg_indexes
                            WHERE schemaname='public' AND tablename=%s AND indexname=%s
                        """, (table_pg, idx))
                        if cur.fetchone() is None:
                            cur.execute(f'CREATE INDEX "{idx}" ON public."{table_pg}" ("{primary}")')
            # Keep meta in sync with current primary
            if primary:
                self._update_sql_meta_primary_id(primary)
            return

        # SQLite fallback
        db_path = self._sql_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path.as_posix())
        try:
            self._ensure_table_sqlite(conn, table_sqlite, fields)
            if rename:
                old_name, new_name, new_type = rename
                existing = self._current_columns_sqlite(conn, table_sqlite)
                if new_name not in existing:
                    if self._sqlite_supports_rename_column(conn) and old_name in existing:
                        conn.execute(f'ALTER TABLE "{table_sqlite}" RENAME COLUMN "{old_name}" TO "{new_name}"')
                    else:
                        # shadow copy
                        conn.execute(f'ALTER TABLE "{table_sqlite}" ADD COLUMN "{new_name}" {_pytype_to_sqlite(new_type)}')
                        if old_name in existing:
                            conn.execute(f'UPDATE "{table_sqlite}" SET "{new_name}" = "{old_name}" WHERE "{new_name}" IS NULL')
            elif new_field:
                fname, ftype = new_field
                existing = self._current_columns_sqlite(conn, table_sqlite)
                if fname not in existing:
                    conn.execute(f'ALTER TABLE "{table_sqlite}" ADD COLUMN "{fname}" {_pytype_to_sqlite(ftype)}')
            else:
                existing = self._current_columns_sqlite(conn, table_sqlite)
                for fname, spec in fields.items():
                    if fname not in existing:
                        conn.execute(f'ALTER TABLE "{table_sqlite}" ADD COLUMN "{fname}" {_pytype_to_sqlite(spec.get("type","string"))}')
            if primary:
                self._ensure_primary_index_sqlite(conn, table_sqlite, primary)
            conn.commit()
        finally:
            conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Top-level helpers
# ──────────────────────────────────────────────────────────────────────────────
def register_noun_type(
    existing_schemas: dict,
    noun_name: str,
    noun_schema: dict,
    *,
    project_path: Path = Path(),
) -> dict:
    if noun_name in existing_schemas:
        raise ValueError(f"Noun '{noun_name}' already exists.")

    nt = NounType(noun_name, noun_schema, project_path=project_path)
    nt.validate_field_structure()

    # Prime SQL meta with columns & (if present) primary_id
    nt._sync_sql_meta_columns()
    pid = noun_schema.get("primary_id_field")
    if pid:
        nt._update_sql_meta_primary_id(pid)

    updated = existing_schemas.copy()
    updated[noun_name] = noun_schema
    return updated


def validate_noun_schema(noun_name: str, schema: dict, project_path: Path):
    try:
        nt = NounType(noun_name, schema, project_path)
        nt.validate_field_structure()
        return {"success": True}
    except Exception as e:
        return {"success": False, "messages": [str(e)]}
