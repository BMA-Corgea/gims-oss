"""Phase 3.4 cohesion locks.

Two guarantees, without the (deferred, high-risk) full handler-class collapse:

1. No-drift guard: every LIVE legacy dispatch map's class keys are a subset of the ONE canonical
   ``core.words.handlers.DESCRIPTOR_CLASSES``. If someone adds a new class key to a scattered
   copy without registering it canonically, this fails — that is exactly the drift Phase 3 kills.
2. The read-side migration shim (``load_descriptor_list``) returns the legacy LIST shape from
   EITHER on-disk shape (list today, name-keyed dict after the migrator runs), so the direct
   list-consumers survive the cutover.
"""
import json

import pytest

from core.words.handlers import DESCRIPTOR_CLASSES
from core.words.reader import load_descriptor_list


def test_live_dispatch_maps_are_subset_of_canonical():
    from api.routers.adjective import ADJ_CLASS_MAP
    from api.routers.adverb import ADV_CLASS_MAP

    canonical = set(DESCRIPTOR_CLASSES)
    for name, m in (("ADJ_CLASS_MAP", ADJ_CLASS_MAP), ("ADV_CLASS_MAP", ADV_CLASS_MAP)):
        extra = set(m) - canonical
        assert not extra, f"{name} has class keys not in the canonical DESCRIPTOR_CLASSES: {extra}"


def test_cli_dispatch_maps_are_subset_of_canonical():
    from utils.handlers.adjective import get_adjective_class_handler
    from utils.handlers.adverb import CLASS_MAP as ADVERB_CLASS_MAP

    canonical = set(DESCRIPTOR_CLASSES)
    assert set(get_adjective_class_handler()) <= canonical
    assert set(ADVERB_CLASS_MAP) <= canonical


@pytest.mark.parametrize("on_disk", [
    # list shape (today)
    [{"adjective": "status", "adjective_class": "Tag", "applies_to": ["Submission"],
      "valid_options": [{"value": "ok"}]},
     {"adjective": "ref", "adjective_class": "Reference", "applies_to": ["Sample"],
      "reference_noun": "Submission", "filters": {}}],
    # name-keyed dict shape (after migration)
    {"status": {"adjective": "status", "adjective_class": "Tag", "applies_to": ["Submission"],
                "valid_options": [{"value": "ok"}]},
     "ref": {"adjective": "ref", "adjective_class": "Reference", "applies_to": ["Sample"],
             "reference_noun": "Submission", "filters": {}}},
])
def test_load_descriptor_list_round_trips_both_shapes(tmp_path, on_disk):
    (tmp_path / "adjective_types.json").write_text(json.dumps(on_disk))
    entries = load_descriptor_list(tmp_path, "adjective")
    assert isinstance(entries, list)
    by_name = {e["adjective"]: e for e in entries}
    assert set(by_name) == {"status", "ref"}
    assert by_name["status"]["adjective_class"] == "Tag"
    assert by_name["ref"]["reference_noun"] == "Submission"
    assert by_name["ref"]["applies_to"] == ["Sample"]
