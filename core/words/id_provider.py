"""IdProvider seam — the single point where reference validity is resolved.

The editor (JSONL store), the workbench (SQL), and the audit each have a different source of
truth for "which ids exist". By injecting an ``IdProvider`` into the one validation engine,
all three resolve references *identically* instead of carrying their own divergent logic.

* :class:`NullIdProvider`   — resolves nothing (skip existence checks); the safe default.
* :class:`StaticIdProvider` — in-memory map, for tests/fixtures.
* :class:`JsonlIdProvider`  — the editor's store: ``<project>/nouns/<noun>/items.jsonl``.
* :class:`SqlIdProvider`    — the workbench's store; the DB-listing function is *injected* so
  ``core`` never imports ``gui``/db drivers (preserves the layering guard).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Set, Union


class IdProvider:
    """Return the set of valid primary ids for a reference noun (optionally filtered).

    Returning ``None`` means "cannot resolve — skip the existence check" (so validation
    degrades gracefully when no store is available, rather than failing closed on every ref).
    A concrete provider returns a (possibly empty) set; an empty set means "no such id exists",
    which the engine reports as REFERENCE_NOT_FOUND.
    """

    def valid_ids(self, reference_noun: str, filters: Optional[dict] = None,
                  reference_key: Optional[str] = None) -> Optional[Set]:
        raise NotImplementedError


class NullIdProvider(IdProvider):
    """Resolves nothing — reference existence checks are skipped. The safe default."""

    def valid_ids(self, reference_noun: str, filters: Optional[dict] = None,
                  reference_key: Optional[str] = None) -> Optional[Set]:
        return None


class StaticIdProvider(IdProvider):
    """In-memory provider for tests/fixtures: ``{noun_name: {id, ...}}``."""

    def __init__(self, mapping: dict):
        self._m = {k: {str(x) for x in v} for k, v in mapping.items()}

    def valid_ids(self, reference_noun: str, filters: Optional[dict] = None,
                  reference_key: Optional[str] = None) -> Optional[Set]:
        return self._m.get(reference_noun, set())


def _apply_equality_filters(items: list, filters: Optional[dict]) -> list:
    """Case-insensitive equality filter, matching the legacy ``apply_filters_to_items``."""
    if not isinstance(filters, dict) or not filters:
        return items
    out = []
    for it in items:
        if all(str(it.get(k, "")).lower() == str(v).lower() for k, v in filters.items()):
            out.append(it)
    return out


class JsonlIdProvider(IdProvider):
    """The editor's reference source: ``<project>/nouns/<noun>/items.jsonl``.

    Mirrors the legacy ``utils.semantics`` reference logic: read the referenced noun's items,
    apply the adjective's equality filters, then collect the id column. The id column is
    ``reference_key`` if declared, else the referenced noun's ``primary_id_field``, else the
    first key of the first row (the legacy heuristic). Returns a concrete set (never ``None``),
    so a missing/empty store yields "id not found" exactly as the editor does today.
    """

    def __init__(self, project_path: Union[str, Path]):
        self.project_path = Path(project_path)
        self._primary_cache: Dict[str, Optional[str]] = {}

    def _primary_for(self, noun: str) -> Optional[str]:
        if noun in self._primary_cache:
            return self._primary_cache[noun]
        pid = None
        types_file = self.project_path / "noun_types.json"
        try:
            data = json.loads(types_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                pid = (data.get(noun) or {}).get("primary_id_field")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pid = None
        self._primary_cache[noun] = pid
        return pid

    def valid_ids(self, reference_noun: str, filters: Optional[dict] = None,
                  reference_key: Optional[str] = None) -> Optional[Set]:
        items_file = self.project_path / "nouns" / reference_noun / "items.jsonl"
        try:
            entries = [json.loads(ln) for ln in items_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            entries = []
        entries = _apply_equality_filters(entries, filters)
        pid = reference_key or self._primary_for(reference_noun)
        if pid is None:
            pid = next(iter(entries[0].keys())) if entries else None
        if pid is None:
            return set()
        return {str(rec.get(pid, "")) for rec in entries}


class SqlIdProvider(IdProvider):
    """The workbench's reference source. The DB-listing function is injected:
    ``list_ids(reference_noun) -> Iterable[id]`` (e.g. ``noun_workbench_gui._list_ids_for_noun``
    bound to a project). Keeping it a callable means ``core`` does not import ``gui`` or any DB
    driver. Returns a concrete set (an unknown noun lists no ids → "not found", matching the
    workbench's reject-on-unknown behavior)."""

    def __init__(self, list_ids: Callable[[str], Iterable[Any]]):
        self._list_ids = list_ids

    def valid_ids(self, reference_noun: str, filters: Optional[dict] = None,
                  reference_key: Optional[str] = None) -> Optional[Set]:
        try:
            return {str(x) for x in (self._list_ids(reference_noun) or [])}
        except Exception:
            return set()
