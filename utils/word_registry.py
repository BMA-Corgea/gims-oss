"""WordRegistry — the single lifecycle owner for part-of-speech type definitions.

Reader-backed (normalizes list-or-dict via ``core.words.reader``), atomic writes
(``utils.atomic`` through the reader), real structured ``get_dependents`` and real
``rename_references`` (was a ``pass`` stub), and ``AppError`` on disentanglement.

Replaces the old loader that: read three phantom files (``prepositional_phrases.json``,
``validation_config.json``, ``adverb_layers.json``) that exist in no real project; returned an
**empty set** for list-shaped adjective/adverb files (so adjectives were invisible) and
**crashed** on dict-shaped ones; resolved the project dir relative to CWD; and had a no-op
``rename_references`` that silently orphaned every reference on a rename.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from core.words.reader import read_types, write_types
from core.words.wordtype import WordType
from utils.logger import get_logger
from utils.paths import projects_dir

log = get_logger(__name__)

_NOUN_REL_KEYS = ("acts_on", "creates", "produces")


class WordRegistry:
    def __init__(self, project_name: str, project_path: Optional[Path] = None):
        self.project = project_name
        self.project_path = Path(project_path) if project_path else (projects_dir() / project_name)
        self._cache: Dict[str, Dict[str, WordType]] = {}

    # ---- loading (normalized, cached, reload-able) ----
    def _types(self, kind: str) -> Dict[str, WordType]:
        if kind not in self._cache:
            self._cache[kind] = read_types(self.project_path, kind)
        return self._cache[kind]

    def reload(self) -> None:
        self._cache.clear()

    def get_all_words(self, word_type: str) -> set:
        return set(self._types(word_type).keys())

    # ---- dependency analysis over the REAL stores (no phantom files) ----
    def get_dependents(self, word_type: str, key: str) -> List[dict]:
        """Structured records of every reference that breaks if ``key`` is deleted/renamed.

        Each: ``{file, key, field_path, kind, label}`` (``label`` is the old human string, kept
        for back-compat). ``rename_references`` reuses these records.
        """
        deps: List[dict] = []

        def add(file, dkey, field_path, kind, label):
            deps.append({"file": file, "key": dkey, "field_path": field_path, "kind": kind, "label": label})

        if word_type == "noun":
            for vname, vt in self._types("verb").items():
                for relk in _NOUN_REL_KEYS:
                    if key in (vt.raw.get(relk) or []):
                        add("verb_types", vname, relk, "verb", f"verb_types: {vname}")
            for aname, at in self._types("adjective").items():
                if key in at.attaches_to:
                    add("adjective_types", aname, "attaches_to", "adjective", f"adjective_types: {aname}")
            for kind in ("adjective", "adverb"):
                for nm, wt in self._types(kind).items():
                    if wt.raw.get("reference_noun") == key or key in (wt.raw.get("reference_nouns") or []):
                        add(f"{kind}_types", nm, "reference_noun", kind, f"{kind}_types: {nm}")
            for nname, nt in self._types("noun").items():
                for fn, fr in nt.fields.items():
                    if fr.reference_noun == key:
                        add("noun_types", nname, f"fields.{fn}", "noun", f"noun_types: {nname}.{fn}")

        elif word_type == "verb":
            for aname, at in self._types("adverb").items():
                if key in at.attaches_to:
                    add("adverb_types", aname, "attaches_to", "adverb", f"adverb_types: {aname}")
            for aname, at in self._types("adjective").items():
                ro = at.raw.get("request_options") or {}
                if isinstance(ro, dict):
                    for val, req in ro.items():
                        if isinstance(req, dict) and key in (req.get("monitored_verbs") or []):
                            add("adjective_types", aname, f"request_options.{val}.monitored_verbs",
                                "adjective", f"adjective_types: {aname} → {val}")

        elif word_type == "adjective":
            for nname, nt in self._types("noun").items():
                for fn, fr in nt.fields.items():
                    if fn == key and (fr.adjective_class or fr.type == "adjective"):
                        add("noun_types", nname, f"fields.{fn}", "noun", f"noun_types: {nname}.{fn}")

        elif word_type == "adverb":
            for vname, vt in self._types("verb").items():
                if key in (vt.raw.get("adverb_schema") or {}):
                    add("verb_types", vname, f"adverb_schema.{key}", "verb", f"verb_types: {vname}")

        return deps

    def is_monitored(self, word_type: str, key: str) -> bool:
        return bool(self.get_dependents(word_type, key))

    def enforce_disentanglement(self, word_type: str, key: str) -> None:
        deps = self.get_dependents(word_type, key)
        if deps:
            from core.errors import AppError
            raise AppError(
                "WORD_IN_USE", f"{word_type} '{key}' is in use", status=409,
                details={"kind": word_type, "key": key, "dependents": [d["label"] for d in deps]},
            )

    # ---- real rename: rewrite every reference, then rename the entry key, atomically ----
    def rename_references(self, word_type: str, old_key: str, new_key: str) -> List[dict]:
        """Rewrite all references ``old_key`` → ``new_key`` across stores and rename the entry.

        Returns the dependents that were rewritten. Was a no-op stub that orphaned references.
        """
        if old_key == new_key:
            return []
        deps = self.get_dependents(word_type, old_key)
        dirty_kinds = set()

        def _rewrite_list(lst):
            return [new_key if x == old_key else x for x in lst]

        for dep in deps:
            kind, dkey, fp = dep["kind"], dep["key"], dep["field_path"]
            wt = self._types(kind).get(dkey)
            if wt is None:
                continue
            raw = wt.raw
            if fp in _NOUN_REL_KEYS:
                raw[fp] = _rewrite_list(raw.get(fp) or [])
            elif fp == "attaches_to":
                wt.relations["attaches_to"] = _rewrite_list(wt.attaches_to)
                raw["attaches_to"] = list(wt.relations["attaches_to"])
            elif fp == "reference_noun":
                if raw.get("reference_noun") == old_key:
                    raw["reference_noun"] = new_key
                if old_key in (raw.get("reference_nouns") or []):
                    raw["reference_nouns"] = _rewrite_list(raw.get("reference_nouns") or [])
            elif fp.startswith("fields."):
                fn = fp.split(".", 1)[1]
                fr = wt.fields.get(fn)
                if fr is not None and fr.reference_noun == old_key:
                    fr.reference_noun = new_key
                    fr.raw["reference_noun"] = new_key
                    nested = raw.get("fields")
                    if isinstance(nested, dict) and fn in nested and isinstance(nested[fn], dict):
                        nested[fn]["reference_noun"] = new_key
            elif fp.startswith("adverb_schema."):
                sch = raw.get("adverb_schema") or {}
                if old_key in sch:
                    sch[new_key] = sch.pop(old_key)
            elif fp.startswith("request_options."):
                # request_options.<val>.monitored_verbs
                _, val, _field = fp.split(".", 2)
                ro = raw.get("request_options") or {}
                if val in ro and isinstance(ro[val], dict):
                    ro[val]["monitored_verbs"] = _rewrite_list(ro[val].get("monitored_verbs") or [])
            dirty_kinds.add(kind)

        # rename the entry's own key
        words = self._types(word_type)
        if old_key in words:
            wt = words.pop(old_key)
            wt.name = new_key
            wt.raw["name"] = new_key if "name" in wt.raw else wt.raw.get("name")
            words[new_key] = wt
            dirty_kinds.add(word_type)

        for kind in dirty_kinds:
            # Phase 6/R17: write the canonical name-keyed dict for ALL kinds (adjective/adverb
            # included) so a rename persists the migrated shape instead of reverting to a list.
            write_types(self.project_path, kind, self._types(kind), legacy=False)
        return deps
