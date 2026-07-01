"""Root pytest configuration.

Ensures the repository root is importable regardless of how pytest is invoked, so
tests can `import core...`, `import utils...`, etc. consistently. Shared fixtures
added during the refactor live here.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
