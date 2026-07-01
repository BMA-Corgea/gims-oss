"""One-time consolidation of nouns into the unified SQL ``instances`` table (DRY-RUN by default).

Owner decision (2026-06-24): **nouns live in SQL; the noun-group folders + their items.jsonl ledgers
are retired.** This migrator performs the single move that makes the unified ``instances`` table
(:class:`core.storage.sql.SqlRecordStore`) the authoritative noun store, keyed by
``collection_for_noun(noun_type)`` + the noun's ``primary_id_field``.

``--source`` selects what each record is built from:

* ``merged`` (DEFAULT) — **SQL-authoritative, JSONL-fills-blanks.** The live app reads the per-noun
  SQL table first, so SQL is the base (what users see); any field SQL left empty/missing is filled
  from items.jsonl, and JSONL-only rows are folded in — so retiring JSONL drops NOTHING (e.g. the
  ``Submission.image`` paths SQL lost are recovered). The sqlite ``_rowid`` artifact is dropped;
  ``_runID``/``archived``/``archived_at`` are kept. Lossless by construction, so no interlock.
* ``sql``    — the per-noun SQL table verbatim (exact current display; discards JSONL-only data).
* ``jsonl``  — items.jsonl only (LEGACY/LOSSY: the live app reads SQL first, so this can lose SQL
  fields). Guarded by the divergence interlock — requires ``--allow-divergent``.

The migrator never modifies or deletes the source tables/JSONL; retiring them is a separate, later
cutover step taken only after a per-key verify. Re-runnable (upsert).

Usage::

    python -m tools.migrate_records                                   # dry-run ALL projects (merged)
    python -m tools.migrate_records --project LIMS-System
    python -m tools.migrate_records --apply --db projects/LIMS-System/objects.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.storage import collection_for_noun
from core.storage.sql import SqlRecordStore
from tools.reconcile_records import _jsonl_rows, _primary_fields, _sql_rows
from utils.paths import projects_dir

_DROP_COLUMNS = {"_rowid"}  # sqlite AUTOINCREMENT artifact; _runID/archived/archived_at are real, kept


def _merge_one(sql_row: Optional[dict], jsonl_row: Optional[dict]) -> dict:
    """SQL-authoritative, JSONL-fills-blanks (a non-empty SQL value is never overwritten)."""
    base = {k: v for k, v in (sql_row or {}).items() if k not in _DROP_COLUMNS}
    fill = {k: v for k, v in (jsonl_row or {}).items() if k not in _DROP_COLUMNS}
    out: dict = {}
    for k in set(base) | set(fill):
        sval, jval = base.get(k), fill.get(k)
        if sval not in (None, ""):
            out[k] = sval
        elif jval not in (None, ""):
            out[k] = jval
        else:
            out[k] = sval if k in base else jval
    return out


def collection_records(
    project_path: Path, project_name: str, con: Optional[sqlite3.Connection],
    noun_type: str, key_field: str, source: str,
) -> Tuple[List[dict], dict]:
    """Return (records_to_write, stats) for one collection under the chosen source."""
    jl_all = _jsonl_rows(project_path, noun_type)
    jl_rows = [r for r in jl_all if r.get(key_field) not in (None, "")]
    jl = {str(r.get(key_field)): r for r in jl_rows}
    _table_used, sql_list = (_sql_rows(con, project_name, noun_type) if con is not None else (None, []))
    sql_rows = [r for r in sql_list if r.get(key_field) not in (None, "")]
    sq = {str(r.get(key_field)): r for r in sql_rows}

    if source == "jsonl":
        recs = jl_rows
    elif source == "sql":
        recs = [{k: v for k, v in r.items() if k not in _DROP_COLUMNS} for r in sql_rows]
    else:  # merged
        recs = [_merge_one(sq.get(k), jl.get(k)) for k in sorted(set(sq) | set(jl))]

    keyless = (len(jl_all) - len(jl_rows)) + sum(1 for r in sql_list if r.get(key_field) in (None, ""))
    stats = {
        "sql": len(sq), "jsonl": len(jl), "written": len(recs),
        "recovered_from_jsonl": len(set(jl) - set(sq)),  # JSONL-only rows folded in (merged)
        "keyless": keyless,  # rows with no primary key value — skipped, can't be keyed
    }
    return recs, stats


def _iter_collections(project_path: Path):
    nouns_dir = project_path / "nouns"
    if not nouns_dir.is_dir():
        return
    primaries = _primary_fields(project_path)
    for noun_dir in sorted(p for p in nouns_dir.iterdir() if p.is_dir()):
        yield noun_dir.name, primaries.get(noun_dir.name, "id")


def migrate_project(project_path: Path, store: SqlRecordStore, source: str) -> List[dict]:
    project_name = project_path.name
    db = project_path / "objects.db"
    con = sqlite3.connect(str(db)) if db.exists() else None
    results: List[dict] = []
    try:
        for noun_type, key_field in _iter_collections(project_path):
            recs, stats = collection_records(project_path, project_name, con, noun_type, key_field, source)
            collection = collection_for_noun(noun_type)
            for rec in recs:
                store.put_record(collection, key_field, rec)
            sql_count = store.count(collection)
            results.append({
                "collection": noun_type, **stats, "sql_count": sql_count,
                "verified": sql_count == stats["written"],
            })
    finally:
        if con is not None:
            con.close()
    return results


def plan_project(project_path: Path, source: str) -> List[dict]:
    project_name = project_path.name
    db = project_path / "objects.db"
    con = sqlite3.connect(str(db)) if db.exists() else None
    plan: List[dict] = []
    try:
        for noun_type, key_field in _iter_collections(project_path):
            recs, stats = collection_records(project_path, project_name, con, noun_type, key_field, source)
            plan.append({"collection": noun_type, "key_field": key_field, **stats})
    finally:
        if con is not None:
            con.close()
    return plan


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Consolidate nouns into the unified SQL instances table")
    ap.add_argument("--project", help="single project name (default: all under projects/)")
    ap.add_argument("--source", choices=("merged", "sql", "jsonl"), default="merged",
                    help="merged=SQL+JSONL-fills-blanks (default, lossless); sql=SQL only; "
                         "jsonl=legacy lossy (interlocked)")
    ap.add_argument("--apply", action="store_true", help="actually write into the SQL store")
    ap.add_argument("--db", help="SQLite path for --apply (required with --apply)")
    ap.add_argument("--allow-divergent", action="store_true",
                    help="proceed with --source jsonl despite SQL<->JSONL divergence (acknowledge loss)")
    args = ap.parse_args(argv)

    root = projects_dir()
    targets = [root / args.project] if args.project else [p for p in sorted(root.iterdir()) if p.is_dir()]

    if not args.apply:
        print(f"DRY-RUN (no writes). source={args.source}. Per-collection plan:")
        grand = 0
        for proj in targets:
            plan = plan_project(proj, args.source)
            if not plan:
                continue
            print(f"\n# {proj.name}")
            for row in plan:
                grand += row["written"]
                flags = []
                if row["recovered_from_jsonl"]:
                    flags.append(f"+{row['recovered_from_jsonl']} recovered-from-jsonl")
                if row["keyless"]:
                    flags.append(f"{row['keyless']} keyless skipped")
                extra = (", " + ", ".join(flags)) if flags else ""
                print(f"  {row['collection']}: write {row['written']} "
                      f"(sql={row['sql']}, jsonl={row['jsonl']}{extra}; key={row['key_field']})")
        print(f"\nTOTAL records to write: {grand}. Re-run with --apply --db <path>.")
        return 0

    if not args.db:
        print("ERROR: --apply requires --db <sqlite path>", file=sys.stderr)
        return 2

    # SAFETY INTERLOCK: only the legacy JSONL-only source can silently lose data (the live app reads
    # SQL first). merged/sql are lossless/authoritative, so they bypass the interlock.
    if args.source == "jsonl" and not args.allow_divergent:
        from tools.reconcile_records import reconcile_project
        divergent = []
        for proj in targets:
            rep = reconcile_project(proj)
            for c in rep["collections"]:
                if c["only_in_sql"] or c["differs"]:
                    divergent.append(f"{proj.name}/{c['collection']}: "
                                     f"{len(c['only_in_sql'])} only-in-sql, {len(c['differs'])} differ")
            for o in rep["orphaned_tables"]:
                divergent.append(f"{proj.name}: orphaned table {o['table']} ({o['rows']} rows)")
        if divergent:
            print("ABORT: --source jsonl over SQL<->JSONL divergence would lose data.", file=sys.stderr)
            for d in divergent:
                print(f"  - {d}", file=sys.stderr)
            print("\nUse --source merged (lossless default) or --allow-divergent to force.", file=sys.stderr)
            return 3

    store = SqlRecordStore(Path(args.db))
    all_ok = True
    for proj in targets:
        results = migrate_project(proj, store, args.source)
        if not results:
            continue
        print(f"\n# {proj.name}")
        for row in results:
            ok = "OK" if row["verified"] else "MISMATCH"
            all_ok = all_ok and row["verified"]
            extra = f" (+{row['recovered_from_jsonl']} from jsonl)" if row["recovered_from_jsonl"] else ""
            print(f"  {row['collection']}: wrote {row['written']}{extra} -> instances[{row['collection']}]={row['sql_count']} [{ok}]")
    print(f"\nTotal in instances: {store.count()}. source={args.source}. "
          f"Rowcount-verify: {'ALL OK' if all_ok else 'MISMATCHES PRESENT'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
