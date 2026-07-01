"""Phase 5 foundation: the local filesystem adapters for the storage ports."""
import pytest

from core.storage import (
    LocalObjectStore, LocalRecordStore, LocalSecretProvider, StorageError,
)


# ── RecordStore ────────────────────────────────────────────────────────────────
def test_record_store_put_get_list_delete(tmp_path):
    rs = LocalRecordStore(tmp_path)
    assert rs.list_records("Sample") == []            # missing collection -> empty
    rs.put_record("Sample", "id", {"id": "S-1", "v": 1})
    rs.put_record("Sample", "id", {"id": "S-2", "v": 2})
    assert {r["id"] for r in rs.list_records("Sample")} == {"S-1", "S-2"}
    assert rs.get_record("Sample", "id", "S-1") == {"id": "S-1", "v": 1}
    assert rs.get_record("Sample", "id", "nope") is None


def test_record_store_put_is_upsert(tmp_path):
    rs = LocalRecordStore(tmp_path)
    rs.put_record("Sample", "id", {"id": "S-1", "v": 1})
    rs.put_record("Sample", "id", {"id": "S-1", "v": 99})  # replace, not append
    recs = rs.list_records("Sample")
    assert len(recs) == 1 and recs[0]["v"] == 99


def test_record_store_delete(tmp_path):
    rs = LocalRecordStore(tmp_path)
    rs.put_record("Sample", "id", {"id": "S-1"})
    assert rs.delete_record("Sample", "id", "S-1") is True
    assert rs.delete_record("Sample", "id", "S-1") is False
    assert rs.list_records("Sample") == []


def test_record_store_key_is_string_compared(tmp_path):
    rs = LocalRecordStore(tmp_path)
    rs.put_record("N", "id", {"id": 7})
    assert rs.get_record("N", "id", "7") == {"id": 7}   # int stored, str queried


# ── ObjectStore ──────────────────────────────────────────────────────────────
def test_object_store_roundtrip(tmp_path):
    os_ = LocalObjectStore(tmp_path)
    ref = os_.put_object("images/a.bin", b"\x00\x01hello")
    assert ref == "images/a.bin"
    assert os_.exists("images/a.bin")
    assert os_.get_object("images/a.bin") == b"\x00\x01hello"
    assert os_.delete_object("images/a.bin") is True
    assert not os_.exists("images/a.bin")


def test_object_store_missing_raises(tmp_path):
    with pytest.raises(StorageError):
        LocalObjectStore(tmp_path).get_object("ghost")


def test_object_store_blocks_traversal(tmp_path):
    with pytest.raises(StorageError):
        LocalObjectStore(tmp_path).put_object("../escape", b"x")


# ── SecretProvider ─────────────────────────────────────────────────────────────
def test_secret_provider_env_then_file(tmp_path, monkeypatch):
    sp = LocalSecretProvider(secrets_dir=tmp_path)
    assert sp.get_secret("jwt") is None
    (tmp_path / "jwt").write_text("from-file")
    assert sp.get_secret("jwt") == "from-file"
    monkeypatch.setenv("GIMS_JWT", "from-env")
    assert sp.get_secret("jwt") == "from-env"           # env wins over file
