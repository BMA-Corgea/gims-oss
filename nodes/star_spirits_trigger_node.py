# nodes/star_spirits_trigger_node.py

from __future__ import annotations

import re
import time
import random
import string
from datetime import datetime
from typing import Any, Dict, Tuple

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.orchestration.node import Node, NodeKind
from core.orchestration.module import Module

# ──────────────────────────────────────────────────────────────────────────────
# Debug
# ──────────────────────────────────────────────────────────────────────────────
from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()

# ──────────────────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────────────────

router = APIRouter(tags=["Star Spirits – Trigger"])

# ──────────────────────────────────────────────────────────────────────────────
# Config & helpers
# ──────────────────────────────────────────────────────────────────────────────

# We DO NOT require feature tags anymore. Fire based on the API shape alone.
REQUIRE_TAG = False

# Map s1..s7 → readable names (fallback used if UI doesn't pass a name)
STAR_NAMES = {
    "s1": "Eldstar",
    "s2": "Mamar",
    "s3": "Skolar",
    "s4": "Muskular",
    "s5": "Misstar",
    "s6": "Klevar",
    "s7": "Kalmar",
}

CACHE_TTL_SEC = 30
HTTP_TIMEOUT_SEC = 6.0

# key: (path, user_id) → value: {"spirit": str, "name": Optional[str], "ts": float}
_pre_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}

def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def _rand8() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def _gc_cache() -> None:
    now = time.time()
    stale = [k for k, v in _pre_cache.items() if now - v.get("ts", 0.0) > CACHE_TTL_SEC]
    for k in stale:
        log.debug("cache:gc:drop", {"key": k})
        _pre_cache.pop(k, None)

def _extract_star_name(spirit: str, payload: Dict[str, Any]) -> str:
    # Prefer an explicit name if UI provided one; otherwise map sN → friendly name
    explicit = (payload.get("star_name") or payload.get("name") or "").strip()
    if explicit:
        return explicit
    s = (spirit or "").lower().strip()
    if s in STAR_NAMES:
        return STAR_NAMES[s]
    if s.startswith("s") and s[1:].isdigit():
        return f"Star Spirit {s[1:]}"
    return "Star Spirit"

# ──────────────────────────────────────────────────────────────────────────────
# PRE hook — record intent before forwarding
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/orchestrate/chain/pre")
async def chain_pre(request: Request):
    """
    Body from orchestrated fetch:
      { method, path, payload }
    We only watch: POST /star-spirits/{project}/collect
    """
    log.debug("pre:begin")
    try:
        env = await request.json()
    except Exception as e:
        log.debug("pre:json-error", repr(e))
        return JSONResponse({"effect": "allow"})

    log.debug("pre:envelope", env)

    method = (env.get("method") or "GET").upper()
    path: str = env.get("path") or ""
    payload: Dict[str, Any] = env.get("payload") or {}

    if method != "POST":
        log.debug("pre:skip:method", {"method": method})
        return JSONResponse({"effect": "allow"})

    if not re.match(r"^/star-spirits/[^/]+/collect$", path):
        log.debug("pre:skip:path", {"path": path})
        return JSONResponse({"effect": "allow"})

    user_id = str(payload.get("user_id") or "").strip()
    spirit = str(payload.get("spirit") or "").strip()
    if user_id and spirit:
        _gc_cache()
        star_name = _extract_star_name(spirit, payload)
        _pre_cache[(path, user_id)] = {"spirit": spirit, "name": star_name, "ts": time.time()}
        log.debug("pre:cached", {"key": (path, user_id), "spirit": spirit, "name": star_name})
    else:
        log.debug("pre:missing-fields", {"user_id": user_id, "spirit": spirit})

    return JSONResponse({"effect": "allow"})

# ──────────────────────────────────────────────────────────────────────────────
# POST hook — after successful response, fire the dual chain
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/orchestrate/chain/post")
async def chain_post(request: Request):
    """
    Body from orchestrated fetch:
      { path, status, body }
    If /star-spirits/{project}/collect returns 2xx, trigger dual chain.
    Silent no-op if dual chain is not mounted (404/405/etc).
    """
    log.debug("post:begin")
    try:
        env = await request.json()
    except Exception as e:
        log.debug("post:json-error", repr(e))
        return JSONResponse({})

    log.debug("post:envelope", env)

    path: str = env.get("path") or ""
    status: int = int(env.get("status") or 0)
    body: Dict[str, Any] = env.get("body") or {}

    m = re.match(r"^/star-spirits/([^/]+)/collect$", path)
    if not m:
        log.debug("post:skip:path", {"path": path})
        return JSONResponse({})
    if not (200 <= status < 300):
        log.debug("post:skip:status", {"status": status})
        return JSONResponse({})

    project = m.group(1)
    user_id = str(body.get("user_id") or "").strip()

    # Recover spirit/name from pre-cache (best-effort)
    spirit, star_name = None, None
    if user_id:
        cached = _pre_cache.pop((path, user_id), None)
        if cached:
            spirit = cached.get("spirit")
            star_name = cached.get("name")
            log.debug("post:cache-hit", {"user_id": user_id, "spirit": spirit, "name": star_name})
        else:
            log.debug("post:cache-miss", {"user_id": user_id})
    else:
        log.debug("post:no-user-in-body")

    # Last-chance fallback for name
    if not star_name:
        star_name = _extract_star_name(spirit or "", {})

    # Prepare call to dual-dataentry node (if mounted)
    base = f"{request.url.scheme}://{request.url.netloc}"
    chain_url = f"{base}/chains/dual-dataentry/run"

    params = {
        "project": project,
        "star_name": star_name,
        "time_of_capture": _now_iso(),
        "star_id_a": _rand8(),
        "star_id_b": _rand8(),
        "label_a": "Schemes and Plots",
        "label_b": "The Chain Test Theory",
        # optional context
        "spirit": spirit or "",
        "user_id": user_id or "",
    }

    log.debug("post:trigger:attempt", {"url": chain_url, "params": params})

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SEC) as client:
            r = await client.get(chain_url, params=params)
            log.debug("post:trigger:resp", {"status": r.status_code})
    except Exception as e:
        log.debug("post:trigger:error", repr(e))

    return JSONResponse({})

# ──────────────────────────────────────────────────────────────────────────────
# Node + Module
# ──────────────────────────────────────────────────────────────────────────────

star_trigger_node = Node(
    name="Star Spirits – Trigger",
    kind=NodeKind.CHAIN,
    router=router,
    route_prefix="",  # hooks live at /orchestrate/chain/*
    meta={
        "label": "Star Spirits Trigger",
        "icon": "✦",
        "requires_tag": False,
    },
)

trigger_module = Module(
    name="Star Spirits Trigger",
    nodes=[star_trigger_node],
    version="0.1.0",
    description="Fires dual-dataentry chain after successful star-spirit capture via orchestrated fetch hooks",
    roles=set(),
)
