"""Front-door guard (Phase 6): the app's mounted route set must not silently drift.

The proposal mandates snapshotting the OpenAPI path list and diffing it before/after wiring changes
(collapsing the dual registry+include_router front door, the R21 node/module factories, etc.). This
test pins the committed baseline so any accidental add/drop of a route fails loudly. When a route is
INTENTIONALLY added/removed, regenerate tests/openapi_paths_baseline.json in the same commit.
"""
import json
from pathlib import Path

BASELINE = Path(__file__).parent / "openapi_paths_baseline.json"
ALL_ROUTES_BASELINE = Path(__file__).parent / "all_routes_baseline.json"


def _current_paths():
    import api.app as m
    return set(m.app.openapi()["paths"].keys())


def test_openapi_path_set_matches_baseline():
    baseline = set(json.loads(BASELINE.read_text()))
    current = _current_paths()
    missing = baseline - current      # routes that disappeared
    added = current - baseline        # routes that appeared
    assert not missing and not added, (
        f"OpenAPI route drift — regenerate the baseline if intentional.\n"
        f"  removed: {sorted(missing)}\n  added: {sorted(added)}"
    )


def test_all_routes_match_baseline():
    """Covers EVERY mounted route, including the include_in_schema=False page nodes (which the
    OpenAPI guard above misses) — so the R21 node/module factory collapse can't drop a page route.

    NOTE: FastAPI >= ~0.131 keeps included routers as a tree of ``_IncludedRouter`` nodes rather
    than flat ``APIRoute`` objects in ``app.routes``, so we enumerate via the version-robust walker
    (see tests/_route_introspection.py) instead of a naive ``r.path`` scan."""
    import api.app as m
    from tests._route_introspection import mounted_path_set
    baseline = set(json.loads(ALL_ROUTES_BASELINE.read_text()))
    current = mounted_path_set(m.app)
    missing = baseline - current
    added = current - baseline
    assert not missing and not added, (
        f"Mounted-route drift — regenerate all_routes_baseline.json if intentional.\n"
        f"  removed: {sorted(missing)}\n  added: {sorted(added)}"
    )
