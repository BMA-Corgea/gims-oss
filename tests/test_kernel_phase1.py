"""Phase 1 kernel guards + unit tests: logger, paths, config.

Guard tests lock in the migration so the duplication cannot regrow:
  - zero top-level `def debug(` definitions remain in source;
  - no file walks up for a directory literally named 'GIMS-Project'.
"""
import logging
import os
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC_DIRS = ("api", "core", "gui", "nodes", "tools", "utils")


def _source_files():
    for sub in SRC_DIRS:
        for py in (REPO / sub).rglob("*.py"):
            parts = set(py.parts)
            if parts & {".venv", "node_modules", "dist", "build", "gims-electron", "__pycache__"}:
                continue
            yield py


# --------------------------------------------------------------------- guards

def test_no_toplevel_def_debug_remains():
    import re

    pat = re.compile(r"^def debug\s*\(", re.M)
    offenders = [str(p.relative_to(REPO)) for p in _source_files()
                 if pat.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert offenders == [], f"hand-rolled top-level def debug still present in: {offenders}"


def test_no_hardcoded_repo_root_folder_walk():
    offenders = [str(p.relative_to(REPO)) for p in _source_files()
                 if "'GIMS-Project' not in" in p.read_text(encoding="utf-8", errors="ignore")]
    assert offenders == [], f"hard-coded repo-root folder-name walk still present in: {offenders}"


# --------------------------------------------------------------------- logger

def test_get_logger_print_style_join(caplog):
    from utils.logger import get_logger

    log = get_logger("gims.test.print_style")
    with caplog.at_level(logging.DEBUG, logger="gims.test.print_style"):
        log.debug("value is:", {"k": 1}, 42)
    assert "value is: {'k': 1} 42" in caplog.text


def test_get_logger_exc_info_passthrough(caplog):
    from utils.logger import get_logger

    log = get_logger("gims.test.exc")
    with caplog.at_level(logging.ERROR, logger="gims.test.exc"):
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("failed while doing X")
    assert "failed while doing X" in caplog.text
    assert "ValueError" in caplog.text  # traceback included


def test_get_logger_never_raises_on_odd_args():
    from utils.logger import get_logger

    log = get_logger("gims.test.odd")
    log.debug()  # no args
    log.info("a", None, object())  # mixed types
    log.warning("x", end="", sep="|")  # stray print-style kwargs are ignored


def test_is_debug_reflects_level():
    from utils.logger import get_logger

    log = get_logger("gims.test.level")
    log.setLevel(logging.WARNING)
    assert log.is_debug() is False
    log.setLevel(logging.DEBUG)
    assert log.is_debug() is True


# ---------------------------------------------------------------------- paths

def test_repo_root_resolves_to_sentinel_dir():
    from utils.paths import repo_root

    root = repo_root()
    assert root.is_dir()
    # Anchored on a sentinel, not a folder name.
    assert (root / "requirements.txt").exists() or (root / "main.py").exists()
    assert root == REPO


def test_resource_path_joins_under_root():
    from utils.paths import repo_root, resource_path

    assert resource_path("projects") == repo_root() / "projects"


# --------------------------------------------------------------------- config

def test_log_level_default_is_warning(monkeypatch):
    import utils.config as config

    monkeypatch.delenv("GIMS_LOG_LEVEL", raising=False)
    assert config.log_level() == "WARNING"
    monkeypatch.setenv("GIMS_LOG_LEVEL", "debug")
    assert config.log_level() == "DEBUG"


def test_rds_and_provider_defaults(monkeypatch):
    import utils.config as config

    monkeypatch.delenv("GIMS_RDS_ENABLED", raising=False)
    monkeypatch.delenv("GIMS_STORAGE_PROVIDER", raising=False)
    assert config.rds_enabled() is False
    assert config.storage_provider() == "local"
    monkeypatch.setenv("GIMS_RDS_ENABLED", "true")
    assert config.rds_enabled() is True


def test_jwt_secret_is_env_first(monkeypatch):
    import utils.config as config

    monkeypatch.setenv("GIMS_JWT_SECRET", "env-wins")
    config.jwt_secret.cache_clear()
    try:
        assert config.jwt_secret() == "env-wins"
    finally:
        config.jwt_secret.cache_clear()
