"""Provider-neutral storage layer (Phase 5).

Ports (``core/`` — no cloud SDK) + the local filesystem adapter. The ``aws`` adapters live under
``api/`` (boto3 allowed) and a SQL ``RecordStore`` arrive with the destructive cutover.
"""
from core.storage.ports import (
    ObjectStore,
    RecordStore,
    SecretProvider,
    StorageError,
)
from core.storage.local import (
    LocalObjectStore,
    LocalRecordStore,
    LocalSecretProvider,
)
from core.storage.sql import SqlRecordStore
from core.storage.factory import (
    StorageProvider,
    UnknownStorageProvider,
    collection_for_noun,
    get_archive_record_store,
    get_object_store,
    get_record_store,
    get_secret_provider,
    register_provider,
)

__all__ = [
    "ObjectStore", "RecordStore", "SecretProvider", "StorageError",
    "LocalObjectStore", "LocalRecordStore", "LocalSecretProvider",
    "SqlRecordStore",
    "collection_for_noun", "get_record_store", "get_archive_record_store",
    "get_object_store", "get_secret_provider",
    "register_provider", "StorageProvider", "UnknownStorageProvider",
]
