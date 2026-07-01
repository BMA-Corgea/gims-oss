"""SQLite-backed :class:`RecordStore` — the unified ``instances`` table.

ONE table holds every part-of-speech instance across all collections, instead of a JSONL file per
noun type:

    instances(collection TEXT, key TEXT, data TEXT JSON, PRIMARY KEY(collection, key))

``collection`` is the noun type, ``key`` is the record's primary-id value, ``data`` is the full
record as JSON (the SQLite analogue of the Postgres JSONB column the cloud adapter will use).
This is the local SQL backing; the Postgres adapter (psycopg) is an ``api/`` adapter added at
cutover. ``sqlite3`` is stdlib, so ``core/`` stays free of cloud/db-driver deps.

Faithful to the :class:`RecordStore` port: ``put_record`` is an upsert keyed on ``record[key_field]``;
``get``/``delete`` key on the stored primary-id value. Writes are single-statement (atomic in
SQLite); a process-level connection is opened per call for simplicity and isolation.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from core.storage.ports import RecordStore, StorageError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    collection TEXT NOT NULL,
    key        TEXT NOT NULL,
    data       TEXT NOT NULL,
    PRIMARY KEY (collection, key)
)
"""


class SqlRecordStore(RecordStore):
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        # timeout=30 + busy_timeout so a concurrent writer (the R4 transaction holds the
        # connection across several statements) WAITS rather than erroring "database is locked".
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    def _ensure_schema(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(_SCHEMA)
        except sqlite3.Error as e:
            raise StorageError(f"schema init failed: {e!r}")

    def list_records(self, collection: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM instances WHERE collection = ?", (collection,)
            ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def get_record(self, collection: str, key_field: str, key: Any) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM instances WHERE collection = ? AND key = ?",
                (collection, str(key)),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def put_record(self, collection: str, key_field: str, record: Dict[str, Any]) -> None:
        key = record.get(key_field)
        if key is None:
            raise StorageError(f"record has no primary key field {key_field!r}")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO instances (collection, key, data) VALUES (?, ?, ?)",
                (collection, str(key), json.dumps(record)),
            )

    def delete_record(self, collection: str, key_field: str, key: Any) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM instances WHERE collection = ? AND key = ?",
                (collection, str(key)),
            )
            return cur.rowcount > 0

    @contextmanager
    def transaction(self) -> Iterator["_SqlTransaction"]:
        """Real unit of work (R4): one connection, deferred commit. Every put/delete on the
        yielded handle runs on the same connection inside one ``BEGIN``; commit on a clean exit,
        roll back on any exception. Each call uses its OWN connection, so it is safe under
        concurrent requests."""
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            yield _SqlTransaction(conn)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    # ---- migration / verification helpers (not part of the port) ----
    def count(self, collection: Optional[str] = None) -> int:
        with self._connect() as conn:
            if collection is None:
                return conn.execute("SELECT COUNT(*) FROM instances").fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM instances WHERE collection = ?", (collection,)
            ).fetchone()[0]

    def collections(self) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT collection FROM instances ORDER BY collection"
            ).fetchall()
        return [r["collection"] for r in rows]


class _SqlTransaction(RecordStore):
    """The unit-of-work handle yielded by :meth:`SqlRecordStore.transaction`. Mirrors the
    store's record API but runs every statement on ONE held connection without committing —
    the surrounding ``transaction()`` context commits or rolls back the whole batch."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def list_records(self, collection: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT data FROM instances WHERE collection = ?", (collection,)
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def get_record(self, collection: str, key_field: str, key: Any) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT data FROM instances WHERE collection = ? AND key = ?",
            (collection, str(key)),
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def put_record(self, collection: str, key_field: str, record: Dict[str, Any]) -> None:
        key = record.get(key_field)
        if key is None:
            raise StorageError(f"record has no primary key field {key_field!r}")
        self._conn.execute(
            "INSERT OR REPLACE INTO instances (collection, key, data) VALUES (?, ?, ?)",
            (collection, str(key), json.dumps(record)),
        )

    def delete_record(self, collection: str, key_field: str, key: Any) -> bool:
        cur = self._conn.execute(
            "DELETE FROM instances WHERE collection = ? AND key = ?",
            (collection, str(key)),
        )
        return cur.rowcount > 0
