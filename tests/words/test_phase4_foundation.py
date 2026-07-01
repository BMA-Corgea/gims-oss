"""Phase 4 foundation (additive): typed RunContext + kind->executor dispatch registry."""
from pathlib import Path

from core.orchestration.run_context import RunContext


def test_run_context_round_trips_loose_dict():
    fetch = lambda *a, **k: []
    d = {
        "verb_group": "Chemistry", "run_id": "R1", "pphrase_name": None,
        "params": {"run_ids": ["R1", "R2"], "x": 1}, "project_path": Path("/p"),
        "canonical_phrase_base": "base", "fetch_noun_items": fetch,
        "some_future_key": 42,  # unknown -> extra
    }
    ctx = RunContext.from_dict(d)
    assert ctx.verb_group == "Chemistry"
    assert ctx.run_ids == ["R1", "R2"]
    assert ctx.fetch_noun_items is fetch
    assert ctx.extra["some_future_key"] == 42
    # to_dict preserves all keys (first-class + extra) so the legacy runner is unaffected.
    back = ctx.to_dict()
    for k, v in d.items():
        assert back[k] == v


def test_run_context_get_shim():
    ctx = RunContext(verb_group="V", run_id="R1")
    assert ctx.get("verb_group") == "V"
    assert ctx.get("missing", "dflt") == "dflt"


def test_run_context_empty():
    ctx = RunContext.from_dict(None)
    assert ctx.params == {} and ctx.run_ids == [] and ctx.verb_group is None


def test_executor_registry_matches_legacy_ternary():
    from core.run_custom import (
        resolve_executor, CustomParserExecutable, PrepositionalPhraseExecutable,
    )
    assert isinstance(resolve_executor("parser"), CustomParserExecutable)
    assert isinstance(resolve_executor("pphrase"), PrepositionalPhraseExecutable)
    # unknown kind -> pphrase, exactly as the old `else` branch did
    assert isinstance(resolve_executor("anything-else"), PrepositionalPhraseExecutable)
