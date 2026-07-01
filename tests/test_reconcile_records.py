"""Phase 5 — the SQL<->JSONL reconciliation gate + the migrator safety interlock.

The migrator reads items.jsonl only, but the live app reads the per-noun SQL table first; on real
data those diverge at the field level. These tests pin the gate's three failure categories on a
synthetic project and prove `migrate_records --apply` refuses to run over divergence by default.
"""
import json
import sqlite3
from pathlib import Path

from tools import reconcile_records as rr
from tools import migrate_records as mr


def _make_project(root: Path) -> Path:
    proj = root / "DivergeProj"
    (proj / "nouns" / "Widget").mkdir(parents=True)
    (proj / "noun_types.json").write_text(json.dumps({"Widget": {"primary_id_field": "wid"}}))
    # JSONL: W1 (matches SQL), W2 (only-in-jsonl), W3 (differs from SQL on a user field)
    (proj / "nouns" / "Widget" / "items.jsonl").write_text(
        "\n".join(json.dumps(r) for r in [
            {"wid": "W1", "color": "red"},
            {"wid": "W2", "color": "green"},
            {"wid": "W3", "color": "blue"},
        ]) + "\n"
    )
    con = sqlite3.connect(str(proj / "objects.db"))
    con.execute('CREATE TABLE "noun_Widget" (wid TEXT, color TEXT)')
    con.executemany('INSERT INTO "noun_Widget" (wid,color) VALUES (?,?)', [
        ("W1", "red"),          # in sync with JSONL
        ("W3", "BLUE"),         # same key, conflicting value (JSONL has 'blue') -> differs
        ("W9", "gold"),         # only-in-sql -> would be LOST by a JSONL-only migration
    ])
    con.commit()
    con.close()
    return proj


def test_reconcile_categorizes_divergence(tmp_path):
    proj = _make_project(tmp_path)
    rep = rr.reconcile_project(proj)
    widget = next(c for c in rep["collections"] if c["collection"] == "Widget")
    assert widget["only_in_sql"] == ["W9"]
    assert widget["only_in_jsonl"] == ["W2"]
    assert widget["differs"] == ["W3"]
    assert widget["table_used"] == "noun_Widget"


def test_reconcile_flags_orphaned_table(tmp_path):
    proj = _make_project(tmp_path)
    # add a digit-leading table the live reader's sanitizer can't reach
    con = sqlite3.connect(str(proj / "objects.db"))
    con.execute('CREATE TABLE "noun_T_123" (id TEXT)')
    con.execute('INSERT INTO "noun_T_123" (id) VALUES (?)', ("x",))
    con.commit()
    con.close()
    rep = rr.reconcile_project(proj)
    assert any(o["table"] == "noun_T_123" and o["rows"] == 1 for o in rep["orphaned_tables"])


def test_migrate_jsonl_source_aborts_on_divergence(tmp_path, monkeypatch):
    _make_project(tmp_path)
    monkeypatch.setattr(mr, "projects_dir", lambda: tmp_path)
    rc = mr.main(["--project", "DivergeProj", "--source", "jsonl", "--apply",
                  "--db", str(tmp_path / "out.db")])
    assert rc == 3  # interlock refused the lossy JSONL-only migration
    assert not (tmp_path / "out.db").exists()  # nothing was written


def test_migrate_jsonl_source_proceeds_with_override(tmp_path, monkeypatch):
    _make_project(tmp_path)
    monkeypatch.setattr(mr, "projects_dir", lambda: tmp_path)
    rc = mr.main(["--project", "DivergeProj", "--source", "jsonl", "--apply",
                  "--db", str(tmp_path / "out.db"), "--allow-divergent"])
    assert rc in (0, 1)
    assert (tmp_path / "out.db").exists()


def test_migrate_merged_is_lossless(tmp_path, monkeypatch):
    """Default 'merged' source: SQL-wins, JSONL-fills-blanks; union of keys; no interlock needed."""
    _make_project(tmp_path)
    monkeypatch.setattr(mr, "projects_dir", lambda: tmp_path)
    db = tmp_path / "instances.db"
    rc = mr.main(["--project", "DivergeProj", "--apply", "--db", str(db)])  # source defaults to merged
    assert rc == 0
    con = sqlite3.connect(str(db))
    rows = {r[0]: json.loads(r[1]) for r in
            con.execute("SELECT key, data FROM instances WHERE collection='Widget'")}
    con.close()
    assert set(rows) == {"W1", "W2", "W3", "W9"}      # union: W9 (only-sql) + W2 (only-jsonl) both kept
    assert rows["W3"]["color"] == "BLUE"               # SQL wins the conflict (vs JSONL 'blue')
