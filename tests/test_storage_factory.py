"""Phase 5 — provider factory + registry.

Locks the local-default selection, the single canonical collection identity (so the migrator and
the read/write seam can never disagree), the layering invariant that asking core for a store never
drags a cloud SDK into core, and the pluggable provider registry (register_provider / dispatch /
clear unknown-provider error).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.storage import factory
from core.storage.factory import StorageProvider, UnknownStorageProvider, register_provider
from core.storage.local import LocalObjectStore, LocalSecretProvider
from core.storage.sql import SqlRecordStore


def test_local_default_record_store_is_unified_sql(tmp_path, monkeypatch):
    monkeypatch.delenv("GIMS_STORAGE_PROVIDER", raising=False)
    monkeypatch.delenv("GIMS_RDS_ENABLED", raising=False)
    store = factory.get_record_store(tmp_path)
    assert isinstance(store, SqlRecordStore)
    # backed by the project's objects.db (where the legacy verb_log + per-noun tables already live)
    assert store.db_path == tmp_path / "objects.db"


def test_local_default_object_and_secret_stores(tmp_path, monkeypatch):
    monkeypatch.delenv("GIMS_STORAGE_PROVIDER", raising=False)
    assert isinstance(factory.get_object_store(tmp_path), LocalObjectStore)
    assert isinstance(factory.get_secret_provider(), LocalSecretProvider)


def test_unregistered_providers_error_clearly(tmp_path, monkeypatch):
    # azure/gcp ship no adapter -> they are simply unregistered, and selecting one fails with a
    # clear UnknownStorageProvider that names what IS registered (not a bare NotImplementedError).
    for provider in ("azure", "gcp"):
        monkeypatch.setenv("GIMS_STORAGE_PROVIDER", provider)
        with pytest.raises(UnknownStorageProvider) as ei:
            factory.get_object_store(tmp_path)
        assert provider in str(ei.value) and "local" in str(ei.value)
        with pytest.raises(UnknownStorageProvider):
            factory.get_secret_provider()


def test_register_provider_dispatch(tmp_path, monkeypatch):
    """A freshly registered provider is dispatched to by storage_provider() for object+secret."""
    sentinel_obj = object()
    sentinel_secret = object()
    register_provider(
        "fakeprov",
        record_store=lambda p, *, archive=False: SqlRecordStore(tmp_path / "x.db"),
        object_store=lambda p: sentinel_obj,
        secret_provider=lambda: sentinel_secret,
    )
    try:
        monkeypatch.setenv("GIMS_STORAGE_PROVIDER", "faKEProv".lower())
        assert factory.get_object_store(tmp_path) is sentinel_obj
        assert factory.get_secret_provider() is sentinel_secret
    finally:
        factory._PROVIDERS.pop("fakeprov", None)


def test_unknown_provider_lists_registered(tmp_path, monkeypatch):
    monkeypatch.setenv("GIMS_STORAGE_PROVIDER", "definitely-not-a-provider")
    with pytest.raises(UnknownStorageProvider) as ei:
        factory.get_object_store(tmp_path)
    msg = str(ei.value)
    assert "definitely-not-a-provider" in msg
    # built-ins are always registered and surfaced so the operator can see valid choices
    assert "aws" in msg and "local" in msg


def test_builtins_are_registered():
    assert {"local", "aws"} <= set(factory._PROVIDERS)
    assert all(isinstance(v, StorageProvider) for v in factory._PROVIDERS.values())


def test_entry_point_provider_is_discovered_on_miss(tmp_path, monkeypatch):
    """A third-party provider advertised under the `gims.storage_providers` entry-point group
    self-registers via its zero-arg hook the first time an unknown name is looked up."""
    import importlib.metadata as md

    class _FakeEP:
        name = "minio_demo"

        def load(self):
            def _hook():
                register_provider(
                    "minio_demo",
                    record_store=lambda p, *, archive=False: SqlRecordStore(tmp_path / "m.db"),
                    object_store=lambda p: ("obj", p),
                    secret_provider=lambda: "sec",
                )
            return _hook

    monkeypatch.setattr(
        md, "entry_points",
        lambda group=None: [_FakeEP()] if group == "gims.storage_providers" else [],
    )
    # discovery is once-only; reset the latch so this test triggers it
    monkeypatch.setattr(factory, "_ENTRY_POINTS_DISCOVERED", False)
    try:
        monkeypatch.setenv("GIMS_STORAGE_PROVIDER", "minio_demo")
        assert factory.get_object_store(tmp_path) == ("obj", tmp_path)
        assert factory.get_secret_provider() == "sec"
    finally:
        factory._PROVIDERS.pop("minio_demo", None)


@pytest.mark.parametrize(
    "noun_type",
    [
        "Sample",
        "Sample Type",          # space -> must NOT be sanitized
        "COA Name Map",         # multiple spaces
        "11111111122222222",    # digit-leading -> legacy writer used a T_ prefix; canonical does not
        "t55t5t5t5t5",
    ],
)
def test_collection_identity_is_the_raw_noun_type(noun_type):
    # The unified instances.collection is a TEXT column, so the canonical id is the noun_type
    # verbatim — no sanitizer, hence no writer/reader split-brain. Round-trips exactly.
    assert factory.collection_for_noun(noun_type) == noun_type


def test_importing_factory_does_not_import_boto3_or_psycopg():
    # boto3 AND psycopg are installed in this env, so a stray module-scope import in core would
    # actually pull them in. Import the factory in a CLEAN subprocess and assert neither leaked.
    code = (
        "import sys; import core.storage.factory; "
        "leaked=[m for m in ('boto3','botocore','psycopg','psycopg2') if m in sys.modules]; "
        "print(','.join(leaked)); sys.exit(1 if leaked else 0)"
    )
    repo_root = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"core.storage.factory leaked cloud SDKs at import: {proc.stdout.strip()!r}"
