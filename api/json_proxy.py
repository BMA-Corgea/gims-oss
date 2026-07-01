# api/json_proxy.py
from __future__ import annotations

import io
import json as _json
from pathlib import Path
import os
import fnmatch

import boto3  # kept for compatibility; real client comes from resolver module

# -------------------------
# Debug block
# -------------------------
from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()

# ──────────────────────────────────────────────────────────────
# Import RDS + S3 context from resolver
# ──────────────────────────────────────────────────────────────
from api.manifest.resolver import (
    RDS_ENABLED,
    _manifest_dir,
    log_info,
    log_warn,
)

log.debug("Initializing json_proxy. RDS_ENABLED:", RDS_ENABLED)

# Dynamically find the project root (repo root, i.e. .../GIMS-Project)
_project_root_path = _manifest_dir.parent.parent.resolve()
_project_root_str = str(_project_root_path)
log.debug("Resolved project root to:", _project_root_str)

# ──────────────────────────────────────────────────────────────
# Load S3 manifest + resolver (EXACTLY like s3_viewer.py)
# ──────────────────────────────────────────────────────────────
_s3_manifest_path = _manifest_dir / "s3_manifest.json"
s3_manifest: dict = {}
s3_resolver_module = None

if _s3_manifest_path.exists():
    try:
        from importlib import import_module

        s3_manifest = _json.loads(_s3_manifest_path.read_text())
        resolver_module_name = s3_manifest.get("resolver_module")
        if resolver_module_name:
            s3_resolver_module = import_module(resolver_module_name)
            log_info("S3 resolver imported", {"module": resolver_module_name})
            log.debug("S3 resolver imported:", resolver_module_name)
        else:
            log_warn("s3_manifest.json missing 'resolver_module'")
            log.debug("S3 resolver_module missing in manifest")
    except Exception as e:
        log_warn("Failed to load s3_manifest.json", {"error": repr(e)})
        log.debug("Failed to load S3 resolver:", repr(e))
else:
    log_warn("s3_manifest.json not found", {"path": str(_s3_manifest_path)})
    log.debug("s3_manifest.json not found:", _s3_manifest_path)

S3_ENABLED: bool = bool(RDS_ENABLED and s3_resolver_module and s3_manifest)

if S3_ENABLED:
    log_info(
        "S3 proxy activated",
        {
            "bucket": s3_manifest.get("bucket_name"),
            "region": s3_manifest.get("region_name"),
        },
    )
    log.debug(
        "S3 proxy activated:",
        s3_manifest.get("bucket_name"),
        s3_manifest.get("region_name"),
    )
else:
    log_info("S3 proxy inactive (RDS mode off or manifest missing)")
    log.debug("S3 proxy inactive")

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────
def _is_s3_path(path) -> bool:
    """
    Decide whether this path should go through S3.

    Rules:
    - If S3 is disabled → always local.
    - If it starts with 's3://' → treat as S3 path.
    - If S3 is enabled and the path is under the repo root → treat as S3 path.
    """
    path_str = str(path)

    # Explicit s3:// path
    if path_str.startswith("s3://"):
        log.debug("_is_s3_path:", path_str, "→ True (explicit s3://)")
        return True

    if not S3_ENABLED:
        log.debug("_is_s3_path:", path_str, "→ False (S3 disabled)")
        return False

    # Normal project-local path
    try:
        abs_path = str(Path(path_str).resolve())
    except Exception:
        abs_path = path_str

    if abs_path.startswith(_project_root_str):
        log.debug("_is_s3_path:", path_str, "→ True (project path in S3 mode)")
        return True

    log.debug("_is_s3_path:", path_str, "→ False (local path in S3 mode)")
    return False


def _key_from_path(path: str | Path) -> str:
    """
    Convert a local or explicit-s3 path to an S3 key.

    - For repo-local paths:
        ./GIMS-Project/projects/foo.json
        → GIMS-Project/projects/foo.json

    - For explicit s3://bucket/GIMS-Project/...:
        → GIMS-Project/...

    We *always* keep the 'GIMS-Project/...' mirror in the bucket.
    """
    path_str = str(path)

    # Explicit s3://bucket/... case
    if path_str.startswith("s3://"):
        bucket = s3_manifest.get("bucket_name", "")
        s3_prefix = f"s3://{bucket}/"
        if s3_prefix and path_str.startswith(s3_prefix):
            key = path_str[len(s3_prefix):]
        else:
            # Fallback: strip leading 's3://<something>/' if present
            tmp = path_str.split("s3://", 1)[-1]
            key = tmp.split("/", 1)[1] if "/" in tmp else tmp
        key = key.replace("\\", "/")
        log.debug("_key_from_path (s3):", path, "→", key)
        return key

    # Normal local path under project root's parent
    try:
        abs_path = Path(path_str).resolve()
        # so key becomes "GIMS-Project/..." → perfect mirror
        key = str(abs_path.relative_to(_project_root_path.parent)).replace(
            os.path.sep, "/"
        )
        log.debug("_key_from_path (local):", path, "→", key)
        return key
    except Exception as e:
        log.debug(
            f"_key_from_path FAILED for {path_str} (Error: {e}). Falling back to raw normalized path."
        )
        return path_str.replace(os.path.sep, "/")


def _ensure_trailing_slash(prefix: str) -> str:
    return prefix if prefix.endswith("/") else prefix + "/"


def _client():
    if not S3_ENABLED:
        raise RuntimeError("S3 proxy used while S3 is disabled.")
    region = s3_manifest.get("region_name", "us-east-1")
    client = s3_resolver_module.get_s3_client(region)
    return client


def _head_object_safe(bucket: str, key: str):
    """Return head_object dict or None if not found."""
    cli = _client()
    try:
        return cli.head_object(Bucket=bucket, Key=key)
    except Exception as e:
        msg = str(e)
        if "Not Found" in msg or "NoSuchKey" in msg or "404" in msg:
            return None
        try:
            from botocore.exceptions import ClientError

            if (
                isinstance(e, ClientError)
                and e.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404
            ):
                return None
        except Exception:
            pass
        raise


def _list_immediate(bucket: str, prefix: str):
    """
    List immediate children (files + 'dirs') under prefix.
    Returns (files, dirs) as lists of names (not full keys).
    """
    cli = _client()
    prefix = _ensure_trailing_slash(prefix)
    paginator = cli.get_paginator("list_objects_v2")
    files: list[str] = []
    dirs: list[str] = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            name = cp.get("Prefix", "")
            if name.startswith(prefix):
                name = name[len(prefix):].rstrip("/")
                if name:
                    dirs.append(name)
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            name = key[len(prefix):]
            if "/" in name:
                # deeper file; Delimiter should avoid these, but guard anyway
                continue
            if name:
                files.append(name)
    return files, dirs

# ──────────────────────────────────────────────────────────────
# Text helpers (used by i_o)
# ──────────────────────────────────────────────────────────────
def read_text(path: Path, encoding: str = "utf-8", errors: str | None = "ignore"):
    if _is_s3_path(path):
        cli = _client()
        key = _key_from_path(path)
        bucket = s3_manifest["bucket_name"]
        log.debug("read_text() S3", {"bucket": bucket, "key": key})
        obj = cli.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read().decode(encoding, errors=errors or "ignore")
    log.debug("read_text() local", path)
    return Path(path).read_text(encoding=encoding, errors=errors or "ignore")


def write_text(path: Path, data: str, encoding: str = "utf-8"):
    if _is_s3_path(path):
        cli = _client()
        key = _key_from_path(path)
        bucket = s3_manifest["bucket_name"]
        log.debug("write_text() S3", {"bucket": bucket, "key": key})
        resp = cli.put_object(
            Bucket=bucket,
            Key=key,
            Body=data.encode(encoding),
            ContentType="application/json",
        )
        return resp
    log.debug("write_text() local", path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(data, encoding=encoding)

# ──────────────────────────────────────────────────────────────
# S3-aware replacements for json.load / dump
# ──────────────────────────────────────────────────────────────
def load(fp, *args, **kwargs):
    log.debug("json.load called with:", type(fp))

    # Path-like argument
    if isinstance(fp, (str, Path)):
        if _is_s3_path(fp):
            cli = _client()
            key = _key_from_path(fp)
            bucket = s3_manifest["bucket_name"]
            obj = cli.get_object(Bucket=bucket, Key=key)
            body = obj["Body"].read().decode("utf-8")
            return _json.loads(body, *args, **kwargs)
        with open(fp, "r", encoding="utf-8") as f:
            return _json.load(f, *args, **kwargs)

    # File-like object that actually refers to S3 path
    if hasattr(fp, "read") and hasattr(fp, "name") and _is_s3_path(fp.name):
        try:
            fp.close()
        except Exception:
            pass
        cli = _client()
        key = _key_from_path(fp.name)
        bucket = s3_manifest["bucket_name"]
        obj = cli.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read().decode("utf-8")
        return _json.loads(body, *args, **kwargs)

    # Fallback: normal json.load
    return _json.load(fp, *args, **kwargs)


def loads(s, *args, **kwargs):
    log.debug("json.loads called", type(s))
    return _json.loads(s, *args, **kwargs)


def dump(obj, fp, *args, **kwargs):
    log.debug("json.dump called with:", type(fp))

    # Path-like argument
    if isinstance(fp, (str, Path)):
        if _is_s3_path(fp):
            cli = _client()
            key = _key_from_path(fp)
            bucket = s3_manifest["bucket_name"]
            data = _json.dumps(obj, *args, **kwargs)
            cli.put_object(
                Bucket=bucket,
                Key=key,
                Body=data.encode("utf-8"),
                ContentType="application/json",
            )
            return
        with open(fp, "w", encoding="utf-8") as f:
            return _json.dump(obj, f, *args, **kwargs)

    # File-like object with S3 name
    if hasattr(fp, "write") and hasattr(fp, "name") and _is_s3_path(fp.name):
        try:
            fp.close()
        except Exception:
            pass
        cli = _client()
        key = _key_from_path(fp.name)
        bucket = s3_manifest["bucket_name"]
        data = _json.dumps(obj, *args, **kwargs)
        cli.put_object(
            Bucket=bucket,
            Key=key,
            Body=data.encode("utf-8"),
            ContentType="application/json",
        )
        return

    # Fallback
    return _json.dump(obj, fp, *args, **kwargs)


def dumps(obj, *args, **kwargs):
    log.debug("json.dumps called", type(obj))
    return _json.dumps(obj, *args, **kwargs)

# ------------------------------------------------------------------------------
# Binary helpers
# ------------------------------------------------------------------------------
def write_bytes(path: str | Path, data: bytes):
    p = Path(path)
    if _is_s3_path(p):
        cli = _client()
        key = _key_from_path(p)
        bucket = s3_manifest["bucket_name"]
        log.debug("write_bytes() S3", {"bucket": bucket, "key": key, "bytes": len(data)})
        cli.put_object(Bucket=bucket, Key=key, Body=data)
        return {"ok": True, "bytes": len(data)}
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return {"ok": True, "bytes": len(data)}


def read_bytes(path: str | Path) -> bytes:
    p = Path(path)
    if _is_s3_path(p):
        cli = _client()
        key = _key_from_path(p)
        bucket = s3_manifest["bucket_name"]
        log.debug("read_bytes() S3", {"bucket": bucket, "key": key})
        obj = cli.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()
    with open(p, "rb") as f:
        return f.read()

# ------------------------------------------------------------------------------
# Filesystem-like adapters (used by i_o + GUI)
# ------------------------------------------------------------------------------
def exists(path: str | Path) -> bool:
    p = Path(path)
    if not _is_s3_path(p):
        return p.exists()
    bucket = s3_manifest["bucket_name"]
    key = _key_from_path(p)
    # file?
    if _head_object_safe(bucket, key):
        return True
    # directory?
    cli = _client()
    pref = _ensure_trailing_slash(key)
    resp = cli.list_objects_v2(Bucket=bucket, Prefix=pref, MaxKeys=1)
    return bool(resp.get("KeyCount", 0))


def is_file(path: str | Path) -> bool:
    p = Path(path)
    if not _is_s3_path(p):
        return p.is_file()
    bucket = s3_manifest["bucket_name"]
    key = _key_from_path(p)
    if key.endswith("/"):
        return False
    return _head_object_safe(bucket, key) is not None


def is_dir(path: str | Path) -> bool:
    p = Path(path)
    if not _is_s3_path(p):
        return p.is_dir()
    bucket = s3_manifest["bucket_name"]
    key = _key_from_path(p)
    cli = _client()
    pref = _ensure_trailing_slash(key)
    resp = cli.list_objects_v2(Bucket=bucket, Prefix=pref, MaxKeys=1)
    return bool(resp.get("KeyCount", 0))


def iterdir(path: str | Path):
    """
    Return a list of immediate children as Path objects.
    """
    p = Path(path)
    if not _is_s3_path(p):
        return list(p.iterdir())
    bucket = s3_manifest["bucket_name"]
    prefix = _key_from_path(p)
    files, dirs = _list_immediate(bucket, prefix)
    result: list[Path] = []
    for f in files:
        result.append(Path(str(p)) / f)
    for d in dirs:
        result.append(Path(str(p)) / d)
    return result


def walk(path: str | Path):
    """
    Rough os.walk equivalent for S3.
    Yields (root_path_str, dirnames, filenames).
    """
    p = Path(path)
    if not _is_s3_path(p):
        yield from os.walk(p)
        return
    bucket = s3_manifest["bucket_name"]
    root_key = _ensure_trailing_slash(_key_from_path(p))
    queue = [root_key]
    while queue:
        current = queue.pop(0)
        files, dirs = _list_immediate(bucket, current)
        for d in dirs:
            queue.append(_ensure_trailing_slash(current + d))
        if current == root_key:
            root_local = str(p)
        else:
            root_local = str(Path(str(p)) / current[len(root_key):])
        yield (root_local, dirs, files)


def makedirs(path: str | Path):
    p = Path(path)
    if not _is_s3_path(p):
        p.mkdir(parents=True, exist_ok=True)
        return True
    cli = _client()
    bucket = s3_manifest["bucket_name"]
    key = _ensure_trailing_slash(_key_from_path(p))
    log.debug("makedirs() S3", {"bucket": bucket, "prefix": key})
    cli.put_object(Bucket=bucket, Key=key, Body=b"")
    return True


def remove(path: str | Path):
    p = Path(path)
    if not _is_s3_path(p):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        return True
    bucket = s3_manifest["bucket_name"]
    key = _key_from_path(p)
    log.debug("remove() S3", {"bucket": bucket, "key": key})
    cli = _client()
    cli.delete_object(Bucket=bucket, Key=key)
    return True


def stat(path: str | Path):
    """
    Return a dict with at least 'st_size' for S3; os.stat_result for local.
    """
    p = Path(path)
    if not _is_s3_path(p):
        return p.stat()
    bucket = s3_manifest["bucket_name"]
    key = _key_from_path(p)
    head = _head_object_safe(bucket, key)
    if not head:
        raise FileNotFoundError(f"S3 object not found: {bucket}/{key}")
    size = int(head.get("ContentLength", 0))
    return {"st_size": size, "content_type": head.get("ContentType")}

# ------------------------------------------------------------------------------
# Minimal open() for S3
# ------------------------------------------------------------------------------
class _S3WriteBuffer(io.BytesIO):
    """
    Simple write-only buffer that uploads to S3 on close().
    """
    def __init__(self, bucket: str, key: str, content_type: str | None = None):
        super().__init__()
        self._bucket = bucket
        self._key = key
        self._content_type = content_type

    def close(self):
        try:
            data = self.getvalue()
            kwargs = {"Bucket": self._bucket, "Key": self._key, "Body": data}
            if self._content_type:
                kwargs["ContentType"] = self._content_type
            _client().put_object(**kwargs)
        finally:
            super().close()


def open(path: str | Path, mode: str = "rb"):
    """
    Minimal S3-aware open() for 'rb' and 'wb'.
    """
    p = Path(path)
    if not _is_s3_path(p):
        return __builtins__["open"](p, mode)

    bucket = s3_manifest["bucket_name"]
    key = _key_from_path(p)

    if "r" in mode:
        obj = _client().get_object(Bucket=bucket, Key=key)
        data = obj["Body"].read()
        return io.BytesIO(data)

    if "w" in mode:
        ctype = "application/octet-stream"
        if str(p).endswith(".json"):
            ctype = "application/json"
        return _S3WriteBuffer(bucket, key, content_type=ctype)

    raise ValueError("json_proxy.open supports only 'rb' / 'wb' modes")

# ------------------------------------------------------------------------------
# Glob + listing helpers
# ------------------------------------------------------------------------------
def glob_first(dir_path: str | Path, pattern: str) -> str | None:
    """
    Very small glob helper used by fs_glob_first(d, f"{tab}.*").
    Only scans the immediate directory level.
    """
    p = Path(dir_path)
    if not _is_s3_path(p):
        matches = list(p.glob(pattern))
        return str(matches[0]) if matches else None

    bucket = s3_manifest["bucket_name"]
    prefix = _ensure_trailing_slash(_key_from_path(p))
    files, _dirs = _list_immediate(bucket, prefix)
    for name in files:
        if fnmatch.fnmatch(name, pattern):
            return str(p / name)
    return None


def list_projects() -> list[str]:
    """
    Attempt to list project directories under <project_root>/projects
    or just under <project_root>, mirroring your app’s expectations.
    """
    if not S3_ENABLED:
        root = _project_root_path / "projects"
        if not root.exists():
            root = _project_root_path
        return sorted([p.name for p in root.iterdir() if p.is_dir()])

    bucket = s3_manifest["bucket_name"]
    # Try "<root>/projects/"
    base1 = _ensure_trailing_slash(
        _key_from_path(_project_root_path / "projects")
    )
    files, dirs = _list_immediate(bucket, base1)
    out = dirs[:]
    if not out:
        # Fallback to "<root>/"
        base2 = _ensure_trailing_slash(_key_from_path(_project_root_path))
        _files2, dirs2 = _list_immediate(bucket, base2)
        out = dirs2
    return sorted(out)


def list_dirnames(path: str | Path) -> list[str]:
    """
    List immediate subdirectories (dir names only).
    """
    p = Path(path)
    if not _is_s3_path(p):
        if p.exists():
            return sorted([d.name for d in p.iterdir() if d.is_dir()])
        return []
    bucket = s3_manifest["bucket_name"]
    prefix = _key_from_path(p)
    _files, dirs = _list_immediate(bucket, prefix)
    return sorted(dirs)
    
# ------------------------------------------------------------------------------
# Make this module a proper drop-in replacement for stdlib json
# ------------------------------------------------------------------------------
import sys as _sys

_this_module = _sys.modules[__name__]

# Copy over all stdlib-json attributes that we haven't overridden.
for _name in dir(_json):
    if not hasattr(_this_module, _name):
        setattr(_this_module, _name, getattr(_json, _name))