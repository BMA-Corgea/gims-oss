# api/routers/verb/_helpers.py
#
# Small shared helpers: project-path resolution and verb load/save.
# Moved VERBATIM from api/routers/verb.py (no logic changes).

from pathlib import Path
from typing import Tuple

from api.i_o import load_schema, save_schema
from api.manifest.resolver import resolve_path
from core.errors import AppError

from ._log import log


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _get_project_path(project: str) -> Path:
    """Get project path using the resolver."""
    project_root = resolve_path(Path(), "project_root")
    path = project_root / project
    log.debug("[_get_project_path]", {"root": str(project_root), "project": project, "resolved": str(path)})
    if not path.exists():
        # In S3-first deployments the local dir may not exist; keep 404 for now to match existing behavior.
        raise AppError(
            "PROJECT_NOT_FOUND",
            f"Project {project} not found",
            status=404,
            details={"project": project},
        )
    return path

def _load_verb(proj: Path, verb_name: str) -> Tuple[dict, dict]:
    """Load full verb_types.json and return (all_verbs, verb_def) with checks."""
    log.debug("[_load_verb] start", {"project": str(proj), "verb": verb_name})
    verbs = load_schema(proj, "verb")
    if verb_name not in verbs:
        log.debug("[_load_verb] 404", {"verb": verb_name})
        raise AppError(
            "VERB_NOT_FOUND",
            f"Verb {verb_name} not found",
            status=404,
            details={"verb": verb_name},
        )
    log.debug("[_load_verb] ok")
    return verbs, verbs[verb_name]

def _save_verb(proj: Path, verbs: dict, verb_name: str, updated: dict) -> None:
    """Persist updated verb_def back to verb_types.json."""
    log.debug("[_save_verb] start", {"verb": verb_name})
    verbs = dict(verbs)
    verbs[verb_name] = updated
    save_schema(proj, "verb", verbs)
    log.debug("[_save_verb] saved", {"verb": verb_name})
