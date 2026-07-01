"""Phase 5: the unified-SQL RecordStore + the JSONL->SQL migrator (dry-run + verified apply).
Plus R4: the RecordStore unit-of-work (transaction) — commit-together / rollback-together."""
import json

import pytest

from core.storage.sql import SqlRecordStore
from tools import migrate_records


def test_sql_record_store_is_a_faithful_record_store(tmp_path):
    s = SqlRecordStore(tmp_path / "instances.db")
    assert s.list_records("Sample") == []
    s.put_record("Sample", "id", {"id": "S-1", "v": 1})
    s.put_record("Sample", "id", {"id": "S-2", "v": 2})
    s.put_record("Submission", "sid", {"sid": "Sub-1"})
    assert {r["id"] for r in s.list_records("Sample")} == {"S-1", "S-2"}
    assert s.get_record("Sample", "id", "S-1") == {"id": "S-1", "v": 1}
    assert s.count() == 3 and s.count("Sample") == 2
    assert s.collections() == ["Sample", "Submission"]


def test_sql_put_is_upsert_on_primary_key(tmp_path):
    s = SqlRecordStore(tmp_path / "i.db")
    s.put_record("N", "id", {"id": "A", "v": 1})
    s.put_record("N", "id", {"id": "A", "v": 2})
    assert s.count("N") == 1
    assert s.get_record("N", "id", "A")["v"] == 2


def test_sql_delete(tmp_path):
    s = SqlRecordStore(tmp_path / "i.db")
    s.put_record("N", "id", {"id": "A"})
    assert s.delete_record("N", "id", "A") is True
    assert s.delete_record("N", "id", "A") is False


def test_transaction_commits_on_success(tmp_path):
    s = SqlRecordStore(tmp_path / "i.db")
    s.put_record("N", "id", {"id": "old", "v": 0})
    with s.transaction() as txn:
        txn.delete_record("N", "id", "old")
        txn.put_record("N", "id", {"id": "A", "v": 1})
        txn.put_record("N", "id", {"id": "B", "v": 2})
        # the in-transaction handle sees its own uncommitted changes
        assert {r["id"] for r in txn.list_records("N")} == {"A", "B"}
    # committed
    assert {r["id"] for r in s.list_records("N")} == {"A", "B"}


def test_transaction_rolls_back_on_exception(tmp_path):
    s = SqlRecordStore(tmp_path / "i.db")
    s.put_record("N", "id", {"id": "keep", "v": 1})
    with pytest.raises(RuntimeError):
        with s.transaction() as txn:
            txn.put_record("N", "id", {"id": "new", "v": 2})
            txn.delete_record("N", "id", "keep")
            raise RuntimeError("boom mid-update")
    # nothing changed: the new row was not committed and the deleted row survives
    assert {r["id"] for r in s.list_records("N")} == {"keep"}
    assert s.get_record("N", "id", "new") is None


def test_default_transaction_passthrough(tmp_path):
    # The base RecordStore.transaction() default yields the store itself (non-atomic,
    # but a valid context manager) so non-SQL adapters keep working.
    from core.storage.local import LocalRecordStore
    s = LocalRecordStore(tmp_path / "local")
    with s.transaction() as txn:
        txn.put_record("N", "id", {"id": "X"})
    assert s.get_record("N", "id", "X") == {"id": "X"}


def _fake_project(root, monkeypatch):
    proj = root / "Proj"
    (proj / "nouns" / "Sample").mkdir(parents=True)
    (proj / "noun_types.json").write_text(json.dumps({
        "Sample": {"primary_id_field": "sample_id", "fields": {}},
    }))
    (proj / "nouns" / "Sample" / "items.jsonl").write_text(
        '{"sample_id": "S-1", "m": "a"}\n{"sample_id": "S-2", "m": "b"}\n{"m": "keyless"}\n')
    monkeypatch.setattr(migrate_records, "projects_dir", lambda: root)
    return proj


def test_migrator_dry_run_reports_plan(tmp_path, monkeypatch, capsys):
    _fake_project(tmp_path, monkeypatch)
    rc = migrate_records.main([])  # dry-run, all projects
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN" in out
    assert "Sample: write 2" in out and "keyless" in out  # 2 keyed migrated; 1 keyless reported+skipped


def test_migrator_apply_rowcount_verifies(tmp_path, monkeypatch):
    _fake_project(tmp_path, monkeypatch)
    db = tmp_path / "out.db"
    rc = migrate_records.main(["--apply", "--db", str(db)])
    assert rc == 0  # all collections verified
    s = SqlRecordStore(db)
    # the keyless row is skipped; the two keyed rows land and verify
    assert s.count("Sample") == 2
    assert s.get_record("Sample", "sample_id", "S-1")["m"] == "a"
