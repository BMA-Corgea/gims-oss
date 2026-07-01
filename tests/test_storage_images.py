"""Phase 5 — noun image nomenclature + ObjectStore round-trip (the 'images in SQL, own folder' work).

Images are referenced from the SQL record by a stable key and stored in a dedicated images/ folder
via the ObjectStore (local in tests). Pins the key scheme, traversal-safety, and a put/get round-trip.
"""
from core.storage import images


def test_image_key_scheme_is_collection_filename():
    assert images.image_object_key("Submission", "cap.jpg") == "images/Submission/cap.jpg"
    # spaces in a collection name are kept (readable; matches collection_for_noun)
    assert images.image_object_key("COA Name Map", "a.png") == "images/COA Name Map/a.png"


def test_image_key_is_traversal_safe_and_basenames_filenames():
    # a legacy nested filename is reduced to its basename; .. / separators can't escape the root
    key = images.image_object_key("N", "nouns/N/images/p.jpg")
    assert key == "images/N/p.jpg"
    assert ".." not in key  # no traversal segment survives


def test_is_legacy_image_ref():
    assert images.is_legacy_image_ref("nouns/Submission/images/cap.jpg") is True
    assert images.is_legacy_image_ref("images/Submission/cap.jpg") is False
    assert images.is_legacy_image_ref("") is False
    assert images.is_legacy_image_ref(None) is False


def test_relocate_legacy_ref():
    assert images.relocate_legacy_ref("nouns/Submission/images/cap.jpg") == "images/Submission/cap.jpg"
    assert images.relocate_legacy_ref("nouns/Primary Aromas/images/a.jpg") == "images/Primary Aromas/a.jpg"
    assert images.relocate_legacy_ref("images/Already/cap.jpg") == "images/Already/cap.jpg"  # unchanged


def test_put_then_get_roundtrips_via_object_store(tmp_path, monkeypatch):
    monkeypatch.delenv("GIMS_STORAGE_PROVIDER", raising=False)
    data = b"\x89PNG\r\n\x1a\n fake bytes"
    key = images.put_image(tmp_path, "Submission", "cap.png", data)
    assert key == "images/Submission/cap.png"
    # local provider wrote it under the project's dedicated images/ folder
    assert (tmp_path / "images" / "Submission" / "cap.png").read_bytes() == data
    assert images.get_image(tmp_path, key) == data
