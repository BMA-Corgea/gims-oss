# api/routers/noun_workbench/paths.py
"""Canonical project-path resolution + the ``/project_path`` diagnostics route."""

from pathlib import Path

from api.manifest.resolver import resolve_path  # resolver

from ._router import log, router


# ──────────────────────────────────────────────────────────────────────────────
# Project path resolver (canonical)
# ──────────────────────────────────────────────────────────────────────────────
def get_project_path(project: str) -> Path:
    """
    Resolve /projects/<project> using resolver (no hard-coding).
    """
    projects_root = resolve_path(Path(), "project_root")
    pp = (projects_root / project).resolve()
    log.debug("[project_path]", pp)
    return pp

@router.get("/project_path", summary="Resolve project path")
def project_path_endpoint(project: str) -> str:
    """Small helper endpoint for the UI/diagnostics."""
    return str(get_project_path(project))
