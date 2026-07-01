"""Local filesystem adapters for the storage ports — the default provider.

Faithful, atomic, boto3-free implementations of the three ports over local disk:

* :class:`LocalRecordStore`   — one JSONL file per collection (``<root>/<collection>/items.jsonl``),
  matching today's ``nouns/<type>/items.jsonl`` layout. Reads/writes are lock-guarded + atomic.
* :class:`LocalObjectStore`   — blobs under ``<root>/<key>`` (atomic writes).
* :class:`LocalSecretProvider`— env (``GIMS_<NAME>``) then a secrets-dir file fallback.

Additive: these are not yet wired into the running app. The ``aws`` adapters (under ``api/``,
where boto3 is allowed) and a SQL ``RecordStore`` come with the destructive cutover.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.atomic import atomic_write_bytes, atomic_write_text, file_lock
from core.storage.ports import ObjectStore, RecordStore, SecretProvider, StorageError


class LocalRecordStore(RecordStore):
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, collection: str) -> Path:
        return self.root / collection / "items.jsonl"

    def _read_all(self, collection: str) -> List[Dict[str, Any]]:
        path = self._path(collection)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as e:
            raise StorageError(f"read failed: {path}: {e!r}")
        out: List[Dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate a stray bad line, like the legacy readers
        return out

    def _write_all(self, collection: str, records: List[Dict[str, Any]]) -> None:
        path = self._path(collection)
        payload = "".join(json.dumps(r) + "\n" for r in records)
        with file_lock(path):
            atomic_write_text(path, payload)

    def list_records(self, collection: str) -> List[Dict[str, Any]]:
        return self._read_all(collection)

    def get_record(self, collection: str, key_field: str, key: Any) -> Optional[Dict[str, Any]]:
        skey = str(key)
        for rec in self._read_all(collection):
            if str(rec.get(key_field)) == skey:
                return rec
        return None

    def put_record(self, collection: str, key_field: str, record: Dict[str, Any]) -> None:
        skey = str(record.get(key_field))
        path = self._path(collection)
        with file_lock(path):
            records = self._read_all(collection)
            replaced = False
            for i, rec in enumerate(records):
                if str(rec.get(key_field)) == skey:
                    records[i] = record
                    replaced = True
                    break
            if not replaced:
                records.append(record)
            atomic_write_text(path, "".join(json.dumps(r) + "\n" for r in records))

    def delete_record(self, collection: str, key_field: str, key: Any) -> bool:
        skey = str(key)
        path = self._path(collection)
        with file_lock(path):
            records = self._read_all(collection)
            kept = [r for r in records if str(r.get(key_field)) != skey]
            if len(kept) == len(records):
                return False
            atomic_write_text(path, "".join(json.dumps(r) + "\n" for r in kept))
            return True


class LocalObjectStore(ObjectStore):
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        # Guard against path traversal escaping the root.
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise StorageError(f"object key escapes root: {key!r}")
        return p

    def put_object(self, key: str, data: bytes) -> str:
        atomic_write_bytes(self._path(key), data)
        return key

    def get_object(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise StorageError(f"object not found: {key!r}")
        except OSError as e:
            raise StorageError(f"read failed: {key!r}: {e!r}")

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete_object(self, key: str) -> bool:
        p = self._path(key)
        try:
            p.unlink()
            return True
        except FileNotFoundError:
            return False


class LocalSecretProvider(SecretProvider):
    """Env (``GIMS_<NAME>``, name upper-cased + non-alnum -> ``_``) then a secrets-dir file."""

    def __init__(self, secrets_dir: Optional[Path] = None):
        self.secrets_dir = Path(secrets_dir) if secrets_dir else None

    @staticmethod
    def _env_key(name: str) -> str:
        return "GIMS_" + "".join(c if c.isalnum() else "_" for c in name).upper()

    def get_secret(self, name: str) -> Optional[str]:
        env = os.environ.get(self._env_key(name))
        if env:
            return env
        if self.secrets_dir:
            f = self.secrets_dir / name
            try:
                txt = f.read_text(encoding="utf-8").strip()
                if txt:
                    return txt
            except (FileNotFoundError, OSError):
                pass
        return None
