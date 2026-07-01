#!/usr/bin/env python3
"""
build_noun_sql.py

Scan projects/LIMS-System/nouns/*/items.jsonl and build a single SQLite DB:
  projects/LIMS-System/nouns/sql/nouns.db

- One table per noun directory: noun_<NOUNNAME> (sanitized).
- Schema is inferred from union of keys across all lines.
- Types: INTEGER, REAL, TEXT (arrays/objects stored as JSON TEXT), NULL handled.
- Indexes:
    - _runID (if present)
    - <primary_id_field> from noun schema (non-unique)
    - composite (<primary_id_field>, _runID) if both present
- Meta tables record provenance, schema, and the primary_id_field (from schema).

Usage:
  python3 tools/build_noun_sql.py
"""

from __future__ import annotations
import json, sys
import re
import sqlite3
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]  # one level up from tools/
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from typing import Any, Dict, Iterable, List, Optional, Tuple

# --- Project paths ---
PROJECT_PATH = Path("projects") / "LIMS-System"          # repo/projects/LIMS-System
NOUNS_DIR    = PROJECT_PATH / "nouns"                     # repo/projects/LIMS-System/nouns
SQL_DIR      = NOUNS_DIR / "sql"                          # repo/projects/LIMS-System/nouns/sql
DEFAULT_DB_PATH = SQL_DIR / "nouns.db"

# Import your canonical loader (no guessing)
# Repo structure assumption: api/i_o.py at repo root level "api/i_o.py"
from api.i_o import load_schema  # type: ignore


# ---------- Utilities ----------

def dbg(*a: Any) -> None:
    print("[build_noun_sql]", *a)

def ensure_sql_dir(sql_dir: Path) -> None:
    sql_dir.mkdir(parents=True, exist_ok=True)

def find_items_files(base: Path) -> List[Tuple[str, Path]]:
    """
    Return list of (noun_name, items_path) for each nouns/<noun>/items.jsonl
    Skips 'sql' folder and non-files.
    """
    pairs: List[Tuple[str, Path]] = []
    if not base.exists():
        return pairs
    for sub in sorted(base.iterdir()):
        if not sub.is_dir():
            continue
        if sub.name.lower() == "sql":
            continue
        items = sub / "items.jsonl"
        if items.exists() and items.is_file():
            pairs.append((sub.name, items))
    return pairs

def sanitize_table_name(noun: str) -> str:
    # Keep alnum + underscore; prefix with noun_ and ensure starts with a letter
    base = re.sub(r"[^0-9a-zA-Z_]", "_", noun).strip("_")
    if not base or not base[0].isalpha():
        base = f"T_{base}"
    return f"noun_{base}"

def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError as e:
                dbg(f"WARNING: {path} line {line_no}: JSON decode error: {e}")
                continue
            if isinstance(obj, dict):
                yield obj
            else:
                yield {"_value": obj}


# ---------- Type inference (mechanical, no guessing of semantics) ----------

def infer_sql_type(value: Any) -> str:
    """
    Map Python value to SQLite type affinity.
    Arrays/objects -> TEXT (JSON).
    Booleans -> INTEGER (0/1).
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "INTEGER"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    if isinstance(value, (list, dict)):
        return "TEXT"
    return "TEXT"

def merge_type(a: str, b: str) -> str:
    """
    Merge two SQLite types; if conflict, fall back to TEXT.
    NULL merges to the other side.
    INTEGER+REAL -> REAL; otherwise different -> TEXT.
    """
    if a == b:
        return a
    if a == "NULL":
        return b
    if b == "NULL":
        return a
    if {"INTEGER", "REAL"} == {a, b}:
        return "REAL"
    return "TEXT"

def analyze_schema(rows: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """
    Consume rows (dicts), return (rows_list, column_types)
    column_types: key -> SQLite type
    """
    rows_list: List[Dict[str, Any]] = []
    col_types: Dict[str, str] = {}
    for row in rows:
        rows_list.append(row)
        for k, v in row.items():
            t = infer_sql_type(v)
            if k in col_types:
                col_types[k] = merge_type(col_types[k], t)
            else:
                col_types[k] = t
    return rows_list, col_types

def coerce_for_sql(value: Any, col_type: str) -> Any:
    """
    Coerce Python value to fit col_type where possible.
    - INTEGER: bool -> int; str numbers -> int if possible
    - REAL: str numbers -> float if possible
    - TEXT: lists/dicts -> json.dumps
    """
    if value is None:
        return None
    if col_type == "INTEGER":
        if isinstance(value, bool):
            return 1 if value else 0
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                try:
                    return int(float(value))
                except ValueError:
                    return value
        return value
    if col_type == "REAL":
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return value
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        return value
    if col_type == "TEXT":
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return value if isinstance(value, str) else str(value)
    return value


# ---------- SQL DDL/DML ----------

def create_meta(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta_tables (
            noun_name        TEXT PRIMARY KEY,
            table_name       TEXT NOT NULL,
            source_path      TEXT NOT NULL,
            row_count        INTEGER NOT NULL,
            primary_id_field TEXT,            -- from noun schema
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

def create_table_rebuild(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    cur = conn.cursor()
    cur.execute(f'DROP TABLE IF EXISTS "{table}"')
    # deterministic ordering: _runID first (if present), then alpha
    specials = [k for k in ("_runID",) if k in columns]
    others = sorted([k for k in columns.keys() if k not in specials])
    cols_sql = ['"_rowid" INTEGER PRIMARY KEY AUTOINCREMENT'] + [f'"{k}" {columns[k]}' for k in specials + others]
    cur.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (\n  ' + ",\n  ".join(cols_sql) + "\n)")
    cur.close()

def create_indexes(conn: sqlite3.Connection, table: str, primary_field: Optional[str], columns: Dict[str, str]) -> None:
    cur = conn.cursor()
    if "_runID" in columns:
        cur.execute(f'CREATE INDEX IF NOT EXISTS "{table}__runID_idx" ON "{table}"("_runID")')
    if primary_field:
        if primary_field not in columns:
            dbg(f"  WARNING: schema primary_id_field '{primary_field}' not present in data columns; skipping index.")
        else:
            cur.execute(f'CREATE INDEX IF NOT EXISTS "{table}__primary_idx" ON "{table}"("{primary_field}")')
            if "_runID" in columns:
                cur.execute(
                    f'CREATE INDEX IF NOT EXISTS "{table}__primary_run_idx" ON "{table}"("{primary_field}","_runID")'
                )
    cur.close()

def insert_rows(conn: sqlite3.Connection, table: str, columns: Dict[str, str], rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    keys_order = list(columns.keys())
    placeholders = ", ".join(["?"] * len(keys_order))
    cols_sql = ", ".join([f'"{k}"' for k in keys_order])
    sql = f'INSERT INTO "{table}" ({cols_sql}) VALUES ({placeholders})'
    cur = conn.cursor()
    n = 0
    for row in rows:
        vals = [coerce_for_sql(row.get(k), columns[k]) for k in keys_order]
        cur.execute(sql, vals)
        n += 1
    cur.close()
    return n

def upsert_meta(conn: sqlite3.Connection, noun_name: str, table_name: str, source: Path,
                row_count: int, columns: Dict[str, str], primary_field: Optional[str]) -> None:
    conn.execute("""
        INSERT INTO meta_tables (noun_name, table_name, source_path, row_count, primary_id_field)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(noun_name) DO UPDATE SET
          table_name=excluded.table_name,
          source_path=excluded.source_path,
          row_count=excluded.row_count,
          primary_id_field=excluded.primary_id_field,
          created_at=datetime('now')
    """, (noun_name, table_name, str(source), row_count, primary_field))
    conn.execute("DELETE FROM meta_columns WHERE noun_name = ?", (noun_name,))
    conn.executemany("""
        INSERT INTO meta_columns (noun_name, column_name, column_type)
        VALUES (?, ?, ?)
    """, [(noun_name, k, v) for k, v in columns.items()])


# ---------- Main per-noun flow ----------

def process_one(conn: sqlite3.Connection, noun: str, items_path: Path, noun_schema_map: Dict[str, dict]) -> None:
    dbg(f"Processing {noun} -> {items_path}")

    # strict, schema-driven primary id retrieval (no heuristics)
    primary_field: Optional[str] = None
    if noun not in noun_schema_map:
        dbg(f"  WARNING: noun '{noun}' not found in noun_types.json; proceeding without primary index.")
    else:
        schema_entry = noun_schema_map[noun]
        primary_field = schema_entry.get("primary_id_field")
        if not isinstance(primary_field, str) or not primary_field.strip():
            dbg(f"  WARNING: noun '{noun}' has no valid 'primary_id_field' in schema; proceeding without primary index.")
            primary_field = None

    rows_iter = iter_jsonl(items_path)
    rows, col_types = analyze_schema(rows_iter)

    if not rows:
        dbg("  (no rows) — skipping table creation")
        return

    table = sanitize_table_name(noun)
    create_table_rebuild(conn, table, col_types)
    inserted = insert_rows(conn, table, col_types, rows)
    create_indexes(conn, table, primary_field, col_types)
    upsert_meta(conn, noun, table, items_path, inserted, col_types, primary_field)
    dbg(f"  -> {inserted} rows into {table} (primary_id_field: {primary_field if primary_field else 'none'})")


# ---------- Entry point ----------

def main() -> None:
    ensure_sql_dir(SQL_DIR)

    # Load canonical noun schema (authoritative)
    noun_schema_map: Dict[str, dict] = load_schema(PROJECT_PATH, "noun")
    if not isinstance(noun_schema_map, dict):
        raise TypeError("load_schema() did not return a dict for noun types.")

    items = find_items_files(NOUNS_DIR)
    if not items:
        dbg(f"No items.jsonl files found under {NOUNS_DIR}")
        return

    db_path = DEFAULT_DB_PATH
    dbg(f"DB: {db_path} | nouns: {len(items)}")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        create_meta(conn)

        for noun, path in items:
            process_one(conn, noun, path, noun_schema_map)
            conn.commit()

        dbg("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
