# OPEN-CORE STUB — auto-written by tools/oss_export. The GIMS public build ships without
# the login/auth, roles, and 21 CFR Part 11 compliance layers; this file preserves the
# import surface the rest of the app needs, implemented as an OPEN single-user no-op.
# Do NOT deploy the open-core build as a shared/multi-user service.
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

from core.orchestration.node import Node, NodeKind
# time_source is a KEPT general trusted-time utility (not part of the removed HMAC chain).
from core.compliance.time_source import get_time_status, get_time_status_cached
from utils.config import time_ntp_server, time_skew_threshold_seconds

router = APIRouter(tags=["Time"])


@router.get("/compliance/time")
async def _time_status(fresh: bool = False):
    """Trusted-time / clock-drift status. Kept in the open build because the runlog clock
    badge and the Duration time-adjective tickers poll it. No compliance data is involved."""
    server, threshold = time_ntp_server(), time_skew_threshold_seconds()
    status = get_time_status(server, threshold) if fresh else get_time_status_cached(server, threshold)
    return JSONResponse(status.to_dict())


@router.get("/compliance/inject.js")
async def _compliance_inject_js():
    return PlainTextResponse("/* open-core: compliance logging disabled */",
                             media_type="application/javascript")


async def _append_event(*_a, **_k):
    """No-op: the open build keeps no compliance trail. Present for the gate handler's import."""
    return {}


compliance_node = Node(
    name="Compliance",
    kind=NodeKind.INFRASTRUCTURE,
    router=router,
    route_prefix="",
    meta={
        "icon": "\U0001f552",
        "label": "Time status",
        "provides_inject": ["/compliance/inject.js"],
    },
)
