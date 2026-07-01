# api/routers/verb/_compat.py
#
# S3-aware compatibility helpers + S3 data-dump prefix copy.
# Moved VERBATIM from api/routers/verb.py (no logic changes).

from pathlib import Path
from typing import Any, Dict

# ─────────────────────────────────────────────────────────────
# S3-aware helpers (from json_proxy)
# ensure_prefix / touch make visible "folders" & empty files in S3.
# We also import internal helpers to support prefix copy on S3.
# If json_proxy isn't available (local-only env), we fall back to local ops.
# ─────────────────────────────────────────────────────────────
try:
    from api.json_proxy import ensure_prefix, touch  # S3-aware mkdir/touch
    from api.json_proxy import _is_s3_path as _s3_is_path
    from api.json_proxy import _get_client as _s3_client
    from api.json_proxy import _key_from_path as _s3_key_from_path
    from api.json_proxy import s3_manifest as _S3_MANIFEST
    _HAS_S3 = True
except Exception:
    _HAS_S3 = False

    def ensure_prefix(path: Path) -> bool:
        path.mkdir(parents=True, exist_ok=True)
        return True

    def touch(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    def _s3_is_path(_):  # type: ignore
        return False

    def _s3_client():  # type: ignore
        raise RuntimeError("S3 unavailable")

    def _s3_key_from_path(p):  # type: ignore
        return str(p)

# ─────────────────────────────────────────────────────────────
# S3 data-dump helpers
# ─────────────────────────────────────────────────────────────
def _s3_copy_prefix(src_dir: Path, dst_dir: Path, delete_src: bool = True) -> Dict[str, Any]:
    """
    Copy all S3 objects under src_dir/ → dst_dir/.
    """
    if not _HAS_S3:
        return {"copied": 0, "deleted": 0, "note": "S3 helpers unavailable"}

    client = _s3_client()
    bucket = _S3_MANIFEST.get("bucket_name")
    src_prefix = _s3_key_from_path(src_dir).rstrip("/") + "/"
    dst_prefix = _s3_key_from_path(dst_dir).rstrip("/") + "/"

    ensure_prefix(dst_dir)

    copied = 0
    deleted = 0
    continuation = None
    while True:
        kw = {"Bucket": bucket, "Prefix": src_prefix}
        if continuation:
            kw["ContinuationToken"] = continuation
        resp = client.list_objects_v2(**kw)
        contents = resp.get("Contents", [])
        for obj in contents:
            key = obj["Key"]
            # Skip the folder marker if present
            if key.endswith("/") and key == src_prefix:
                continue
            suffix = key[len(src_prefix):]
            new_key = dst_prefix + suffix
            client.copy_object(
                Bucket=bucket,
                CopySource={"Bucket": bucket, "Key": key},
                Key=new_key,
            )
            copied += 1
        if not resp.get("IsTruncated"):
            break
        continuation = resp.get("NextContinuationToken")

    if delete_src and copied:
        # Collect keys again for deletion
        continuation = None
        to_delete = []
        while True:
            kw = {"Bucket": bucket, "Prefix": src_prefix}
            if continuation:
                kw["ContinuationToken"] = continuation
            resp = client.list_objects_v2(**kw)
            contents = resp.get("Contents", [])
            for obj in contents:
                to_delete.append({"Key": obj["Key"]})
                if len(to_delete) == 1000:
                    client.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
                    deleted += len(to_delete)
                    to_delete = []
            if not resp.get("IsTruncated"):
                break
            continuation = resp.get("NextContinuationToken")
        if to_delete:
            client.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
            deleted += len(to_delete)

    return {"copied": copied, "deleted": deleted}
