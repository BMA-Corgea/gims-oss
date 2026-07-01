# api/iostore/writers.py -- split out of api/i_o.py (wiring-neutral). JSON/JSONL writers.
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Callable
from api.json_proxy import read_text, write_text
from utils.logger import get_logger

log = get_logger(__name__)


def save_schema(project_path: Path, word_type: str, data):
    """
    Overwrite the full *_types.json file for the given word type. (S3-AWARE)

    For 'adjective'/'adverb', `data` is the legacy per-scope LIST the GUI edits; it is folded to the
    canonical name-keyed dict before writing (Phase 6/R17) so the on-disk migration STICKS and
    folded same-name entries (e.g. one adjective on two nouns) are preserved. noun/verb write as-is.
    """
    path = project_path / f"{word_type}_types.json"
    if word_type in ("adjective", "adverb") and isinstance(data, list):
        from core.words.reader import keyed_from_descriptor_list
        out = keyed_from_descriptor_list(word_type, data)
    else:
        out = data
    write_text(path, json.dumps(out, indent=2), encoding="utf-8")

def save_override(project_path: Path, data: list[dict]):
    path = project_path / "override.json"
    write_text(path, json.dumps(data, indent=2), encoding="utf-8")

def append_jsonl(path: Path, entry: dict):
    """
    Append a new JSON line to a .jsonl file. (S3-AWARE)
    """
    try:
        payload = read_text(path, encoding="utf-8")
        lines = payload.splitlines()
    except FileNotFoundError:
        lines = []
    
    lines.append(json.dumps(entry))
    new_payload = "\n".join(lines) + "\n"
    write_text(path, new_payload, encoding="utf-8")

def replace_jsonl_entry(path: Path, match: Callable[[dict], bool], new_entry: dict):
    """
    Replace a single entry in a .jsonl file where match(entry) is True. (S3-AWARE)
    """
    try:
        payload = read_text(path, encoding="utf-8")
        lines = [json.loads(line) for line in payload.splitlines() if line.strip()]
    except FileNotFoundError:
        raise ValueError("File not found, cannot replace entry.")

    updated = False
    for i, line in enumerate(lines):
        if match(line):
            lines[i] = new_entry
            updated = True
            break
    if not updated:
        raise ValueError("Entry not found for replacement.")
    
    new_payload = "".join(json.dumps(line) + "\n" for line in lines)
    write_text(path, new_payload, encoding="utf-8")

def rewrite_jsonl(path: Path, entries: list[dict]):
    """
    Atomically rewrite a .jsonl file with the given list of dicts. (S3-AWARE)
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    
    payload = "".join(json.dumps(e) + "\n" for e in entries)
    write_text(path, payload, encoding="utf-8")

def save_json(path: Path, data: dict):
    """
    Save a full dict to a .json file (e.g., DataEntry.json, Status.json). (S3-AWARE)
    """
    write_text(path, json.dumps(data, indent=2), encoding="utf-8")

def read_json(path: Path, default=None):
    try:
        txt = read_text(path, encoding="utf-8")
        return json.loads(txt)
    except FileNotFoundError:
        return default
    except Exception:
        log.warning("[read_json] failed to read/parse JSON", {"path": str(path)}, exc_info=True)
        return default

def write_json(path: Path, data: Any):
    save_json(path, data)
