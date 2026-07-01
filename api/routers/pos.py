"""Generic part-of-speech read surface — ONE endpoint shape for all six word kinds.

Phase 3.6 (additive scope). The five POS editors (noun/verb/adjective/adverb/conjunction) each
grew their own bespoke list/get routes. This router exposes a single uniform read surface that
funnels through the ONE normalize-on-read choke point (``core.words.reader.read_types``) and the
ONE lifecycle owner (``utils.word_registry.WordRegistry``), so any word kind is introspected the
same way regardless of its on-disk shape (list or keyed dict):

    GET /pos/{kind}/{project}            -> {name: canonical-definition}  (all types of that kind)
    GET /pos/{kind}/{project}/{name}     -> {definition, dependents}      (one type + what uses it)

``kind`` is constrained by an Enum so only the five real kinds resolve (this also keeps the path
from shadowing kind-specific routes like ``/verb/status-workflow/*``). This is READ-ONLY and
ADDITIVE — every existing POS route is untouched. The CRUD-forwarder consolidation (converting the
scattered POS routes into thin aliases over a generic handler) is folded into the Phase 6 route
collapse, where it belongs with the OpenAPI-diff guard.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path

from fastapi import APIRouter

from core.errors import AppError
from core.words.reader import read_types
from utils.logger import get_logger
from utils.paths import projects_dir
from utils.word_registry import WordRegistry

log = get_logger(__name__)


class WordKind(str, Enum):
    noun = "noun"
    verb = "verb"
    adjective = "adjective"
    adverb = "adverb"
    conjunction = "conjunction"


def _resolve_project_path(project: str) -> Path:
    """Accept an absolute path or a project name living under ``projects/``."""
    p = Path(project)
    if p.exists():
        return p.resolve()
    candidate = projects_dir() / project
    if not candidate.exists():
        raise AppError("PROJECT_NOT_FOUND", f"No project '{project}'", status=404,
                       details={"project": project})
    return candidate


def make_word_router() -> APIRouter:
    """Build the generic ``/pos`` read router (one router, kind as an Enum path param)."""
    router = APIRouter(prefix="/pos", tags=["pos"])

    @router.get("/{kind}/{project}", summary="List all types of one part of speech")
    def list_types(kind: WordKind, project: str):
        project_path = _resolve_project_path(project)
        words = read_types(project_path, kind.value)
        return {"kind": kind.value, "project": project,
                "count": len(words),
                "types": {name: wt.to_dict() for name, wt in words.items()}}

    @router.get("/{kind}/{project}/{name}", summary="Get one part-of-speech type + its dependents")
    def get_type(kind: WordKind, project: str, name: str):
        project_path = _resolve_project_path(project)
        words = read_types(project_path, kind.value)
        wt = words.get(name)
        if wt is None:
            raise AppError("WORD_NOT_FOUND", f"No {kind.value} '{name}' in '{project}'",
                           status=404, details={"kind": kind.value, "name": name})
        try:
            dependents = WordRegistry(project_path.name, project_path=project_path).get_dependents(kind.value, name)
        except Exception as e:  # dependents are best-effort context, never fail the read
            log.debug("[word_router] get_dependents failed:", repr(e))
            dependents = []
        return {"kind": kind.value, "project": project, "name": name,
                "definition": wt.to_dict(), "dependents": dependents}

    return router


router = make_word_router()
