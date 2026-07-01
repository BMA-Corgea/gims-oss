import shutil
from pathlib import Path
from types import SimpleNamespace
import json

import builtins
import utils.file_ops as file_ops

def test_sanitize_filename():
    name = "Raw Data: PCR Output (1)"
    assert file_ops.sanitize_filename(name) == "Raw_Data_PCR_Output_1.csv"

# Updated upload tests to use upload_file_to_folder

def test_upload_file_to_folder_success(tmp_path, monkeypatch):
    src = tmp_path / "in.csv"
    src.write_text("a,b")
    dest_folder = tmp_path / "dest"
    monkeypatch.setattr(file_ops.tk, "Tk", lambda: SimpleNamespace(withdraw=lambda: None))
    monkeypatch.setattr(file_ops.filedialog, "askopenfilename", lambda title, filetypes: str(src))
    uploaded = file_ops.upload_file_to_folder(dest_folder)
    assert uploaded
    assert (dest_folder / "in.csv").exists()
    assert (dest_folder / "in.csv").read_text() == "a,b"

def test_upload_file_to_folder_cancel(tmp_path, monkeypatch):
    dest_folder = tmp_path / "dest"
    monkeypatch.setattr(file_ops.tk, "Tk", lambda: SimpleNamespace(withdraw=lambda: None))
    monkeypatch.setattr(file_ops.filedialog, "askopenfilename", lambda *a, **k: "")
    uploaded = file_ops.upload_file_to_folder(dest_folder)
    assert uploaded is None
    assert not dest_folder.exists()

def test_upload_file_to_folder_creates_parent(tmp_path, monkeypatch):
    src = tmp_path / "src.csv"
    src.write_text("a")
    dest_folder = tmp_path / "new" / "sub"
    monkeypatch.setattr(file_ops.tk, "Tk", lambda: SimpleNamespace(withdraw=lambda: None))
    monkeypatch.setattr(file_ops.filedialog, "askopenfilename", lambda *a, **k: str(src))
    uploaded = file_ops.upload_file_to_folder(dest_folder)
    assert uploaded
    assert (dest_folder / "src.csv").exists()

def test_upload_file_to_folder_overwrite(tmp_path, monkeypatch):
    src = tmp_path / "src.csv"
    src.write_text("first")
    dest_folder = tmp_path / "dest"
    dest_folder.mkdir()
    (dest_folder / "src.csv").write_text("old")
    monkeypatch.setattr(file_ops.tk, "Tk", lambda: SimpleNamespace(withdraw=lambda: None))
    monkeypatch.setattr(file_ops.filedialog, "askopenfilename", lambda *a, **k: str(src))
    uploaded = file_ops.upload_file_to_folder(dest_folder)
    assert uploaded
    assert (dest_folder / "src.csv").read_text() == "first"

def test_upload_file_to_folder_copy_error(tmp_path, monkeypatch):
    src = tmp_path / "src.csv"
    src.write_text("a")
    dest_folder = tmp_path / "dest"
    def bad_copy(src_p, dest_p):
        raise IOError("boom")
    monkeypatch.setattr(file_ops.shutil, "copy", bad_copy)
    monkeypatch.setattr(file_ops.tk, "Tk", lambda: SimpleNamespace(withdraw=lambda: None))
    monkeypatch.setattr(file_ops.filedialog, "askopenfilename", lambda *a, **k: str(src))
    uploaded = file_ops.upload_file_to_folder(dest_folder)
    assert uploaded is None

# Additional sanitize_filename cases

def test_sanitize_trailing_spaces():
    assert file_ops.sanitize_filename("  name  ") == "name.csv"

def test_sanitize_special_chars():
    assert file_ops.sanitize_filename("A*B&C") == "A_B_C.csv"

def test_sanitize_collapse_underscores():
    assert file_ops.sanitize_filename("A__B  C") == "A_B_C.csv"

def test_sanitize_numbers():
    assert file_ops.sanitize_filename("file123") == "file123.csv"
