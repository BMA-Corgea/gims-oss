# api/iostore/schema.py -- split out of api/i_o.py (wiring-neutral). Schema load/lookup.
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional
from api.json_proxy import read_text
from utils.logger import get_logger

log = get_logger(__name__)


def load_schema(project_path: Path, word_type: str):
    """
    Load the full *_types.json schema file. (S3-AWARE)
    word_type: one of 'noun', 'verb', 'adjective', 'adverb'.

    For 'adjective'/'adverb', the on-disk shape (legacy LIST *or* the migrated name-keyed dict) is
    normalized to the per-scope LIST view every consumer here expects (Phase 6/R17 wordtype
    migration) — so callers never branch on shape and demote/CRUD stays scope-safe. noun/verb are
    returned as the raw keyed dict. (S3 read stays here; the reader only normalizes in memory.)
    """
    schema_path = project_path / f"{word_type}_types.json"
    try:
        payload = read_text(schema_path, encoding="utf-8")
        raw = json.loads(payload)
    except Exception as e:
        msg = str(e)
        # Catch S3 "NoSuchKey" or local "FileNotFoundError"
        if "NoSuchKey" in msg or isinstance(e, FileNotFoundError):
            log.debug(f"[load_schema] {schema_path} not found (local or S3).")
            if word_type in ("adjective", "adverb"):
                return []  # descriptor consumers iterate a list; empty is the natural "none"
            raise FileNotFoundError(f"{schema_path} not found.")
        else:
            # A different error (e.g., invalid JSON), let it crash
            log.debug(f"[load_schema] Failed to load/parse schema {schema_path}", {"error": repr(e)})
            raise e
    if word_type in ("adjective", "adverb"):
        from core.words.reader import words_from_raw, _serialize_legacy_list
        return _serialize_legacy_list(word_type, words_from_raw(word_type, raw), per_scope=True)
    return raw

def load_override(project_path: Path) -> list[dict]:
    """
    Load the override.json file (list of override instructions). (S3-AWARE)
    """
    override_path = project_path / "override.json"
    try:
        payload = read_text(override_path, encoding="utf-8")
        return json.loads(payload)
    except FileNotFoundError:
        return []  # Original behavior
    except Exception:
        log.warning("[load_override] failed to read/parse override.json", {"path": str(override_path)}, exc_info=True)
        return []

def get_noun_schema(project_path: Path, noun_name: str) -> Optional[dict]:
    schema = load_schema(project_path, "noun")
    return schema.get(noun_name)

def get_verb_schema(project_path: Path, verb_name: str) -> Optional[dict]:
    schema = load_schema(project_path, "verb")
    return schema.get(verb_name)

def get_adjective_schema(
    project_path: Path,
    adjective_name: str,
    applies_to: Optional[str] = None
) -> Optional[dict]:
    # Route through the back-compat reader (Phase 3): tolerates the LIST shape and the
    # dict shape (which the old list-scan crashed on), and folds duplicate names so a single
    # entry carries every attach target. Returns the same external shape (one entry dict).
    from core.words.reader import read_types
    wt = read_types(project_path, "adjective").get(adjective_name)
    if wt is None:
        return None
    if applies_to and applies_to not in wt.attaches_to:
        return None
    return {**wt.raw, "applies_to": wt.attaches_to}

def get_adverb_schema(
    project_path: Path,
    adverb_name: str,
    applies_to: Optional[str] = None
) -> Optional[dict]:
    # Reader-backed (Phase 3). NOTE: adverbs are scoped by ``verb``; the reader normalizes that
    # into attaches_to, so ``applies_to`` (the verb) now matches correctly (the old code filtered
    # on a non-existent ``applies_to`` key and silently returned None).
    from core.words.reader import read_types
    wt = read_types(project_path, "adverb").get(adverb_name)
    if wt is None:
        return None
    if applies_to and applies_to not in wt.attaches_to:
        return None
    entry = {**wt.raw, "attaches_to": wt.attaches_to}
    if applies_to:
        entry["verb"] = applies_to
    return entry

def get_override_schema(project_path: Path, run_id: str) -> list[dict]:
    overrides = load_override(project_path)
    return [entry for entry in overrides if entry.get("run") == run_id]
