"""Noun image handling — bytes in the ObjectStore, reference (key) in the SQL record.

Owner decision (2026-06-24): image handling happens in SQL (the noun record holds an image KEY,
never the bytes) and images get their OWN folder + nomenclature — out of the retired
``nouns/<type>/images/`` tree. This is locked decision 6 (large binaries -> object store by
reference) applied to images.

Nomenclature — a dedicated top-level ``images/`` folder, organised by noun collection then the
record's primary id::

    images/<collection>/<primary_id>/<filename>

So every image is traceable to the exact noun instance it belongs to, and the SAME key resolves in
both providers through the one :class:`core.storage.ports.ObjectStore`:

* local : a file at ``projects/<project>/images/<collection>/<primary_id>/<filename>``
* aws   : the S3 key ``images/<collection>/<primary_id>/<filename>`` under the project prefix

The noun record's ``image`` field stores that key. Legacy references (``nouns/<type>/images/<file>``)
are rewritten to this scheme by ``tools/migrate_images`` when the noun folders are retired.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.storage.factory import get_object_store

IMAGES_ROOT = "images"


def _safe(segment: str, *, basename: bool = False) -> str:
    """One path segment: drop traversal/separators, keep it human-readable. ``basename`` reduces a
    path to its final component (used for filenames, which may arrive as a legacy ``a/b/c.jpg``)."""
    s = str(segment).strip().replace("\\", "/")
    s = s.split("/")[-1] if basename else s.replace("/", "_")
    s = re.sub(r"[^\w.\- ]+", "_", s).strip()
    return s or "_"


def image_object_key(collection: str, filename: str) -> str:
    """The canonical ObjectStore key for a noun image: ``images/<collection>/<filename>``.

    Mirrors the per-noun source layout under one dedicated ``images/`` folder (organisation is not a
    priority per the owner; filenames are already unique within a noun's image folder)."""
    return f"{IMAGES_ROOT}/{_safe(collection)}/{_safe(filename, basename=True)}"


_LEGACY_RE = re.compile(r"nouns/(.+?)/images/(.+)$")


def is_legacy_image_ref(ref: object) -> bool:
    """True for a pre-cutover reference of the form ``nouns/<noun>/images/<file>``."""
    return bool(ref) and "nouns/" in str(ref) and "/images/" in str(ref)


def relocate_legacy_ref(ref: str) -> str:
    """Rewrite ``nouns/<noun>/images/<rest>`` -> ``images/<noun>/<rest>`` (else return unchanged)."""
    m = _LEGACY_RE.match(str(ref))
    return f"{IMAGES_ROOT}/{m.group(1)}/{m.group(2)}" if m else str(ref)


def put_image(project_path: Path, collection: str, filename: str, data: bytes) -> str:
    """Store image bytes under the canonical key; return the key to persist in the noun record."""
    key = image_object_key(collection, filename)
    return get_object_store(Path(project_path)).put_object(key, data)


def get_image(project_path: Path, key: str) -> bytes:
    """Fetch image bytes for a stored key via the project's ObjectStore."""
    return get_object_store(Path(project_path)).get_object(key)
