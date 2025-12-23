# api/tools/s3_viewer.py
from __future__ import annotations
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import RedirectResponse
from botocore.exceptions import ClientError

# -------------------------------------------------------------------------
# Imports from your manifest system
# -------------------------------------------------------------------------
from api.manifest.resolver import (
    _manifest_dir,
    log_info,
    log_warn,
)
import json
from importlib import import_module

# -------------------------------------------------------------------------
# Debug helper
# -------------------------------------------------------------------------
DEBUG_ENABLED = True
def debug(*args, **kwargs):
    if DEBUG_ENABLED:
        print("[s3_viewer]", *args, **kwargs)

# -------------------------------------------------------------------------
# Router setup
# -------------------------------------------------------------------------
router = APIRouter(prefix="/s3_viewer", tags=["S3 Viewer"])

# -------------------------------------------------------------------------
# Load S3 manifest + resolver dynamically (just like RDS)
# -------------------------------------------------------------------------
_s3_manifest_path = _manifest_dir / "s3_manifest.json"
s3_manifest = {}
s3_resolver_module = None

if _s3_manifest_path.exists():
    try:
        s3_manifest = json.loads(_s3_manifest_path.read_text())
        resolver_module_name = s3_manifest.get("resolver_module")
        if resolver_module_name:
            s3_resolver_module = import_module(resolver_module_name)
            log_info("S3 resolver imported", {"module": resolver_module_name})
        else:
            log_warn("s3_manifest.json missing 'resolver_module'")
    except Exception as e:
        log_warn("Failed to load s3_manifest.json", {"error": repr(e)})
else:
    log_warn("s3_manifest.json not found", {"path": str(_s3_manifest_path)})

S3_ENABLED = bool(s3_resolver_module and s3_manifest)

# -------------------------------------------------------------------------
# Endpoint: generate presigned URL and redirect
# -------------------------------------------------------------------------
@router.get("/view")
def view_s3_object(
    key: str = Query(..., description="Object key inside your S3 bucket"),
    expires: int = Query(600, description="Link expiration in seconds (default 10 min)")
):
    """
    Generate a temporary signed URL for the given S3 object and redirect to it.
    Works via manifest-based resolver (no env vars required).

    Example:
        /s3_viewer/view?key=LIMS-System/test/photo1.jpg
    """
    debug("Requested key:", key)

    if not S3_ENABLED:
        raise HTTPException(400, "S3 not enabled or manifest missing")

    if not key:
        raise HTTPException(400, "Missing 'key' parameter")

    try:
        # Get presigned URL via resolver
        client = s3_resolver_module.get_s3_client(s3_manifest["region_name"])
        bucket = s3_manifest["bucket_name"]
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires,
        )
        debug("Generated presigned URL:", url)
        return RedirectResponse(url)

    except ClientError as e:
        debug("AWS error:", e)
        raise HTTPException(500, f"S3 error: {e}")

    except Exception as e:
        debug("General failure:", e)
        raise HTTPException(500, f"S3 resolver failed: {e}")
