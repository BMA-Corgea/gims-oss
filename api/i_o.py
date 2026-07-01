# api/i_o.py -- thin re-export barrel. The storage I/O layer was split (wiring-neutral)
# into api/iostore/. All `from api.i_o import X`, `from api import i_o`/`i_o.X`, and
# monkeypatch.setattr(api.i_o, ...) seams keep working unchanged.
from api.iostore import *  # noqa: F401,F403
# underscore + passthrough names that `*` would skip, re-exported explicitly so
# i_o.<name> attribute access and `from api.i_o import <name>` keep resolving:
from api.iostore import (read_text, write_text, S3_ENABLED, _is_s3_path, resolve_path,
    log, DEBUG_ENABLED, _PSYCOPG_AVAILABLE)

# load_local_layout_map STAYS physically here: its body uses Path(__file__).resolve().parent
# to reach api/manifest/local_layout_map.json, so it cannot move into the package.
import json
from pathlib import Path


def load_local_layout_map(project_path: Path) -> dict:
    """
    (Unchanged) This file MUST be local to configure the S3/RDS connection.
    """
    here = Path(__file__).resolve()
    repo_root = here.parent
    fpath = repo_root / "manifest" / "local_layout_map.json"
    if not fpath.exists():
        raise FileNotFoundError(f"local_layout_map.json not found at {fpath}")

    data = json.loads(fpath.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        if "db_map" in data and isinstance(data["db_map"], dict):
            return data["db_map"]
        if "db_endpoints" in data and isinstance(data["db_endpoints"], dict):
            return data["db_endpoints"]
    return data if isinstance(data, dict) else {}


def list_projects_safe() -> list:
    """Swallow + log a project-listing failure, returning [] (never raises)."""
    try:
        return io_list_projects()
    except Exception:
        log.warning("[list_projects] list_projects failed", exc_info=True)
        return []
