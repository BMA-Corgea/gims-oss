"""Guard: no Python-isms or syntax errors in JavaScript embedded in Python strings.

GIMS nodes ship browser JS as large inline string blobs (INJECT_JS / STATE_TAB_JS /
_DOCK_JS ...). A Python literal slipping into one (e.g. `el.disabled = True`) becomes a
runtime ``ReferenceError`` in the user's browser, yet is invisible to ``compileall``
(valid string) and to the rest of pytest (the JS is never executed). This locks the
door on that class after the 2026-06-27 ``True``->``true`` fix.

Logic lives in tools/lint/check_inline_js.py (also runnable standalone as a CI gate).
"""
import importlib.util
import shutil
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_inline_js", _REPO / "tools" / "lint" / "check_inline_js.py"
)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def test_inline_js_blobs_are_discovered():
    # Sanity: the scanner actually finds the node JS blobs (so a green run means
    # "checked", not "found nothing to check").
    assert len(list(check.find_blobs(_REPO))) > 0


def test_no_pythonisms_in_inline_js():
    rows = check.scan_pythonisms(_REPO)
    assert not rows, "Python-isms found in inline JS:\n" + check.format_pythonisms(rows)


@pytest.mark.skipif(not shutil.which("node"), reason="node not available for JS syntax check")
def test_inline_js_is_syntactically_valid():
    errs = check.scan_syntax(_REPO)
    assert not errs, "Inline-JS syntax errors:\n" + "\n".join(
        f"  {rel} ({name}): {err}" for (rel, name, err) in errs
    )
