"""Provider-neutral storage ports — the seams the app's persistence is funneled through.

Three small interfaces, each abstracting one thing the app does today in a provider-specific,
scattered way:

* :class:`RecordStore`   — structured instance records (today: ``nouns/<type>/items.jsonl`` on
  local disk, or SQLite/Postgres in RDS mode). The eventual unified-SQL ``instances`` table is
  just another ``RecordStore`` implementation.
* :class:`ObjectStore`   — opaque blobs / large files by reference (today: ``nouns/<type>/images``
  on disk, or S3). "Large files → object store by ref" means a record holds a key, not the bytes.
* :class:`SecretProvider`— secrets (today: ``GIMS_JWT_SECRET`` env / ``.dev_jwt_secret`` file).

These live in ``core/`` and import NO cloud SDK (the layering guard enforces it). The ``local``
adapter (filesystem-backed, atomic) lives beside them; the ``aws`` adapter lives under ``api/``
(where boto3 is allowed) and is added when the destructive cutover is built. Pure interfaces +
local adapter = additive: nothing is wired into the running app yet.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional


class StorageError(Exception):
    """Raised by adapters for unrecoverable storage faults (distinct from 'not found')."""


class RecordStore(ABC):
    """CRUD over structured records, grouped into named *collections* (e.g. one noun type).

    A record is a plain ``dict``. Each collection has a primary-key field name supplied per call
    (the noun's ``primary_id_field``), so the store never needs the schema — it just keys on it.
    """

    @abstractmethod
    def list_records(self, collection: str) -> List[Dict[str, Any]]:
        """All records in the collection (empty list if the collection does not exist)."""

    @abstractmethod
    def get_record(self, collection: str, key_field: str, key: Any) -> Optional[Dict[str, Any]]:
        """The record whose ``key_field`` equals ``key`` (str-compared), or ``None``."""

    @abstractmethod
    def put_record(self, collection: str, key_field: str, record: Dict[str, Any]) -> None:
        """Insert or replace ``record`` by its ``key_field`` value (atomic)."""

    @abstractmethod
    def delete_record(self, collection: str, key_field: str, key: Any) -> bool:
        """Delete by key; return True if a record was removed."""

    @contextmanager
    def transaction(self) -> Iterator["RecordStore"]:
        """A unit of work (R4): apply several put/delete ops atomically — commit on a clean
        exit, roll back on exception — so a multi-record write (e.g. grid_save "replace this
        run's rows") never leaves the store half-updated::

            with store.transaction() as txn:
                txn.delete_record(coll, pid, old_key)
                txn.put_record(coll, pid, new_record)

        The yielded object exposes the same record methods as the store. This **default** is a
        passthrough (each op auto-commits, no rollback) for backends that cannot span statements;
        the SQL store overrides it with a real single-connection transaction. Callers should still
        treat the yielded object as the store for the duration of the ``with``.
        """
        yield self


class ObjectStore(ABC):
    """Opaque blob storage keyed by string. Records reference objects by key, not by value."""

    @abstractmethod
    def put_object(self, key: str, data: bytes) -> str:
        """Store ``data`` under ``key``; return the canonical reference (the key)."""

    @abstractmethod
    def get_object(self, key: str) -> bytes:
        """Return the bytes for ``key``; raise :class:`StorageError` if absent."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def delete_object(self, key: str) -> bool:
        """Delete by key; return True if an object was removed."""


class SecretProvider(ABC):
    """Read-only access to named secrets (signing keys, credentials)."""

    @abstractmethod
    def get_secret(self, name: str) -> Optional[str]:
        """Return the secret value for ``name``, or ``None`` if unset."""
