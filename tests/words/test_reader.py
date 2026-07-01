"""Phase 3 Step 0: the back-compat reader normalizes list-OR-dict to one keyed shape,
idempotently, against the real (inconsistently-shaped) project files."""
import pytest

from core.words.reader import read_types, write_types
from utils.paths import projects_dir

PROJECTS = ["LIMS-System", "RunlogTest", "Sterility"]
KINDS = ["noun", "verb", "adjective", "adverb"]


@pytest.mark.parametrize("project", PROJECTS)
@pytest.mark.parametrize("kind", KINDS)
def test_read_returns_keyed_dict_without_crashing(project, kind):
    pp = projects_dir() / project
    words = read_types(pp, kind)
    assert isinstance(words, dict)
    for name, wt in words.items():
        assert wt.kind == kind
        assert wt.name


def test_list_shaped_adjectives_are_visible():
    """The headline bug: list-shaped adjective files used to return an empty set."""
    adjs = read_types(projects_dir() / "LIMS-System", "adjective")
    assert len(adjs) > 0
    # a recurring adjective folds to a single key with multiple attach targets
    multi = [wt for wt in adjs.values() if len(wt.attaches_to) >= 1]
    assert multi, "expected adjectives to carry attaches_to noun targets"


def test_dict_shaped_adjectives_do_not_crash():
    """Sterility/adjective_types.json is a dict {} — must read as empty, not crash."""
    assert read_types(projects_dir() / "Sterility", "adjective") == {}


@pytest.mark.parametrize("project", PROJECTS)
@pytest.mark.parametrize("kind", KINDS)
def test_keyed_dict_roundtrip_is_idempotent(project, kind, tmp_path):
    first = read_types(projects_dir() / project, kind)
    write_types(tmp_path, kind, first, legacy=False)        # canonical keyed dict
    second = read_types(tmp_path, kind)
    assert set(first) == set(second)
    for k in first:
        assert first[k].to_dict() == second[k].to_dict()


@pytest.mark.parametrize("kind", ["adjective", "adverb"])
def test_legacy_list_roundtrip_preserves_names_and_targets(kind, tmp_path):
    first = read_types(projects_dir() / "LIMS-System", kind)
    write_types(tmp_path, kind, first, legacy=True)          # back to legacy list
    second = read_types(tmp_path, kind)
    assert set(first) == set(second)
    for k in first:
        assert set(first[k].attaches_to) == set(second[k].attaches_to)
