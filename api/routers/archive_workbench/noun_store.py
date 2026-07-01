"""Instances-backed noun archive/restore primitives (split verbatim from archive_workbench.py).

Nouns live in the unified `instances` record store (objects.db hot / archive.db
hard). These helpers take the primary-id field as a parameter, so they do not
depend on the schema-loading seam and can live outside the package __init__.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path

from core.storage.factory import get_record_store, get_archive_record_store, collection_for_noun


_ARCHIVED_TRUE = (1, "1", True, "true", "True", "yes", "Yes")


def _rec_is_archived(rec: dict) -> bool:
    return isinstance(rec, dict) and rec.get("archived") in _ARCHIVED_TRUE

def _rec_run_id(rec: dict):
    v = rec.get("_run_ID")
    if v in (None, ""):
        v = rec.get("_runID")
    return v

def _archived_noun_ids(project_path: Path, noun_type: str, pf: str, strategy: str, limit: int = 5000):
    """Return (ids, count) of currently-archived records for a noun. soft=hot records flagged
    archived; hard=records in the archive store."""
    coll = collection_for_noun(noun_type)
    if strategy == "soft":
        recs = [r for r in get_record_store(project_path).list_records(coll) if _rec_is_archived(r)]
        recs.sort(key=lambda r: (str(r.get("archived_at") or ""), str(r.get(pf) or "")))
    else:
        recs = list(get_archive_record_store(project_path).list_records(coll))
        recs.sort(key=lambda r: str(r.get(pf) or ""))
    count = len(recs)
    ids = [str(r.get(pf)) for r in recs[:limit] if r.get(pf) is not None]
    return ids, count

def _archive_noun_ids(project_path: Path, noun_type: str, pf: str, ids, strategy: str) -> int:
    """soft: flag archived in hot. hard: move hot->archive store. Returns affected count."""
    coll = collection_for_noun(noun_type)
    hot = get_record_store(project_path)
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    n = 0
    if strategy == "soft":
        for pid in ids:
            rec = hot.get_record(coll, pf, pid)
            if rec is None:
                continue
            rec["archived"] = 1
            rec["archived_at"] = now
            hot.put_record(coll, pf, rec)
            n += 1
    else:
        arc = get_archive_record_store(project_path)
        for pid in ids:
            rec = hot.get_record(coll, pf, pid)
            if rec is None:
                continue
            rec2 = dict(rec)
            rec2["__archived_from"] = "instances"
            rec2["__archived_at"] = now
            rec2["__archive_strategy"] = "hard"
            arc.put_record(coll, pf, rec2)
            hot.delete_record(coll, pf, pid)
            n += 1
    return n

def _restore_noun_ids(project_path: Path, noun_type: str, pf: str, ids, strategy: str) -> int:
    """soft: clear archived flag in hot. hard: move archive->hot store. Returns affected count."""
    coll = collection_for_noun(noun_type)
    hot = get_record_store(project_path)
    n = 0
    if strategy == "soft":
        for pid in ids:
            rec = hot.get_record(coll, pf, pid)
            if rec is None:
                continue
            rec["archived"] = 0
            rec.pop("archived_at", None)
            hot.put_record(coll, pf, rec)
            n += 1
    else:
        arc = get_archive_record_store(project_path)
        for pid in ids:
            rec = arc.get_record(coll, pf, pid)
            if rec is None:
                continue
            rec2 = {k: v for k, v in rec.items() if k not in ("__archived_from", "__archived_at", "__archive_strategy")}
            hot.put_record(coll, pf, rec2)
            arc.delete_record(coll, pf, pid)
            n += 1
    return n
