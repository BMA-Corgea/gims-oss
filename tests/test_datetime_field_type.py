"""The ``datetime`` noun field type — a date WITH a time-of-day, distinct from ``date``.

Covers the type vocabulary (canonical + both NounType handlers), the validation engine's
date-vs-datetime distinction, and the create-path through the real ``/noun/edit`` API the React
noun editor drives.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from unittest.mock import patch

from core.words.wordtype import CANONICAL_FIELD_TYPES, WordType
from core.words.validation import validate_instance, is_valid_date, is_valid_datetime


def test_datetime_in_every_type_vocabulary():
    assert "datetime" in CANONICAL_FIELD_TYPES
    from core.handlers.noun import TYPE_MAP as CORE_TM, VALID_FIELD_TYPES as CORE_VFT
    from utils.handlers.noun import TYPE_MAP as CLI_TM, VALID_FIELD_TYPES as CLI_VFT
    assert CORE_TM.get("datetime") == "datetime" and "datetime" in CORE_VFT
    assert CLI_TM.get("datetime") == "datetime" and "datetime" in CLI_VFT


def test_date_vs_datetime_validation_is_distinct():
    # a date field is day-granular: it rejects a time-of-day
    assert is_valid_date("2026-06-29", "yyyy-mm-dd")
    assert not is_valid_date("2026-06-29T14:30", "yyyy-mm-dd")
    # a datetime field accepts an instant (and tolerates a bare date)
    assert is_valid_datetime("2026-06-29T14:30:00.123Z", None)
    assert is_valid_datetime("2026-06-29T14:30", None)
    assert is_valid_datetime("2026-06-29", None)
    assert not is_valid_datetime("nope", None)


def test_validate_instance_distinguishes_date_and_datetime():
    wt = WordType.from_dict("noun", "N", {
        "primary_id_field": "id",
        "fields": {
            "id": {"type": "string"},
            "day": {"type": "date", "format": "yyyy-mm-dd"},
            "at": {"type": "datetime"},
        },
    })
    assert validate_instance({"id": "x", "day": "2026-06-29", "at": "2026-06-29T14:30"}, wt) == []
    bad = validate_instance({"id": "x", "day": "2026-06-29T14:30", "at": "garbage"}, wt)
    codes = {f.code for f in bad}
    assert "TYPE_NOT_DATE" in codes      # a date field rejects a time-bearing value
    assert "TYPE_NOT_DATETIME" in codes  # a datetime field rejects junk


def test_datetime_field_create_path_through_noun_api(tmp_path):
    """Adding a datetime field via POST /noun/edit (the React noun editor's path) persists it as
    type=datetime, distinct from a date field, and the schema still validates."""
    from api import json_proxy
    from api.manifest.resolver import resolve_path
    from fastapi.testclient import TestClient

    P = "ZZDatetimeFieldTypeTest"
    proj = resolve_path(Path(), "project_root") / P
    if proj.exists():
        shutil.rmtree(proj)
    proj.mkdir(parents=True)
    (proj / "noun_types.json").write_text(json.dumps(
        {"Specimen": {"primary_id_field": "specimen_id", "fields": {"specimen_id": {"type": "string", "required": True}}}}))
    (proj / "adjective_types.json").write_text("{}")
    (proj / "verb_types.json").write_text("{}")

    try:
        with patch.object(json_proxy, "_is_s3_path", lambda path: False):
            import api.app as m
            c = TestClient(m.app)
            assert c.post(f"/noun/edit/{P}/Specimen",
                          json={"action": "add", "field_name": "received_at", "field_type": "datetime"}).status_code == 200
            assert c.post(f"/noun/edit/{P}/Specimen",
                          json={"action": "add", "field_name": "received_day", "field_type": "date", "format": "yyyy-mm-dd"}).status_code == 200
            fields = json.loads((proj / "noun_types.json").read_text())["Specimen"]["fields"]
            assert fields["received_at"]["type"] == "datetime"
            assert fields["received_day"]["type"] == "date"
            assert c.get(f"/noun/describe/{P}/Specimen").status_code == 200
    finally:
        shutil.rmtree(proj, ignore_errors=True)
