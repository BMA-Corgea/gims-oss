# core/camera.py

from typing import Optional


ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_PIXELS = 100 * 1_000_000   # 100 MP
MAX_WIDTH = 1080               # px
MAX_SIZE_BYTES = 3 * 1024 * 1024  # 3 MB


class CameraValidationError(Exception):
    pass


def validate_image_metadata(
    mimetype: str,
    width: int,
    height: int,
    size_bytes: int
) -> list[str]:
    """
    Validate image metadata against global rules.
    Returns a list of error messages (empty if valid).
    """
    errors = []
    if mimetype not in ALLOWED_TYPES:
        errors.append(f"[X] Invalid type {mimetype}, allowed: {sorted(ALLOWED_TYPES)}")

    if width * height > MAX_PIXELS:
        errors.append(f"[X] Image too large: {width}x{height} px exceeds {MAX_PIXELS} px")

    if size_bytes > MAX_SIZE_BYTES:
        errors.append(f"[X] File size {size_bytes} bytes exceeds {MAX_SIZE_BYTES} bytes")

    return errors


def find_picture_field(noun_schema: dict) -> Optional[str]:
    """
    Given a noun schema, return the first field that has adjective_class == 'Picture'.
    """
    for fld, fld_def in noun_schema.get("fields", {}).items():
        if fld_def.get("adjective_class") == "Picture":
            return fld
    return None


def build_image_update(
    noun_schema: dict,
    noun_item: dict,
    rel_path: str
) -> dict:
    """
    Build an update dict for a noun item when a new picture path is provided.
    No writes are performed here, only a pure mapping of what should be changed.
    """
    primary_id_field = noun_schema.get("primary_id_field")
    if not primary_id_field:
        raise CameraValidationError("[X] Noun schema missing primary_id_field")

    picture_field = find_picture_field(noun_schema)
    if not picture_field:
        raise CameraValidationError("[X] No Picture adjective found in noun schema")

    if primary_id_field not in noun_item:
        raise CameraValidationError(f"[X] Item missing primary_id_field {primary_id_field}")

    return {
        "primary_id": noun_item[primary_id_field],
        "run_id": noun_item.get("_runID"),
        "updates": {
            picture_field: rel_path
        }
    }
