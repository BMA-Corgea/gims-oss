"""AWS adapters for the storage ports — the cloud half of the provider-neutral seam.

Lives under ``api/`` because this is the ONE layer where ``boto3`` / ``psycopg`` are allowed
(the layering guard keeps them out of ``core/``). Selected only when ``GIMS_RDS_ENABLED=true``
(records -> Postgres) or ``GIMS_STORAGE_PROVIDER=aws`` (objects -> S3, secrets -> Secrets
Manager); under the local default these classes are never constructed.

* :class:`PgRecordStore`              — the unified ``instances`` table on Postgres/JSONB, the
  cloud twin of :class:`core.storage.sql.SqlRecordStore`. DSN + connection are resolved LAZILY
  (first query), so constructing the store touches no AWS — keeping it importable/constructible
  in tests with no credentials.
* :class:`S3ObjectStore`              — blobs in the bucket from ``s3_manifest.json``, via the
  existing :func:`api.manifest.s3_resolver_aws.get_s3_client`.
* :class:`SecretsManagerSecretProvider` — named secrets via AWS Secrets Manager.

``normalize_pg_dsn`` is the ONE canonical SQLAlchemy-URI -> psycopg-DSN normalizer (the cutover
collapses the copies duplicated across i_o/archive/backup onto this one).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from core.storage.ports import ObjectStore, RecordStore, SecretProvider, StorageError

_INSTANCES_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    collection TEXT NOT NULL,
    key        TEXT NOT NULL,
    data       JSONB NOT NULL,
    PRIMARY KEY (collection, key)
)
"""


def normalize_pg_dsn(url: str) -> str:
    """Canonical SQLAlchemy-style URI -> plain psycopg DSN (the single copy; mirrors the logic
    triplicated today in api/i_o.py + archive/backup)."""
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("postgresql+", 1)[1]
    if "?ssl=require" in url:
        url = url.replace("?ssl=require", "?sslmode=require")
    return url.replace("postgresql://asyncpg://", "postgresql://")


class PgRecordStore(RecordStore):
    """Unified ``instances`` table on Postgres/JSONB. DSN + connection resolved lazily.

    ``table`` selects the backing table (default ``instances``). The archive store passes
    ``instances_archive`` so hard-archived records live in a dedicated table in the same
    Postgres database — the cloud analogue of the local ``archive.db`` (Phase 6/R17). The name
    is an internal constant (never user input), so f-string interpolation is injection-safe.
    """

    def __init__(self, project_path: Path, table: str = "instances"):
        self.project_path = Path(project_path)
        self.table = table
        self._dsn: Optional[str] = None
        self._schema_ready = False

    def _resolve_dsn(self) -> str:
        if self._dsn is None:
            from api.manifest.resolver import get_db_uri  # lazy: avoids boto3 at construction
            uri = get_db_uri("object_sql_db")
            if not uri or not uri.startswith("postgresql"):
                raise StorageError(f"object_sql_db did not resolve to a Postgres DSN: {uri!r}")
            self._dsn = normalize_pg_dsn(uri)
        return self._dsn

    def _connect(self):
        import psycopg  # lazy
        conn = psycopg.connect(self._resolve_dsn())
        if not self._schema_ready:
            with conn.cursor() as cur:
                cur.execute(_INSTANCES_DDL.format(table=self.table))
            conn.commit()
            self._schema_ready = True
        return conn

    @staticmethod
    def _as_dict(cell: Any) -> Dict[str, Any]:
        return cell if isinstance(cell, dict) else json.loads(cell)

    def list_records(self, collection: str) -> List[Dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT data FROM {self.table} WHERE collection = %s", (collection,))
            return [self._as_dict(r[0]) for r in cur.fetchall()]

    def get_record(self, collection: str, key_field: str, key: Any) -> Optional[Dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT data FROM {self.table} WHERE collection = %s AND key = %s",
                (collection, str(key)),
            )
            row = cur.fetchone()
        return self._as_dict(row[0]) if row else None

    def put_record(self, collection: str, key_field: str, record: Dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb  # lazy
        key = record.get(key_field)
        if key is None:
            raise StorageError(f"record has no primary key field {key_field!r}")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self.table} (collection, key, data) VALUES (%s, %s, %s)
                    ON CONFLICT (collection, key) DO UPDATE SET data = EXCLUDED.data
                    """,
                    (collection, str(key), Jsonb(record)),
                )
            conn.commit()

    def delete_record(self, collection: str, key_field: str, key: Any) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {self.table} WHERE collection = %s AND key = %s",
                    (collection, str(key)),
                )
                removed = cur.rowcount > 0
            conn.commit()
        return removed

    @contextmanager
    def transaction(self) -> Iterator["_PgTransaction"]:
        """Real unit of work (R4) on the cloud path: one connection, deferred commit. Every
        put/delete on the yielded handle runs on the same connection; commit on a clean exit,
        roll back on any exception. Without this, ``grid_save``'s atomic update silently fell
        back to the non-atomic per-op-commit default in RDS mode."""
        conn = self._connect()  # also runs the one-time DDL+commit on a fresh connection
        try:
            yield _PgTransaction(conn, self.table)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ---- migration / verification helpers (not part of the port) ----
    def count(self, collection: Optional[str] = None) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            if collection is None:
                cur.execute(f"SELECT COUNT(*) FROM {self.table}")
            else:
                cur.execute(f"SELECT COUNT(*) FROM {self.table} WHERE collection = %s", (collection,))
            return cur.fetchone()[0]


class _PgTransaction(RecordStore):
    """Unit-of-work handle yielded by :meth:`PgRecordStore.transaction` — every statement runs
    on ONE held connection with no per-op commit; the surrounding ``transaction()`` commits or
    rolls back the whole batch (cloud analogue of ``_SqlTransaction``)."""

    def __init__(self, conn, table: str):
        self._conn = conn
        self._table = table

    def list_records(self, collection: str) -> List[Dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT data FROM {self._table} WHERE collection = %s", (collection,))
            return [PgRecordStore._as_dict(r[0]) for r in cur.fetchall()]

    def get_record(self, collection: str, key_field: str, key: Any) -> Optional[Dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT data FROM {self._table} WHERE collection = %s AND key = %s",
                (collection, str(key)),
            )
            row = cur.fetchone()
        return PgRecordStore._as_dict(row[0]) if row else None

    def put_record(self, collection: str, key_field: str, record: Dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb  # lazy
        key = record.get(key_field)
        if key is None:
            raise StorageError(f"record has no primary key field {key_field!r}")
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self._table} (collection, key, data) VALUES (%s, %s, %s)
                ON CONFLICT (collection, key) DO UPDATE SET data = EXCLUDED.data
                """,
                (collection, str(key), Jsonb(record)),
            )

    def delete_record(self, collection: str, key_field: str, key: Any) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {self._table} WHERE collection = %s AND key = %s",
                (collection, str(key)),
            )
            return cur.rowcount > 0


class S3ObjectStore(ObjectStore):
    """Blob storage in the bucket named by ``s3_manifest.json``. Client/bucket resolved lazily."""

    def __init__(self, project_path: Path):
        self.project_path = Path(project_path)
        self._client = None
        self._bucket: Optional[str] = None
        self._region: Optional[str] = None

    def _manifest(self) -> dict:
        manifest_path = Path(__file__).resolve().parent / "manifest" / "s3_manifest.json"
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError) as e:
            raise StorageError(f"s3_manifest.json unreadable: {e!r}")

    def _ensure(self):
        if self._client is None:
            from api.manifest.s3_resolver_aws import get_s3_client  # lazy
            m = self._manifest()
            self._bucket = m.get("bucket_name")
            self._region = m.get("region_name", "us-east-1")
            if not self._bucket:
                raise StorageError("s3_manifest.json missing 'bucket_name'")
            self._client = get_s3_client(self._region)
        return self._client, self._bucket

    def put_object(self, key: str, data: bytes) -> str:
        cli, bucket = self._ensure()
        cli.put_object(Bucket=bucket, Key=key, Body=data)
        return key

    def get_object(self, key: str) -> bytes:
        cli, bucket = self._ensure()
        try:
            return cli.get_object(Bucket=bucket, Key=key)["Body"].read()
        except Exception as e:  # botocore ClientError etc.
            raise StorageError(f"object not found: {key!r}: {e!r}")

    def exists(self, key: str) -> bool:
        cli, bucket = self._ensure()
        try:
            cli.head_object(Bucket=bucket, Key=key)
            return True
        except Exception:
            return False

    def delete_object(self, key: str) -> bool:
        cli, bucket = self._ensure()
        if not self.exists(key):
            return False
        cli.delete_object(Bucket=bucket, Key=key)
        return True


class SecretsManagerSecretProvider(SecretProvider):
    """Named secrets via AWS Secrets Manager. Region from ``rds_manifest.json`` or ``AWS_REGION``."""

    def __init__(self, region_name: Optional[str] = None):
        self._region = region_name
        self._client = None

    def _ensure(self):
        if self._client is None:
            import boto3  # lazy
            region = self._region
            if not region:
                import os
                manifest_path = Path(__file__).resolve().parent / "manifest" / "rds_manifest.json"
                try:
                    region = json.loads(manifest_path.read_text(encoding="utf-8")).get("region_name")
                except (FileNotFoundError, OSError, json.JSONDecodeError):
                    region = None
                region = region or os.environ.get("AWS_REGION", "us-east-1")
            self._region = region
            self._client = boto3.session.Session().client("secretsmanager", region_name=region)
        return self._client

    def get_secret(self, name: str) -> Optional[str]:
        cli = self._ensure()
        try:
            return cli.get_secret_value(SecretId=name).get("SecretString")
        except Exception:
            return None
