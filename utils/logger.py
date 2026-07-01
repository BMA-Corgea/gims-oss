"""One logging kernel for the whole app — replaces 49 hand-rolled ``debug()`` copies.

Before this, 49 files each defined ``def debug(*args): if DEBUG_ENABLED: print("[tag]", ...)``
plus a module-level ``DEBUG_ENABLED`` flag (13 shipped ``= True`` → uncontrolled production
spew and info-leak). Now every module does::

    from utils.logger import get_logger
    log = get_logger(__name__)
    log.debug("thing happened:", value, {"k": 1})

Level comes from one env var ``GIMS_LOG_LEVEL`` (default ``WARNING``). The per-module logger
name preserves the old ``[tag]`` semantics.

The returned logger is a thin wrapper over ``logging.Logger`` whose level methods accept
**print-style varargs** (joined with spaces), because that is how the 49 originals were
called — a 1:1 drop-in. It also forwards ``exc_info=`` and any other ``Logger`` attribute.
"""
from __future__ import annotations

import logging
import sys

from utils.config import log_level

_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    root.setLevel(getattr(logging, log_level(), logging.WARNING))
    _configured = True


class GimsLogger:
    """``logging.Logger`` wrapper with print-style varargs and ``is_debug()``."""

    def __init__(self, name: str) -> None:
        _configure()
        self._log = logging.getLogger(name)

    @staticmethod
    def _msg(args: tuple) -> str:
        return " ".join(str(a) for a in args)

    def _emit(self, level: int, args: tuple, kwargs: dict) -> None:
        if not self._log.isEnabledFor(level):
            return
        self._log.log(level, self._msg(args) if args else "", exc_info=kwargs.get("exc_info"))

    def debug(self, *args, **kwargs) -> None:
        self._emit(logging.DEBUG, args, kwargs)

    def info(self, *args, **kwargs) -> None:
        self._emit(logging.INFO, args, kwargs)

    def warning(self, *args, **kwargs) -> None:
        self._emit(logging.WARNING, args, kwargs)

    warn = warning

    def error(self, *args, **kwargs) -> None:
        self._emit(logging.ERROR, args, kwargs)

    def critical(self, *args, **kwargs) -> None:
        self._emit(logging.CRITICAL, args, kwargs)

    def exception(self, *args, **kwargs) -> None:
        kwargs.setdefault("exc_info", True)
        self._emit(logging.ERROR, args, kwargs)

    def is_debug(self) -> bool:
        return self._log.isEnabledFor(logging.DEBUG)

    def __getattr__(self, name: str):
        # Forward setLevel/isEnabledFor/etc. to the underlying logger. Guard _log so
        # attribute access during __init__ does not recurse.
        if name == "_log":
            raise AttributeError(name)
        return getattr(self._log, name)


def get_logger(name: str = "gims") -> GimsLogger:
    """Return a namespaced print-style logger gated by ``GIMS_LOG_LEVEL``."""
    return GimsLogger(name)
