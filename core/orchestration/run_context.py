"""Typed run-execution context — one named shape for what is today a loose dict.

The custom-tool runner threads a bare ``dict`` (``verb_group`` / ``run_id`` / ``params`` /
``project_path`` / an injected ``fetch_noun_items`` callable, ...) from the GUI into
``core.run_custom.run_custom_tool`` and its executors. :class:`RunContext` gives that shape
a name, defaults, and a documented contract, while staying *dict-compatible* at the boundary
(``from_dict`` / ``to_dict`` / mapping accessors) so it can be adopted incrementally without
rewriting every ``context.get(...)`` reader at once.

Phase 4 foundation: additive. Construct a RunContext at the GUI boundary, pass ``.to_dict()`` to
the existing dict-consuming runner; migrate readers to attribute access over time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Keys that have first-class fields; everything else round-trips through ``extra``.
_KNOWN_KEYS = {
    "verb_group", "run_id", "pphrase_name", "params", "project_path",
    "canonical_phrase_base", "fetch_noun_items",
}


@dataclass
class RunContext:
    """The context passed through a custom-tool run. All fields optional (a pphrase run has no
    ``verb_group``/``run_id``; a parser run has no ``pphrase_name``)."""

    verb_group: Optional[str] = None
    run_id: Optional[str] = None
    pphrase_name: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    project_path: Optional[Path] = None
    canonical_phrase_base: Optional[str] = None
    fetch_noun_items: Optional[Callable[..., Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)  # forward-compat passthrough

    @property
    def run_ids(self) -> List[str]:
        """Convenience: the resolved run-id list the runner stashes under ``params``."""
        rid = self.params.get("run_ids")
        return list(rid) if isinstance(rid, (list, tuple)) else ([] if rid is None else [rid])

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "RunContext":
        d = dict(d or {})
        known = {k: d.pop(k) for k in list(d) if k in _KNOWN_KEYS}
        params = known.get("params") or {}
        return cls(
            verb_group=known.get("verb_group"),
            run_id=known.get("run_id"),
            pphrase_name=known.get("pphrase_name"),
            params=dict(params) if isinstance(params, dict) else {},
            project_path=known.get("project_path"),
            canonical_phrase_base=known.get("canonical_phrase_base"),
            fetch_noun_items=known.get("fetch_noun_items"),
            extra=d,  # whatever else the caller passed
        )

    def to_dict(self) -> Dict[str, Any]:
        """The legacy loose-dict form the runner/executors still consume. Omits ``None`` first-
        class fields only when they were never set is unnecessary — the runner uses ``.get``."""
        out: Dict[str, Any] = {
            "verb_group": self.verb_group,
            "run_id": self.run_id,
            "pphrase_name": self.pphrase_name,
            "params": self.params,
            "project_path": self.project_path,
            "canonical_phrase_base": self.canonical_phrase_base,
            "fetch_noun_items": self.fetch_noun_items,
        }
        out.update(self.extra)
        return out

    # Minimal mapping shim so existing ``context.get("verb_group")`` callers work unchanged
    # if handed a RunContext directly during incremental migration.
    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)
