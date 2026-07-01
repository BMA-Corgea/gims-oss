import pytest
import builtins
from utils import interface


def test_menu_prompt(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "a")
    assert interface.menu_prompt({"a": "add"}) == "a"


def test_indexed_choice(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "1")
    assert interface.indexed_choice(["x", "y"], prompt_msg="Choose") == 1


def test_prompt_if_missing_direct():
    assert interface.prompt_if_missing("val", ["x"], "label") == "val"


def test_prompt_if_missing_prompt(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "0")
    assert interface.prompt_if_missing(None, ["a"], "thing") == "a"


def test_menu_prompt_retry(monkeypatch):
    seq = iter(["x", "a"])
    monkeypatch.setattr(builtins, "input", lambda *a: next(seq))
    result = interface.menu_prompt({"a":"add"})
    assert result == "a"


def test_indexed_choice_quit(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "q")
    assert interface.indexed_choice(["x"], prompt_msg="P") is None


def test_indexed_choice_empty(capsys):
    assert interface.indexed_choice([], prompt_msg="P") is None
    assert "No options" in capsys.readouterr().out


def test_prompt_if_missing_exit(monkeypatch):
    monkeypatch.setattr(interface, "indexed_choice", lambda opts, prompt_msg="": None)
    with pytest.raises(SystemExit):
        interface.prompt_if_missing(None, ["x"], "label")


def test_prompt_if_missing_lower(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "0")
    res = interface.prompt_if_missing(None, ["AA"], "label", lowercase=True)
    assert res == "aa"


def test_menu_prompt_match_first(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a: "d")
    result = interface.menu_prompt({"d":"delete"})
    assert result == "d"
