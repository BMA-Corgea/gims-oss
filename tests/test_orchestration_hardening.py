"""Phase 6 — orchestration engine hardening (module/node/chain/trigger).

Focused-hardening pass per the scan: trigger handlers fail-open-but-LOUD + per-handler timeout
(R7 down-payment); registry/module/node fail-fast validation + singleton mount dedup; fetch-node
guard hooks fail-closed. Full EventDispatcher rewrite (R7) + full require_permission (R6) remain
dedicated Phase-7 work.
"""
from __future__ import annotations

import asyncio

import pytest


# ───────────────────────────────────────────────────── triggers: loud + bounded (Orch-2)

def test_maybe_await_swallows_handler_error_but_logs(monkeypatch):
    from core.orchestration import triggers
    warnings = []
    monkeypatch.setattr(triggers.log, "warning", lambda *a, **k: warnings.append(a))

    def boom():
        raise ValueError("kaboom")

    # Must NOT raise (side-effect handlers fail open) ...
    asyncio.run(triggers._maybe_await(boom))
    # ... but must be LOUD (no longer a DEBUG-gated silent swallow).
    assert warnings, "handler error was not logged"
    assert any("boom" in str(a).lower() or "error" in str(a).lower() for a in warnings[0])


def test_maybe_await_times_out_slow_handler(monkeypatch):
    from core.orchestration import triggers
    monkeypatch.setenv("GIMS_CHAIN_HANDLER_TIMEOUT", "0.1")
    warnings = []
    monkeypatch.setattr(triggers.log, "warning", lambda *a, **k: warnings.append(a))

    async def slow():
        await asyncio.sleep(5)

    # Should return in ~0.1s, not hang 5s, and log a timeout.
    asyncio.run(triggers._maybe_await(slow))
    assert any("timed out" in " ".join(str(x) for x in a).lower() for a in warnings)


def test_publish_chain_pre_isolates_a_failing_subscriber(monkeypatch):
    from core.orchestration import triggers
    monkeypatch.setattr(triggers.log, "warning", lambda *a, **k: None)
    ran = []

    async def bad(env, request):
        raise RuntimeError("nope")

    async def good(env, request):
        ran.append("good")

    # register on snapshot-isolated lists, then clean up
    triggers.subscribe_chain_pre(bad)
    triggers.subscribe_chain_pre(good)
    try:
        asyncio.run(triggers.publish_chain_pre({}, None))
        assert "good" in ran, "a failing subscriber blocked the others"
    finally:
        triggers.unsubscribe_chain_pre(bad)
        triggers.unsubscribe_chain_pre(good)


def test_triggers_uses_kernel_logger_not_print():
    """triggers.py must have migrated off its own DEBUG/print to the kernel logger."""
    import core.orchestration.triggers as triggers
    src = __import__("pathlib").Path(triggers.__file__).read_text()
    assert "DEBUG = False" not in src
    assert 'print("[triggers]"' not in src
    assert hasattr(triggers, "log")


# ───────────────────────────────────────────── registry/module/node fail-fast + dedup (Orch-3)

from fastapi import APIRouter, FastAPI  # noqa: E402
from core.orchestration.node import Node, NodeKind  # noqa: E402
from core.orchestration.module import Module  # noqa: E402
from core.orchestration.registry import ModuleRegistry  # noqa: E402


def _api_node(name: str) -> Node:
    return Node(name=name, kind=NodeKind.API, router=APIRouter())


def test_node_requires_router_for_non_chain():
    with pytest.raises(ValueError):
        Node(name="x", kind=NodeKind.API, router=None)


def test_node_chain_requires_chain_or_router():
    with pytest.raises(ValueError):
        Node(name="c", kind=NodeKind.CHAIN, router=None, chain=None)


def test_node_rejects_bad_kind_and_empty_name():
    with pytest.raises(ValueError):
        Node(name="", kind=NodeKind.API, router=APIRouter())
    with pytest.raises(ValueError):
        Node(name="x", kind="api", router=APIRouter())  # type: ignore[arg-type]


def test_module_rejects_duplicate_node_names():
    with pytest.raises(ValueError):
        Module(name="M", nodes=[_api_node("dup"), _api_node("dup")])


def test_module_rejects_non_node():
    with pytest.raises(TypeError):
        Module(name="M", nodes=["not-a-node"])  # type: ignore[list-item]


def test_registry_rejects_duplicate_module_name():
    reg = ModuleRegistry()
    reg.register(Module(name="M", nodes=[_api_node("a")]))
    with pytest.raises(ValueError):
        reg.register(Module(name="M", nodes=[_api_node("b")]))  # different object, same name


def test_registry_tolerates_same_object_reregister():
    reg = ModuleRegistry()
    m = Module(name="M", nodes=[_api_node("a")])
    reg.register(m)
    reg.register(m)  # idempotent — must not raise
    assert reg.list() == ["M"]


def test_mount_all_dedups_shared_singleton_router():
    """A router object shared across modules is mounted ONCE, not once per module."""
    shared = APIRouter()

    @shared.get("/shared/ping")
    def _ping():
        return {"ok": True}

    shared_node = Node(name="shared", kind=NodeKind.API, router=shared)
    reg = ModuleRegistry()
    reg.register(Module(name="A", nodes=[shared_node, _api_node("a_only")]))
    reg.register(Module(name="B", nodes=[shared_node, _api_node("b_only")]))

    app = FastAPI()
    reg.mount_all(app)
    # FastAPI >= ~0.131 keeps each include as an _IncludedRouter tree node (no flat .path), so
    # count occurrences across the recursed route tree: dedup works -> exactly 1, broken -> 2.
    from tests._route_introspection import count_path
    n = count_path(app, "/shared/ping")
    assert n == 1, f"shared router mounted {n}x (expected 1)"


# ───────────────────────────────────────── fetch-node guard hooks fail-closed + auth (Orch-4)

from nodes.orchestrated_fetch_node import _pre_hook_fails_closed  # noqa: E402


def test_pre_hook_absent_allows(monkeypatch):
    monkeypatch.setenv("GIMS_FAIL_CLOSED_HOOKS", "true")
    # 404/405 == hook genuinely not configured -> allow (do not deny)
    assert _pre_hook_fails_closed(404) is False
    assert _pre_hook_fails_closed(405) is False


def test_pre_hook_error_fails_closed_by_default(monkeypatch):
    monkeypatch.delenv("GIMS_FAIL_CLOSED_HOOKS", raising=False)  # secure default
    assert _pre_hook_fails_closed(None) is True   # network error / timeout
    assert _pre_hook_fails_closed(500) is True    # 5xx / garbage / non-JSON
    assert _pre_hook_fails_closed(403) is True


def test_pre_hook_can_be_reverted_to_fail_open(monkeypatch):
    monkeypatch.setenv("GIMS_FAIL_CLOSED_HOOKS", "false")
    assert _pre_hook_fails_closed(None) is False
    assert _pre_hook_fails_closed(500) is False


def test_every_registered_module_carries_an_auth_guard():
    """Auth guard-test (owner: central guard + guard-test): no module may be mounted without its
    policy (RULES) guard node — otherwise it would bypass the orchestrate enforcement path. Catches
    a future module built via make_standard_module(login_rules=False) or hand-rolled without a guard."""
    import api.app  # noqa: F401  (registers all modules)
    from core.orchestration.registry import registry
    from core.orchestration.node import NodeKind

    PUBLIC_ALLOWLIST: set[str] = set()  # intentionally guardless modules (none today)
    missing = []
    for name in registry.list():
        if name in PUBLIC_ALLOWLIST:
            continue
        mod = registry.get(name)
        if not any(n.kind == NodeKind.RULES for n in mod.nodes.values()):
            missing.append(name)
    assert not missing, f"modules mounted WITHOUT an auth/RULES guard node: {missing}"


def test_orchestrate_does_not_forward_client_role_headers():
    """R6: the fetch node must not forward client-supplied X-Account-Roles (a forgeable role
    claim). Identity travels via the verified cookie/JWT only. (X-Feature-Tags may still be read
    for debug logging, but must not be propagated as an authz input.)"""
    import pathlib
    import nodes.orchestrated_fetch_node as ofn
    src = pathlib.Path(ofn.__file__).read_text()
    assert "roles_hdr" not in src                       # the role-forwarding var is gone
    assert 'fwd_headers["X-Account-Roles"]' not in src  # not injected into forwarded headers
