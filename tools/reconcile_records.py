"""Per-key reconciliation of the live SQL read path vs items.jsonl — the mandatory pre-migration
data-loss gate (READ-ONLY; writes nothing).

The live reader ``api.i_o.get_noun_items`` reads the per-noun SQL table FIRST (``noun_<sanitized>``
in ``projects/<p>/objects.db``) and only falls back to ``nouns/<type>/items.jsonl``. But
``tools/migrate_records`` reads items.jsonl ONLY. So before any ``--apply`` we must prove, per
collection and PER KEY (not just by rowcount), that migrating JSONL would not drop a row the app
currently shows. This tool reports three failure categories:

* ``only_in_sql``       — keys present in the per-noun SQL table but NOT in items.jsonl. These are
  the rows the live app shows that a JSONL-only migration would SILENTLY LOSE. (Hard fail.)
* ``orphaned_tables``   — ``noun_*`` tables with rows that NO collection's reader sanitizer can
  reach (the digit-leading ``noun_T_<digits>`` vs ``noun_<digits>`` split-brain). Data on disk that
  neither the live reader nor the migrator surfaces. (Hard fail.)
* ``differs``           — keys in BOTH but whose non-internal fields disagree (informational).

Exit code is non-zero if any ``only_in_sql`` key or any orphaned table with rows exists, so this
can be wired as a CI/pre-cutover gate. ``only_in_jsonl`` is reported but is NOT a loss (the migrator
captures it); it flags a behavior change (a row reappears that the live SQL read currently hides).

Usage::

    python -m tools.reconcile_records                      # all projects
    python -m tools.reconcile_records --project LIMS-System
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.paths import projects_dir


def _primary_fields(project_path: Path) -> Dict[str, str]:
    try:
        data = json.loads((project_path / "noun_types.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {name: (schema or {}).get("primary_id_field") or "id" for name, schema in data.items()}


def _reader_table_names(project_name: str, noun_type: str) -> Tuple[str, str]:
    """EXACTLY mirror api.i_o.get_noun_items: (full_table, base_table)."""
    sanitized = re.sub(r"\W+", "_", str(noun_type)).strip("_")
    base_table = f"noun_{sanitized}"
    full_table = f"{project_name.replace('_', '-')}_{base_table}"
    return full_table, base_table


def _jsonl_rows(project_path: Path, noun_type: str) -> List[dict]:
    p = project_path / "nouns" / noun_type / "items.jsonl"
    try:
        text = p.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _sql_rows(con: sqlite3.Connection, project_name: str, noun_type: str) -> Tuple[Optional[str], List[dict]]:
    """Read the reachable per-noun table the live reader would use; return (table_used, rows)."""
    full_table, base_table = _reader_table_names(project_name, noun_type)
    con.row_factory = sqlite3.Row
    for table in (full_table, base_table):
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists:
            rows = con.execute(f'SELECT * FROM "{table}"').fetchall()
            return table, [dict(r) for r in rows]
    return None, []


def _key_index(rows: List[dict], key_field: str) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for r in rows:
        k = r.get(key_field)
        if k in (None, ""):
            continue
        out[str(k)] = r
    return out


def _payload_differs(a: dict, b: dict) -> bool:
    """Compare user fields only (ignore SQL-internal columns like _rowid/_runID and the dual-key
    space/underscore spellings) — coarse, informational."""
    def norm(d: dict) -> dict:
        return {k: d[k] for k in d if not k.startswith("_")}
    return norm(a) != norm(b)


def reconcile_project(project_path: Path) -> dict:
    nouns_dir = project_path / "nouns"
    project_name = project_path.name
    db_path = project_path / "objects.db"
    primaries = _primary_fields(project_path)

    collections: List[dict] = []
    reached_tables: set[str] = set()
    con = sqlite3.connect(str(db_path)) if db_path.exists() else None
    try:
        if nouns_dir.is_dir():
            for noun_dir in sorted(p for p in nouns_dir.iterdir() if p.is_dir()):
                noun_type = noun_dir.name
                key_field = primaries.get(noun_type, "id")
                jsonl = _key_index(_jsonl_rows(project_path, noun_type), key_field)
                table_used, sql_list = (None, [])
                if con is not None:
                    table_used, sql_list = _sql_rows(con, project_name, noun_type)
                    if table_used:
                        reached_tables.add(table_used)
                sql = _key_index(sql_list, key_field)
                only_sql = sorted(set(sql) - set(jsonl))
                only_jsonl = sorted(set(jsonl) - set(sql))
                differs = sorted(k for k in (set(sql) & set(jsonl)) if _payload_differs(sql[k], jsonl[k]))
                collections.append({
                    "collection": noun_type, "key_field": key_field, "table_used": table_used,
                    "sql_count": len(sql), "jsonl_count": len(jsonl),
                    "only_in_sql": only_sql, "only_in_jsonl": only_jsonl, "differs": differs,
                })

        # Orphaned per-noun tables: noun_* with rows that no collection reader reached.
        orphaned: List[dict] = []
        if con is not None:
            all_noun_tables = [
                r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'noun_%' "
                    "OR name LIKE '%-noun_%' ORDER BY name"
                ).fetchall()
            ]
            for t in all_noun_tables:
                if t in reached_tables:
                    continue
                n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                if n > 0:
                    orphaned.append({"table": t, "rows": n})
    finally:
        if con is not None:
            con.close()

    return {"project": project_name, "collections": collections, "orphaned_tables": orphaned}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Per-key SQL<->JSONL reconciliation (read-only gate)")
    ap.add_argument("--project", help="single project (default: all under projects/)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args(argv)

    root = projects_dir()
    targets = [root / args.project] if args.project else [p for p in sorted(root.iterdir()) if p.is_dir()]
    reports = [reconcile_project(p) for p in targets]

    if args.json:
        print(json.dumps(reports, indent=2, default=str))

    hard_fail = False
    for rep in reports:
        printable = [c for c in rep["collections"] if c["sql_count"] or c["jsonl_count"]]
        if not printable and not rep["orphaned_tables"]:
            continue
        print(f"\n# {rep['project']}")
        for c in printable:
            flags = []
            if c["only_in_sql"]:
                flags.append(f"!! {len(c['only_in_sql'])} ONLY-IN-SQL (lost if JSONL-migrated): {c['only_in_sql']}")
                hard_fail = True
            if c["only_in_jsonl"]:
                flags.append(f"+{len(c['only_in_jsonl'])} only-in-jsonl (reappears): {c['only_in_jsonl']}")
            if c["differs"]:
                flags.append(f"~{len(c['differs'])} differ: {c['differs']}")
            tag = f"  [{'; '.join(flags)}]" if flags else "  [in sync]"
            print(f"  {c['collection']}: sql={c['sql_count']} jsonl={c['jsonl_count']} "
                  f"(key={c['key_field']}, table={c['table_used']}){tag}")
        for o in rep["orphaned_tables"]:
            print(f"  !! ORPHANED TABLE {o['table']}: {o['rows']} rows unreachable by the live reader")
            hard_fail = True

    print(f"\nRECONCILE VERDICT: {'HARD FAIL — data would be lost by a JSONL-only migration' if hard_fail else 'SAFE — JSONL is a superset of every reachable SQL table; no orphaned rows'}")
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
