#!/usr/bin/env python3
"""Guard against bugs hiding in JavaScript embedded in Python string literals.

GIMS nodes ship browser JS as big inline `r\"\"\"...\"\"\"` blobs (INJECT_JS, STATE_TAB_JS,
_DOCK_JS, ...). A whole class of defect lives there invisibly:

  * a Python literal used where JS wants a value — e.g. `el.disabled = True`
    (real bug fixed 2026-06-27) becomes a runtime `ReferenceError: True is not
    defined` in the browser;
  * an outright JS syntax error.

Neither is caught by `python -m compileall` (the blob is a valid *string*) nor by
the rest of pytest (the JS is never executed). This module extracts every
JS-looking string constant from the Python *source* tree and:

  1. flags Python-isms used in JS value positions (regex, no deps);
  2. if Node is available, runs `node --check` for a real syntax pass.

Run directly as a pre-commit / CI gate:

    python tools/lint/check_inline_js.py        # exit 1 on any finding

Or rely on tests/test_inline_js.py, which calls scan_pythonisms()/scan_syntax().
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Only SOURCE — never build artifacts, deps, runtime data, or vendored/frozen copies.
SKIP_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", "dist", "build",
    "gims-electron", "backups", "projects", "Style Inspirations",
}

# A string constant is treated as JS if it's sizeable and has a couple of JS tells.
JS_MARKERS = ("function", "addEventListener", "document.", "=>", "querySelector",
              "const ", "let ", "window.", "createElement")

# Python literals sitting where JS expects a value: `= True`, `: None`, `? False`,
# `return None`, `&& True`, etc. (JS has no True/False/None — these are bugs.)
PY_LITERAL = re.compile(r"(?:[=(,:?]|=>|\breturn|&&|\|\|)\s*(True|False|None)\b")
# Other Python constructs that are wrong/invalid in JS.
PY_SMELL = re.compile(r"(?<![\w.$])(?:elif |def |lambda | is None| is not None|print\()")

_COMMENT_PREFIXES = ("//", "*", "/*")


def _looks_like_js(s: str) -> bool:
    return len(s) > 150 and sum(m in s for m in JS_MARKERS) >= 2


def find_blobs(root: Path = REPO_ROOT):
    """Yield (path, varname, lineno, text) for every JS-looking str constant in source."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = Path(dirpath) / fn
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Assign)
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                        and _looks_like_js(node.value.value)):
                    target = node.targets[0] if node.targets else None
                    name = target.id if isinstance(target, ast.Name) else "<expr>"
                    yield p, name, node.lineno, node.value.value


def scan_pythonisms(root: Path = REPO_ROOT):
    """Return [(relpath, varname, blob_line, kind, token, text), ...]."""
    out = []
    for p, name, _lineno, text in find_blobs(root):
        rel = p.relative_to(root)
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(_COMMENT_PREFIXES):  # ignore JS comments
                continue
            for m in PY_LITERAL.finditer(line):
                out.append((str(rel), name, i, "py-literal", m.group(1), stripped[:140]))
            for m in PY_SMELL.finditer(line):
                out.append((str(rel), name, i, "py-smell", m.group(0).strip(), stripped[:140]))
    return out


def scan_syntax(root: Path = REPO_ROOT):
    """Return [(relpath, varname, first_error_line), ...] via `node --check` (empty if no node)."""
    if not shutil.which("node"):
        return []
    out = []
    for p, name, _lineno, text in find_blobs(root):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
            tf.write(text)
            tmp = tf.name
        try:
            r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
        finally:
            os.unlink(tmp)
        if r.returncode:
            first = (r.stderr.strip().splitlines() or ["syntax error"])[0]
            out.append((str(p.relative_to(root)), name, first))
    return out


def format_pythonisms(rows) -> str:
    return "\n".join(
        f"  {rel}  ({name})  [{kind} '{tok}']  blob-line {ln}: {text}"
        for (rel, name, ln, kind, tok, text) in rows
    )


def main() -> int:
    blobs = list(find_blobs(REPO_ROOT))
    lit = scan_pythonisms()
    syn = scan_syntax()
    if lit:
        print(f"Python-isms in inline JS ({len(lit)}):")
        print(format_pythonisms(lit))
    if syn:
        print(f"Inline-JS syntax errors ({len(syn)}):")
        for rel, name, err in syn:
            print(f"  {rel} ({name}): {err}")
    if not lit and not syn:
        mode = "node syntax-checked" if shutil.which("node") else "node not found; regex-only"
        print(f"OK — {len(blobs)} inline-JS blobs clean ({mode}).")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
