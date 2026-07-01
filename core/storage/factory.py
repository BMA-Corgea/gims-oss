"""Provider-selecting factory for the storage ports — the one place the app asks for a store.

Selects a concrete adapter from configuration (``utils.config``) so the rest of the app depends
only on the neutral :mod:`core.storage.ports` interfaces:

* :func:`get_record_store`  — structured instance records. Local (default): the unified-SQL
  ``instances`` table inside ``projects/<project>/objects.db`` (:class:`SqlRecordStore`). RDS
  mode (``GIMS_RDS_ENABLED=true``): the Postgres/JSONB adapter under ``api/`` (lazy-imported).
* :func:`get_object_store`  — large blobs / files by reference. Local: filesystem; ``aws``: S3.
* :func:`get_secret_provider`— signing keys / credentials. Local: env+file; ``aws``: Secrets Manager.

PROVIDER REGISTRY (Phase 5 follow-up — "not AWS-specific"): backends are not a hardcoded
``if provider == …`` ladder; each registers a :class:`StorageProvider` (three port factories) via
:func:`register_provider`. The built-ins ``local`` and ``aws`` self-register at import; third
parties self-register through the ``gims.storage_providers`` entry-point group (discovered lazily
on the first lookup miss). Adding MinIO/GCS/Azure Blob is then a new adapter + one
``register_provider`` call — no edit to this factory. An unselectable name fails with a clear
:class:`UnknownStorageProvider` listing what *is* registered.

* :func:`get_object_store` / :func:`get_secret_provider` select by ``storage_provider()``.
* :func:`get_record_store` / :func:`get_archive_record_store` select on the *orthogonal*
  ``rds_enabled()`` axis (local SQLite vs cloud Postgres) — you can pair local objects with a
  cloud record DB — so they map that boolean to the ``local`` / ``aws`` built-ins. Custom
  providers therefore contribute the object/secret stores; record storage stays that binary.

LAYERING: the ``aws`` adapters live under ``api/`` (where boto3 is allowed) and are imported
**lazily, inside the registered factory callables' bodies** — never at module scope, and never at
registration time — so ``import core.storage.factory`` (which runs the built-in registrations)
pulls no cloud SDK into ``core`` (the layering guard enforces it).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict

from utils.config import rds_enabled, storage_provider
from utils.logger import get_logger
from core.storage.ports import ObjectStore, RecordStore, SecretProvider
from core.storage.local import LocalObjectStore, LocalSecretProvider
from core.storage.sql import SqlRecordStore

log = get_logger(__name__)


class UnknownStorageProvider(ValueError):
    """Raised when a configured/selected storage provider name has no registered adapter."""


@dataclass(frozen=True)
class StorageProvider:
    """A registered storage backend: one factory per port.

    ``record_store`` takes ``(project_path, *, archive=False)`` so a single provider serves both
    the hot ``instances`` store and its archive variant; ``object_store`` takes ``project_path``;
    ``secret_provider`` takes no args. Cloud backends MUST do their SDK import INSIDE these
    callables (never at module scope, never at registration time) to keep core cloud-SDK-free.
    """
    record_store: Callable[..., RecordStore]
    object_store: Callable[[Path], ObjectStore]
    secret_provider: Callable[[], SecretProvider]


_PROVIDERS: Dict[str, StorageProvider] = {}
_ENTRY_POINTS_DISCOVERED = False


def register_provider(
    name: str,
    *,
    record_store: Callable[..., RecordStore],
    object_store: Callable[[Path], ObjectStore],
    secret_provider: Callable[[], SecretProvider],
) -> None:
    """Register (or replace) the storage backend selectable as ``name``.

    Called by the built-ins below at import, and by third-party packages — typically from a
    zero-arg hook exposed under the ``gims.storage_providers`` entry-point group. Keep any cloud
    SDK import inside the three factory callables, not at call time, to preserve the layering guard.
    """
    _PROVIDERS[name] = StorageProvider(
        record_store=record_store, object_store=object_store, secret_provider=secret_provider
    )


def _discover_entry_point_providers() -> None:
    """Load third-party providers advertised under the ``gims.storage_providers`` entry-point
    group, once, on the first lookup miss. Each entry point is a zero-arg callable that calls
    :func:`register_provider`. Best-effort: a broken provider is logged, not fatal."""
    global _ENTRY_POINTS_DISCOVERED
    if _ENTRY_POINTS_DISCOVERED:
        return
    _ENTRY_POINTS_DISCOVERED = True
    try:
        from importlib.metadata import entry_points  # noqa: PLC0415
        eps = entry_points(group="gims.storage_providers")
    except Exception:
        log.warning("storage: entry-point discovery failed", exc_info=True)
        return
    for ep in eps:
        try:
            ep.load()()
        except Exception:
            log.warning("storage: provider entry point %r failed to register", ep.name, exc_info=True)


def _get_provider(name: str) -> StorageProvider:
    provider = _PROVIDERS.get(name)
    if provider is None:
        _discover_entry_point_providers()
        provider = _PROVIDERS.get(name)
    if provider is None:
        raise UnknownStorageProvider(
            f"unknown storage provider {name!r} (registered: {sorted(_PROVIDERS)})"
        )
    return provider


def _record_provider() -> StorageProvider:
    """The provider that owns record storage, on the ``rds_enabled()`` axis (cloud Postgres vs
    local SQLite) — independent of ``storage_provider()`` so local objects can pair with a cloud DB."""
    return _get_provider("aws" if rds_enabled() else "local")


def collection_for_noun(noun_type: str) -> str:
    """Canonical identity of a noun collection in the unified ``instances`` table.

    THE single definition shared by the migrator (``tools/migrate_records``) and the app's
    read/write seam, so they can never disagree on where a noun's rows live.

    The unified table keys on ``(collection, key)`` with ``collection`` a plain TEXT column, so —
    unlike the legacy per-noun SQL tables that needed SQL-safe names and *diverged* between the
    writer (``noun_workbench_gui._sanitize_table_name``: ``\\W -> _`` **plus** a ``T_`` prefix for
    digit-leading names, e.g. ``noun_T_11111111122222222``) and the reader
    (``i_o.get_noun_items``: ``re.sub(\\W+, _)`` with **no** ``T_`` prefix, e.g.
    ``noun_11111111122222222`` — a real split-brain for digit-leading nouns) — the collection is
    simply the ``noun_type`` exactly as it appears in ``*_types.json`` and the ``nouns/<type>/``
    folder. No sanitization, so no two-sanitizer drift and no split-brain.
    """
    return str(noun_type)


def _objects_db_path(project_path: Path) -> Path:
    """Local SQLite file backing the unified ``instances`` table for a project.

    Mirrors the ``object_sql_db`` layout key (``RDS+objects.db`` -> ``objects.db`` under the
    project) without importing the ``api`` resolver into ``core``. Only used on the local /
    RDS-disabled path; in RDS mode :func:`get_record_store` routes to Postgres instead.
    """
    return Path(project_path) / "objects.db"


def get_record_store(project_path: Path) -> RecordStore:
    """The RecordStore for a project's instance records.

    RDS enabled -> Postgres/JSONB adapter (``api/``, lazy). Otherwise -> the unified-SQL
    ``instances`` table in the project's local ``objects.db``.
    """
    return _record_provider().record_store(project_path)


def _archive_db_path(project_path: Path) -> Path:
    """Local SQLite file backing the unified ``instances`` table for ARCHIVED records.

    A separate ``archive.db`` sibling of ``objects.db`` (Phase 6/R17): hard-archived noun
    records move here, keeping the hot store lean while staying queryable for restore.
    """
    return Path(project_path) / "archive.db"


def get_archive_record_store(project_path: Path) -> RecordStore:
    """The RecordStore for a project's ARCHIVED instance records (Phase 6/R17).

    A separate store with the same ``instances`` schema as :func:`get_record_store`:
    * local / RDS-disabled -> the project's local ``archive.db``.
    * RDS enabled -> the Postgres adapter pointed at a dedicated ``instances_archive`` table in
      the same database (the cloud analogue of ``archive.db``).
    """
    return _record_provider().record_store(project_path, archive=True)


def get_object_store(project_path: Path) -> ObjectStore:
    """The ObjectStore for a project's large files / blobs (referenced by key, not inlined)."""
    return _get_provider(storage_provider()).object_store(Path(project_path))


def get_secret_provider() -> SecretProvider:
    """The SecretProvider for signing keys / credentials."""
    return _get_provider(storage_provider()).secret_provider()


# ── Built-in providers ──────────────────────────────────────────────────────────────────────
# Each cloud import lives INSIDE the callable body (never at module/registration scope) so
# importing this module — which runs the registrations below — drags no cloud SDK into core.

def _local_record_store(project_path: Path, *, archive: bool = False) -> RecordStore:
    db = _archive_db_path(project_path) if archive else _objects_db_path(project_path)
    return SqlRecordStore(db)


def _local_object_store(project_path: Path) -> ObjectStore:
    return LocalObjectStore(Path(project_path))


def _local_secret_provider() -> SecretProvider:
    from utils.paths import repo_root  # noqa: PLC0415 — local default reads env then repo-root file
    return LocalSecretProvider(repo_root())


def _aws_record_store(project_path: Path, *, archive: bool = False) -> RecordStore:
    from api.storage_aws import PgRecordStore  # noqa: PLC0415 — lazy, see module docstring
    return PgRecordStore(project_path, table="instances_archive") if archive else PgRecordStore(project_path)


def _aws_object_store(project_path: Path) -> ObjectStore:
    from api.storage_aws import S3ObjectStore  # noqa: PLC0415
    return S3ObjectStore(project_path)


def _aws_secret_provider() -> SecretProvider:
    from api.storage_aws import SecretsManagerSecretProvider  # noqa: PLC0415
    return SecretsManagerSecretProvider()


register_provider(
    "local",
    record_store=_local_record_store,
    object_store=_local_object_store,
    secret_provider=_local_secret_provider,
)
register_provider(
    "aws",
    record_store=_aws_record_store,
    object_store=_aws_object_store,
    secret_provider=_aws_secret_provider,
)
