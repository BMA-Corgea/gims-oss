import json
import builtins
from pathlib import Path
from utils.handlers.verb import VerbType
from tools.register import register_verb_interactive
from tools.edit import edit_verb_interactive
from utils.handlers import conjunction

import pytest


@pytest.fixture
def project_dir(tmp_path, monkeypatch):
    # work inside a fresh temp directory
    monkeypatch.chdir(tmp_path)
    proj = tmp_path / "projects" / "MyProj"
    proj.mkdir(parents=True)
    return proj


def test_register_cli_entrypoint(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "TestVerbCLI")
    monkeypatch.setattr(VerbType, "interactive_register_from_context", lambda self: True)
    # should not error
    register_verb_interactive("MyProj")


def test_edit_cli_entrypoint(monkeypatch):
    called = {}
    def fake_edit_existing(self):
        called["ok"] = True
    monkeypatch.setattr(VerbType, "edit_existing", fake_edit_existing)
    edit_verb_interactive("MyProj")
    assert called.get("ok"), "edit_existing should have been called"


def test_interactive_register_creates_file(project_dir, monkeypatch):
    from pathlib import Path
    import json
    from utils.handlers.verb import VerbType
    import utils.handlers.conjunction as conjunction

    vt = VerbType("TestVerb", "MyProj")
    vt.verb_path.parent.mkdir(parents=True, exist_ok=True)

    # ─── Provide inputs in the exact order called ──────────────────────────────
    # 1) indexed_choice → len(opts) to create new group
    # 2) input for new group name
    # 3) input for description
    # 4) prompt_status_overrides → menu loop ('q' to exit)
    # 5) menu_prompt loop ('s' to save)

    inputs = iter([
        "Demo",                # new group name
        "A wonderful verb",    # description
        "q"                    # quit prompt_status_overrides
    ])

    def patched_input(prompt=""):
        return next(inputs)

    monkeypatch.setattr("builtins.input", patched_input)

    # indexed_choice returns len(opts) to simulate creating a new group
    monkeypatch.setattr("utils.handlers.verb.indexed_choice", lambda opts, msg: len(opts) - 1)

    # stub prompt_status_overrides to avoid internal input calls
    monkeypatch.setattr(
        "utils.handlers.verb.prompt_status_overrides",
        lambda existing_overrides=None, project_name="": ["Pending", "Complete"]
    )

    # stub menu_prompt to save immediately
    monkeypatch.setattr("utils.handlers.verb.menu_prompt", lambda opts: "s")

    # stub out schema methods and folder creation
    monkeypatch.setattr(vt, "configure_data_entry_schema", lambda: vt.data.update({
        "data_entry_schema": {"set_up_inputs": {"noun_type_ref": "Sample"}}
    }))
    monkeypatch.setattr(vt, "configure_adverb_schema", lambda: None)
    monkeypatch.setattr(vt, "ensure_group_folders_and_log", lambda: None)

    # ─── Execute ───────────────────────────────────────────────────────────────
    saved = vt.interactive_register_from_context()
    assert saved is True

    # ─── Confirm verb_types.json was written correctly ─────────────────────────
    vtfile = Path("projects") / "MyProj" / "verb_types.json"
    data = json.loads(vtfile.read_text())
    assert "TestVerb" in data
    entry = data["TestVerb"]
    assert entry["verb_group"] == "Demo"
    assert entry["status_values"] == ["Pending", "Complete"]


def test_configure_adverb_schema(monkeypatch, tmp_path):
    from utils.handlers.verb import VerbType
    import json
    import utils.handlers.conjunction as conjunction

    # ─── Setup a fake project ──────────────────────────────────────────────────
    proj = tmp_path / "projects" / "MyProj"
    proj.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    # ─── Provide inputs for adverb name and reference noun ─────────────────────
    inputs = iter([
        "weather",      # adverb name
        "temperature",  # reference noun
        "",             # blank to finish loop
        "q"             # quit prompt_status_overrides
    ])

    def patched_input(prompt=""):
        return next(inputs)

    monkeypatch.setattr("builtins.input", patched_input)

    # stub prompt_status_overrides to avoid internal input calls
    monkeypatch.setattr(
        conjunction, "prompt_status_overrides",
        lambda existing=None, project_name="": []
    )

    vt = VerbType("AdvTest", "MyProj")
    vt.configure_adverb_schema()

    # ─── Verify in-memory adverb_schema ────────────────────────────────────────
    assert vt.data["adverb_schema"] == {
        "weather": {"reference_noun": "temperature"}
    }

    # ─── Verify adverb_types.json was updated on disk ──────────────────────────
    adf = proj / "adverb_types.json"
    entries = json.loads(adf.read_text())
    assert any(
        e["verb"] == "AdvTest"
        and e["adverb"] == "weather"
        and e.get("reference_noun") == "temperature"
        for e in entries
    )


def test_configure_data_entry_schema_initializes_sections(monkeypatch):
    vt = VerbType("X", "MyProj")
    seq = iter(["i", "q"])
    monkeypatch.setattr("utils.handlers.verb.menu_prompt", lambda opts: next(seq))
    monkeypatch.setattr(vt, "_configure_instructions", lambda schema: schema.setdefault("instructions", ["hi"]))

    vt.configure_data_entry_schema()
    assert "instructions" in vt.data["data_entry_schema"]


def test_prompt_log_schema_adds_and_sets_primary(monkeypatch):
    vt = VerbType("X", "MyProj")
    # a) add runID (required), b) add note (optional), q, then pick primary
    seq = iter(['a','runID','0','y','a','note','0','n','q','0'])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(seq))
    monkeypatch.setattr("utils.handlers.verb.menu_prompt", lambda opts: next(seq))
    monkeypatch.setattr("utils.handlers.verb.indexed_choice", lambda opts, msg: 0)

    out = vt.prompt_log_schema_fields_with_primary()
    assert out["primary_id"] == "runID"
    assert out["fields"]["note"]["required"] is False


def test_configure_instructions(monkeypatch):
    vt = VerbType("I", "MyProj")
    seq = iter(['a','Step1','a','Step2','q'])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(seq))
    monkeypatch.setattr("utils.handlers.verb.menu_prompt", lambda opts: next(seq))

    schema = {}
    vt._configure_instructions(schema)
    assert schema["instructions"] == ['Step1','Step2']


def test_configure_raw_data_inputs(monkeypatch):
    vt = VerbType("R", "MyProj")
    seq = iter(['a','Labels','a','Data','q'])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(seq))
    monkeypatch.setattr("utils.handlers.verb.menu_prompt", lambda opts: next(seq))

    schema = {}
    vt._configure_raw_data_inputs(schema)
    assert schema["raw_data_inputs"] == ['Labels','Data']


def test_configure_interpretation_manual(monkeypatch):
    vt = VerbType("InterpTest", "MyProj")
    schema = {}

    # pick "parsed" (index 0), then immediately quit the tab‐editor
    seq_idx = iter(['0'])
    monkeypatch.setattr("utils.handlers.verb.indexed_choice", lambda opts, msg: int(next(seq_idx)))
    monkeypatch.setattr("utils.handlers.verb.menu_prompt", lambda opts: 'q')

    vt._configure_interpretation(schema)
    assert schema["interpretation"]["method"] == "parsed"


def test_configure_interpretation_uploaded(monkeypatch):
    vt = VerbType("InterpAuto", "MyProj")
    schema = {}

    # pick "uploaded" (index 1), then quit
    seq_idx = iter(['1'])
    monkeypatch.setattr("utils.handlers.verb.indexed_choice", lambda opts, msg: int(next(seq_idx)))
    monkeypatch.setattr("utils.handlers.verb.menu_prompt", lambda opts: 'q')

    vt._configure_interpretation(schema)
    assert schema["interpretation"]["method"] == "uploaded"


def test_edit_verb_group_change_group(monkeypatch, project_dir):
    vt = VerbType("GroupVerb", "MyProj")
    vt.data['verb_group'] = 'Alpha'

    # seed an on‐disk verb_types.json
    vf = Path("projects") / "MyProj" / "verb_types.json"
    vf.write_text(json.dumps({
        'GroupVerb': {'verb_group': 'Alpha'},
        'Other':     {'verb_group': 'Beta'}
    }))

    # choose the one other group
    monkeypatch.setattr("utils.handlers.verb.indexed_choice", lambda opts,msg: 0)
    monkeypatch.setattr("builtins.input", lambda prompt="": "NewGroup")
    monkeypatch.setattr(vt, "ensure_group_folders_and_log", lambda: None)

    vt.edit_verb_group()
    assert vt.data['verb_group'] == 'Beta'
