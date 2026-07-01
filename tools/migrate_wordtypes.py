"""One-time, idempotent migrator: fold list-shaped ``*_types.json`` into the canonical
name-keyed dict shape (Phase 3 Step 7).

Safe by design:
- **Dry-run by default** (pass ``--apply`` to write); prints exactly what would change.
- **Idempotent**: already-keyed-dict files are left byte-stable (re-running is a no-op).
- **Shape-aware**: only list-shaped adjective/adverb files are folded; noun/verb dicts pass through.
- **Logs collisions/landmines** surfaced by the reader (same name + different class → suffix key).
- **Skips build artifacts** (``dist/``, ``gims-electron/``) — those regenerate from source.

DO NOT ``--apply`` until every direct list-consumer (handlers, gui loaders) reads through
``core.words.reader`` or otherwise tolerates the keyed-dict shape. The reader-backed
``api/i_o`` getters and ``WordRegistry`` already do; remaining direct ``json.load`` consumers
do not (see handoff.md, Phase 3 remaining).

Usage:
    .venv/bin/python -m tools.migrate_wordtypes            # dry-run over projects/*
    .venv/bin/python -m tools.migrate_wordtypes --apply    # write the canonical shape
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.words.reader import read_types, serialize, types_path
from utils.paths import projects_dir

KINDS = ["noun", "verb", "adjective", "adverb"]


def _plan_project(project_path: Path, apply: bool) -> list[str]:
    changes: list[str] = []
    for kind in KINDS:
        path = types_path(project_path, kind)
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8")
        try:
            on_disk = json.loads(raw)
        except json.JSONDecodeError:
            changes.append(f"  SKIP  {path.name}: not valid JSON")
            continue
        words = read_types(project_path, kind)            # normalized
        canonical = serialize(kind, words, legacy=False)  # keyed dict
        already = isinstance(on_disk, dict) and on_disk == canonical
        if already:
            continue
        shape = "list" if isinstance(on_disk, list) else "dict"
        n_in = len(on_disk) if isinstance(on_disk, (list, dict)) else 0
        changes.append(f"  {kind}_types.json: {shape}({n_in}) -> keyed dict({len(canonical)})")
        if apply:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(canonical, indent=2), encoding="utf-8")
            tmp.replace(path)
    return changes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--project", help="limit to one project name")
    args = ap.parse_args(argv)

    root = projects_dir()
    projects = ([root / args.project] if args.project
                else [p for p in sorted(root.iterdir()) if p.is_dir()])
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[migrate_wordtypes] {mode} over {root}")
    total = 0
    for proj in projects:
        changes = _plan_project(proj, args.apply)
        if changes:
            print(f"{proj.name}:")
            for c in changes:
                print(c)
            total += len(changes)
    if total == 0:
        print("Nothing to migrate (all files already canonical).")
    elif not args.apply:
        print(f"\n{total} file(s) would change. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
