"""Module-composition guard (Phase 6 / R21): the registered modules' node-lists + injections must
not drift.

The route guards (test_openapi_baseline) catch a dropped ROUTE, but NOT a dropped node or a changed
asset injection inside a Module — exactly what the `make_standard_module` collapse of the 20
`modules/*.py` could get wrong. This pins each module's name -> {sorted node names, inject map} so the
factory refactor is byte-equivalent. Regenerate module_composition_baseline.json in the same commit
when a composition change is intentional.
"""
import json
from pathlib import Path

BASELINE = Path(__file__).parent / "module_composition_baseline.json"


def _current_composition():
    import api.app  # noqa: F401 — registers all modules
    from core.orchestration.registry import registry

    snap = {}
    for m in registry.all():
        inject = getattr(m, "_inject", None) or getattr(m, "inject", {}) or {}
        snap[m.name] = {
            "nodes": sorted(n.name for n in m.nodes.values()),
            "inject": {
                k: {
                    "scripts": sorted(v.get("scripts", [])),
                    "stylesheets": sorted(v.get("stylesheets", [])),
                }
                for k, v in inject.items()
            },
        }
    return snap


def test_module_composition_matches_baseline():
    baseline = json.loads(BASELINE.read_text())
    current = _current_composition()
    assert set(baseline) == set(current), (
        f"module set drift — removed: {sorted(set(baseline) - set(current))}, "
        f"added: {sorted(set(current) - set(baseline))}"
    )
    diffs = {name: {"baseline": baseline[name], "current": current[name]}
             for name in baseline if baseline[name] != current[name]}
    assert not diffs, f"module composition drift (regenerate baseline if intentional): {list(diffs)}"
