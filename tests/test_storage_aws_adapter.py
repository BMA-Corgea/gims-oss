"""Phase 5 — aws storage adapters (additive; reachable only in aws/RDS mode).

Without AWS credentials we can still prove: the module imports, the three classes implement the
ports, construction is LAZY (touches no AWS), the factory routes to them when the env flags are
set, and the canonical DSN normalizer is correct. Live Postgres/S3 behavior is out of scope here.
"""
import pytest

from api import storage_aws as aws
from core.storage import factory
from core.storage.ports import ObjectStore, RecordStore, SecretProvider


def test_adapters_implement_the_ports():
    assert issubclass(aws.PgRecordStore, RecordStore)
    assert issubclass(aws.S3ObjectStore, ObjectStore)
    assert issubclass(aws.SecretsManagerSecretProvider, SecretProvider)


def test_pg_record_store_construction_is_lazy(tmp_path):
    # Constructing must NOT resolve a DSN or open a connection (no AWS creds in CI/local).
    store = aws.PgRecordStore(tmp_path)
    assert store._dsn is None and store._schema_ready is False


def test_normalize_pg_dsn_canonicalizes_sqlalchemy_uris():
    assert (
        aws.normalize_pg_dsn("postgresql+asyncpg://u:p@h:5432/db?ssl=require")
        == "postgresql://u:p@h:5432/db?sslmode=require"
    )
    # plain DSN passes through unchanged
    assert aws.normalize_pg_dsn("postgresql://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"


def test_factory_routes_to_aws_when_flags_set(tmp_path, monkeypatch):
    monkeypatch.setenv("GIMS_RDS_ENABLED", "true")
    assert isinstance(factory.get_record_store(tmp_path), aws.PgRecordStore)

    monkeypatch.setenv("GIMS_STORAGE_PROVIDER", "aws")
    assert isinstance(factory.get_object_store(tmp_path), aws.S3ObjectStore)
    assert isinstance(factory.get_secret_provider(), aws.SecretsManagerSecretProvider)
