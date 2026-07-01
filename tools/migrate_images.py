"""Relocate noun images out of the retired ``nouns/<type>/images/`` tree into the dedicated
``images/<collection>/<filename>`` folder, and rewrite every image reference inside the unified
``instances`` records (DRY-RUN by default; originals are LEFT in place as a rollback copy).

Owner decision (2026-06-24): images get their own folder + nomenclature, referenced from SQL; the
images themselves MUST be preserved. Two passes, so nothing is missed regardless of any SQL/JSONL
reference discrepancy:

1. **File mirror** — copy every file under each ``nouns/<noun>/images/`` to ``images/<noun>/`` (ALL
   on-disk images move, referenced or not; idempotent).
2. **Reference rewrite** — read the populated ``instances`` records (the merged, authoritative refs,
   which include image paths SQL had dropped) and rewrite each ``nouns/<noun>/images/<f>`` value to
   ``images/<noun>/<f>``.

Run AFTER ``tools/migrate_records --apply`` has populated ``instances`` in the same objects.db.

Usage::

    python -m tools.migrate_images                                  # dry-run all projects
    python -m tools.migrate_images --project LIMS-System
    python -m tools.migrate_images --project LIMS-System --apply --db projects/LIMS-System/objects.db
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from core.storage.images import is_legacy_image_ref, relocate_legacy_ref
from core.storage.sql import SqlRecordStore
from tools.reconcile_records import _primary_fields
from utils.paths import projects_dir


def _legacy_image_dirs(project_path: Path):
    nouns = project_path / "nouns"
    if not nouns.is_dir():
        return
    for noun_dir in sorted(p for p in nouns.iterdir() if p.is_dir()):
        img = noun_dir / "images"
        if img.is_dir():
            yield noun_dir.name, img


def plan_project(project_path: Path) -> dict:
    files = 0
    for _noun, img_dir in _legacy_image_dirs(project_path):
        files += sum(1 for p in img_dir.rglob("*") if p.is_file())
    # count refs to rewrite in instances (if populated)
    refs = 0
    db = project_path / "objects.db"
    if db.exists():
        store = SqlRecordStore(db)
        for collection in store.collections():
            for rec in store.list_records(collection):
                refs += sum(1 for v in rec.values() if is_legacy_image_ref(v))
    return {"image_files": files, "refs_to_rewrite": refs}


def apply_project(project_path: Path) -> dict:
    # 1) mirror every on-disk image file into the dedicated images/ folder
    moved = 0
    dest_root = project_path / "images"
    for noun, img_dir in _legacy_image_dirs(project_path):
        for src in img_dir.rglob("*"):
            if not src.is_file():
                continue
            rel = src.relative_to(img_dir)
            dst = dest_root / noun / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            moved += 1

    # 2) rewrite legacy refs inside the unified instances records
    rewritten_refs, rewritten_records, dangling = 0, 0, 0
    db = project_path / "objects.db"
    if db.exists():
        store = SqlRecordStore(db)
        primaries = _primary_fields(project_path)
        for collection in store.collections():
            key_field = primaries.get(collection, "id")
            for rec in store.list_records(collection):
                changed = False
                for field, value in list(rec.items()):
                    if is_legacy_image_ref(value):
                        new_ref = relocate_legacy_ref(value)
                        rec[field] = new_ref
                        rewritten_refs += 1
                        changed = True
                        if not (project_path / new_ref).exists():
                            dangling += 1
                if changed and rec.get(key_field) not in (None, ""):
                    store.put_record(collection, key_field, rec)
                    rewritten_records += 1
    return {"moved_files": moved, "rewritten_refs": rewritten_refs,
            "rewritten_records": rewritten_records, "dangling_after": dangling}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Relocate noun images to the dedicated images/ folder")
    ap.add_argument("--project", help="single project (default: all under projects/)")
    ap.add_argument("--apply", action="store_true", help="copy files + rewrite refs in instances")
    ap.add_argument("--db", help="(unused placeholder; objects.db is resolved per project)")
    args = ap.parse_args(argv)

    root = projects_dir()
    targets = [root / args.project] if args.project else [p for p in sorted(root.iterdir()) if p.is_dir()]

    if not args.apply:
        for proj in targets:
            plan = plan_project(proj)
            if plan["image_files"] or plan["refs_to_rewrite"]:
                print(f"# {proj.name}: mirror {plan['image_files']} image file(s) -> images/, "
                      f"rewrite {plan['refs_to_rewrite']} instances ref(s)")
        print("\nDRY-RUN. Re-run with --apply (after migrate_records --apply).")
        return 0

    for proj in targets:
        res = apply_project(proj)
        if any(res.values()):
            print(f"# {proj.name}: mirrored {res['moved_files']} file(s) -> images/, "
                  f"rewrote {res['rewritten_refs']} ref(s) in {res['rewritten_records']} record(s)"
                  + (f"  [WARN {res['dangling_after']} ref(s) point to a missing file]" if res["dangling_after"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
