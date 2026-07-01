"""Back-compat reader/writer — the single normalize-on-read choke point for ``*_types.json``.

``read_types`` accepts the on-disk shape as it is today (name-keyed **dict** for noun/verb,
**list** for adjective/adverb — and inconsistently dict-or-list across projects) and always
returns a name-keyed ``dict[str, WordType]``. This kills the list-vs-dict branching that every
consumer reinvented (and the ``word_registry.get_all_words`` empty-set bug on list files).

``write_types`` can emit EITHER the new canonical keyed dict (``legacy=False``) or the legacy
list (``legacy=True``, adjective/adverb only) so the on-disk migration can dual-write during the
transition window. Writes are atomic + lock-safe via ``utils.atomic``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Union

from utils.atomic import atomic_write_json, file_lock
from utils.logger import get_logger
from core.words.wordtype import (
    KIND_ADJECTIVE,
    KIND_ADVERB,
    WordType,
)

log = get_logger(__name__)

# kind -> (name key, scope key, scope-is-list, class key) for the list-shaped descriptor files.
_DESCRIPTOR_SHAPE = {
    KIND_ADJECTIVE: ("adjective", "applies_to", True, "adjective_class"),
    KIND_ADVERB: ("adverb", "verb", False, "adverb_class"),
}


def types_path(project_path: Union[str, Path], kind: str) -> Path:
    return Path(project_path) / f"{kind}_types.json"


def _load_raw(path: Path) -> Optional[Union[dict, list]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as e:
        # Don't silently mask a corrupt schema as empty — fail loud through the contract.
        from core.errors import AppError
        raise AppError("SCHEMA_CORRUPT", f"Could not parse {path.name}", status=500,
                       details={"path": str(path), "error": repr(e)})


def _fold_descriptor_list(kind: str, entries: List[dict]) -> Dict[str, WordType]:
    """Fold a list of adjective/adverb entries into a name-keyed dict of WordType.

    Same name with the same class → merge ``attaches_to``. Same name with a *different* class
    → keep a suffixed key (``name#scope``) and log the collision (locked decision: never drop).
    """
    name_key, scope_key, scope_is_list, class_key = _DESCRIPTOR_SHAPE[kind]
    out: Dict[str, WordType] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get(name_key)
        if not name:
            continue
        scope_val = entry.get(scope_key)
        if scope_is_list:
            scopes = [s for s in (scope_val or []) if s]
        else:
            scopes = [scope_val] if scope_val else []
        cls = entry.get(class_key) or entry.get("class")

        existing = out.get(name)
        if existing is None:
            out[name] = WordType.from_descriptor_entry(kind, name, entry, scopes)
            continue
        if existing.descriptor_class == cls:
            merged = sorted(set(existing.attaches_to) | set(scopes))
            existing.relations["attaches_to"] = merged
            existing.raw["attaches_to"] = merged
        else:
            suffix = "-".join(scopes) or "x"
            key = f"{name}#{suffix}"
            log.warning("words.reader: %s name collision with different class -> key %r",
                        kind, key, {"name": name, "classes": [existing.descriptor_class, cls]})
            out[key] = WordType.from_descriptor_entry(kind, name, entry, scopes)
    return out


def words_from_raw(kind: str, raw: Optional[Union[dict, list]]) -> Dict[str, WordType]:
    """Normalize ALREADY-LOADED raw JSON (list OR dict) into ``{name: WordType}``.

    The in-memory half of :func:`read_types`, split out so S3-aware callers (``api.i_o``) can do
    their own provider-aware read (``read_text``) and still share this one normalization — the
    reader's own file read (:func:`_load_raw`) is local-FS only.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {name: WordType.from_dict(kind, name, val) for name, val in raw.items()}
    if isinstance(raw, list):
        if kind in _DESCRIPTOR_SHAPE:
            return _fold_descriptor_list(kind, raw)
        # A list where we expected a keyed dict (noun/verb) — best-effort key by 'name'.
        out: Dict[str, WordType] = {}
        for entry in raw:
            if isinstance(entry, dict) and entry.get("name"):
                out[entry["name"]] = WordType.from_dict(kind, entry["name"], entry)
        return out
    return {}


def read_types(project_path: Union[str, Path], kind: str) -> Dict[str, WordType]:
    """Return ``{name: WordType}`` for ``<project>/<kind>_types.json``, normalizing list OR dict."""
    return words_from_raw(kind, _load_raw(types_path(project_path, kind)))


def _serialize_legacy_list(kind: str, words: Dict[str, WordType], per_scope: bool = False) -> List[dict]:
    """Expand WordTypes back to the legacy list shape (adjective/adverb).

    Default: one entry per name (adjective ``applies_to`` carries all scopes; adverb already one
    entry per ``verb``). ``per_scope=True``: ALWAYS one entry per scope — adjective ``applies_to``
    becomes a single-element list. This matches the pre-migration on-disk shape the GUI CRUD
    expects, so demote (filter by ``noun in applies_to``) removes only the demoted scope and never
    orphans a folded sibling.
    """
    name_key, scope_key, scope_is_list, _ = _DESCRIPTOR_SHAPE[kind]
    entries: List[dict] = []
    for wt in words.values():
        base = {k: v for k, v in wt.raw.items() if k not in ("class", "attaches_to")}
        base[name_key] = wt.name
        if scope_is_list and not per_scope:
            base[scope_key] = list(wt.attaches_to)
            entries.append(base)
        else:
            scopes = wt.attaches_to or [base.get(scope_key)]
            for sc in scopes:
                e = dict(base)
                e[scope_key] = [sc] if scope_is_list else sc
                entries.append(e)
    return entries


def serialize(kind: str, words: Dict[str, WordType], legacy: bool = False) -> Union[dict, list]:
    """Canonical keyed dict (default) or the legacy list (adjective/adverb, ``legacy=True``)."""
    if legacy and kind in _DESCRIPTOR_SHAPE:
        return _serialize_legacy_list(kind, words)
    return {name: wt.to_dict() for name, wt in words.items()}


def write_types(project_path: Union[str, Path], kind: str, words: Dict[str, WordType],
                legacy: bool = False) -> None:
    """Atomically persist ``words`` to ``<project>/<kind>_types.json``."""
    path = types_path(project_path, kind)
    with file_lock(path):
        atomic_write_json(path, serialize(kind, words, legacy=legacy))


def load_descriptor_list(project_path: Union[str, Path], kind: str, per_scope: bool = False) -> List[dict]:
    """Return adjective/adverb entries as the legacy LIST shape, from EITHER on-disk shape.

    Read-side migration shim: direct list-consumers (the CLI tools, ``load_adjective_handler``,
    ``disambiguation.load_schema``) keep iterating a list of entry dicts whether the file is still
    a list or has been migrated to a name-keyed dict. Built by normalizing through
    :func:`read_types` then re-expanding via :func:`_serialize_legacy_list`, so it round-trips
    today's list and survives the keyed-dict migration without each consumer branching on shape.
    ``per_scope=True`` yields one entry per scope (see :func:`_serialize_legacy_list`).
    """
    if kind not in _DESCRIPTOR_SHAPE:
        raise ValueError(f"load_descriptor_list is for descriptor kinds {tuple(_DESCRIPTOR_SHAPE)}, got {kind!r}")
    return _serialize_legacy_list(kind, read_types(project_path, kind), per_scope=per_scope)


def keyed_from_descriptor_list(kind: str, entries: List[dict]) -> dict:
    """Fold a legacy adjective/adverb LIST (any per-scope/folded mix) into the canonical
    name-keyed dict — the write-side counterpart of :func:`load_descriptor_list`, so a GUI that
    edits the list shape persists the migrated keyed-dict shape."""
    if kind not in _DESCRIPTOR_SHAPE:
        raise ValueError(f"keyed_from_descriptor_list is for descriptor kinds {tuple(_DESCRIPTOR_SHAPE)}, got {kind!r}")
    return serialize(kind, words_from_raw(kind, list(entries or [])), legacy=False)
