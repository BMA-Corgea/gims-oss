"""Generic write-side descriptor handler — the uniform core shared by the adjective and adverb
POS routers (Phase 3c collapse).

The two descriptor editors look alike but differ per-kind in scope (adjective attaches to a *noun*
via an ``applies_to`` list; adverb attaches to a single *verb*) and, more deeply, in their CRUD
cascade targets (adjective promote/demote re-types a NOUN FIELD; adverb promote/demote edits a
VERB's ``adverb_schema`` subtree). Those CRUD operations are therefore irreducibly per-kind and
stay in their routers.

What IS genuinely uniform — and lives here so it is written once — is:
  * the handler LOADER (find the type-file entry, wrap it in the unified core Descriptor, or a bare
    WordHandler for an unknown class),
  * the ``/logic`` dispatch (Reference / ReferenceList feed noun items, everything else passes
    ``project_path``),
  * the ``/projects`` listing.

The five POS routers keep their exact route declarations (path/method/order/name) — only their
bodies delegate here — so the byte-pinned route baselines + order hash are unaffected.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from api import i_o
from core.errors import AppError
from core.words.handlers.base import WordHandler
from core.words.handlers.behaviors import Descriptor
from utils.logger import get_logger

log = get_logger(__name__)


def load_descriptor_handler(
    project_path: Path,
    *,
    schema_kind: str,                              # "adjective" | "adverb"
    scope_value: str,                              # the noun (adjective) or verb (adverb)
    attaches_to: str,                              # "noun" | "verb"
    match: Callable[[dict], bool],                 # selects the entry in the <kind>_types list
    behavior_of: Callable[[dict], Optional[str]],  # entry -> Descriptor behavior name (or None)
    not_found_code: str,
    not_found_msg: str,
    not_found_details: Dict[str, Any],
    handler_kwargs: Optional[Callable[[], dict]] = None,  # extra ctor kwargs, evaluated on success only
) -> WordHandler:
    """Find the matching descriptor entry and wrap it in the unified ``Descriptor`` behavior — or a
    bare :class:`WordHandler` when the class is unknown (the legacy Base* fallback). Behaviour-
    identical to the former per-router ``load_*_handler`` helpers; the per-kind specifics are passed
    in so neither router reimplements the Descriptor/WordHandler wiring."""
    entries: List[Dict[str, Any]] = i_o.load_schema(project_path, schema_kind)
    entry = next((e for e in entries if match(e)), None)
    if entry is None:
        raise AppError(not_found_code, not_found_msg, status=404, details=not_found_details)
    kw = handler_kwargs() if handler_kwargs else {}
    behavior = behavior_of(entry)
    if behavior is None:
        return WordHandler(entry, attaches_to=attaches_to, target_name=scope_value,
                           project_name=project_path.name, **kw)
    return Descriptor(entry, behavior_name=behavior, attaches_to=attaches_to,
                      target_name=scope_value, project_name=project_path.name, **kw)


def run_descriptor_logic(project_path: Path, handler: WordHandler) -> Any:
    """Shared ``/logic`` dispatch. Reference / ReferenceList behaviors are fed the referenced noun's
    items (missing store -> empty); everything else just receives ``project_path``. Byte-identical to
    the former adjective/adverb ``run_logic`` bodies."""
    behavior_name = getattr(handler, "behavior_name", None)
    if behavior_name == "ReferenceList":
        noun_items_map: Dict[str, list] = {}
        for ref_noun in handler.get_reference_noun():
            try:
                noun_items_map[ref_noun] = i_o.get_noun_items(project_path, ref_noun)
            except FileNotFoundError:
                noun_items_map[ref_noun] = []
        return handler.use_logic(noun_items_map=noun_items_map)
    if behavior_name == "Reference":
        ref_noun = handler.get_reference_noun()
        try:
            items = i_o.get_noun_items(project_path, ref_noun)
        except FileNotFoundError:
            items = []
        return handler.use_logic(noun_items=items)
    return handler.use_logic(project_path=project_path) or {}


def list_projects() -> list:
    """Available projects (S3- and local-aware); empty list on failure instead of a 500."""
    try:
        return i_o.io_list_projects()
    except Exception:
        log.warning("descriptor list_projects failed; returning empty list", exc_info=True)
        return []
