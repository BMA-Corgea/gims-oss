import json
import tools.create_project as cp
from pathlib import Path


def test_create_project_success(tmp_path, monkeypatch):
    base = tmp_path / "projects"
    monkeypatch.setattr(cp, "BASE", base)
    cp.create_project("NewProj")
    proj = base / "NewProj"
    assert proj.exists()
    for sub in ["nouns", "verbs", "adjectives", "adverbs", "aliases"]:
        assert (proj / sub).is_dir()
    for alias in ["nouns", "verbs", "adjectives", "adverbs"]:
        path = proj / "aliases" / f"{alias}.json"
        assert path.exists() and json.loads(path.read_text()) == {}
    cfg = json.loads((proj / "config.json").read_text())
    assert cfg["name"] == "NewProj" and cfg["version"] == "0.1.0"


def test_create_project_existing(tmp_path, monkeypatch, capsys):
    base = tmp_path / "projects"
    proj = base / "P"
    proj.mkdir(parents=True)
    monkeypatch.setattr(cp, "BASE", base)
    cp.create_project("P")
    out = capsys.readouterr().out
    assert "already exists" in out