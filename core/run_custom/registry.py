# core/run_custom/registry.py
from __future__ import annotations
from .schema import ExecutableBase
from .parser_executor import CustomParserExecutable
from .pphrase_executor import PrepositionalPhraseExecutable


# ──────────────────────────────────────────────────────────────────────────────
# Kind -> executor dispatch registry (Phase 4). Replaces the inline
# `CustomParserExecutable() if kind == "parser" else PrepositionalPhraseExecutable()`
# with ONE lookup point, so adding a tool kind is registering a class, not editing a
# branch. Behavior-preserving: unknown kinds fall back to the prepositional-phrase
# executor exactly as the old `else` branch did (upstream validation already pins
# kind to "parser"/"pphrase", so the fallback is unreachable in practice).
# ──────────────────────────────────────────────────────────────────────────────
EXECUTOR_REGISTRY: dict[str, type] = {
    "parser": CustomParserExecutable,
    "pphrase": PrepositionalPhraseExecutable,
}


def resolve_executor(kind: str) -> "ExecutableBase":
    """Instantiate the executor registered for ``kind`` (default: pphrase, matching legacy)."""
    return EXECUTOR_REGISTRY.get(kind, PrepositionalPhraseExecutable)()
