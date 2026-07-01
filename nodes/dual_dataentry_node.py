# nodes/dual_dataentry_node.py

from __future__ import annotations

import random
import string
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter, Query
import httpx

from core.errors import AppError
from core.orchestration.node import Node, NodeKind
from api import i_o  # provides get_url_base(project_path)

# Phase 6: this node calls the noun-workbench API over HTTP (httpx, via
# i_o.get_url_base) — it does NOT import the GUI router, so orchestration no longer
# depends on the GUI layer.

# ──────────────────────────────────────────────────────────────────────────────
# Debug
# ──────────────────────────────────────────────────────────────────────────────

from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _iso_now() -> str:
    s = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    log.debug("iso_now", s)
    return s

def _rand8() -> str:
    rid = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    log.debug("rand8", rid)
    return rid

def _make_payload(params: Dict[str, Any], which: str) -> Dict[str, Any]:
    """
    Build the Star Spirit Lore payload.
    - Star Spirit Name = exact clicked name (no label suffixing)
    - Star Spirit ID   = 8-char A–Z/0–9 unless star_id_a/star_id_b is provided
    - Time of Capture  = provided or now()
    """
    name = (params.get("star_name") or "Star Spirit").strip()
    toc  = (params.get("time_of_capture") or _iso_now()).strip()

    override = (params.get(f"star_id_{which}") or "").strip()
    sid = override or _rand8()

    payload = {
        "Star Spirit Name": name,
        "Star Spirit ID":   sid,
        "Time of Capture":  toc,
        # Intentionally omit "Star Spirit Ability"
    }
    log.debug(f"payload:{which}", payload)
    return payload

# ──────────────────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/chains/dual-dataentry", tags=["Dual DataEntry"])
log.debug("router:init", {"prefix": "/chains/dual-dataentry"})

@router.get("/run")
async def run_dual_dataentry(
    project: str = Query(..., description="Project name (e.g., LIMS-System)"),
    # Optional knobs (trigger usually fills these)
    star_name: str = Query("Star Spirit"),
    time_of_capture: str = Query(""),
    star_id_a: str = Query(""),
    star_id_b: str = Query(""),
    # extra passthrough (handy for logs/traceability)
    label_a: str = Query("Schemes and Plots"),
    label_b: str = Query("The Chain Test Theory"),
    spirit: str = Query("", description="s1..s7"),
    user_id: str = Query("", description="capturing user (uuid)"),
):
    """
    Step 1: POST /noun/Star%20Spirit%20Lore/create (A)
    Step 2: POST /noun/Star%20Spirit%20Lore/create (B)

    Returns both responses; raises 502 if either fails at HTTP level.
    """
    log.debug("run:begin", {
        "project": project, "star_name": star_name, "spirit": spirit,
        "user_id": user_id, "label_a": label_a, "label_b": label_b
    })

    # Resolve same-origin base from project (lives outside core)
    base = i_o.get_url_base(Path("projects") / project)
    url = f"{base}/api/noun_workbench/Star%20Spirit%20Lore/create?project={project}"
    log.debug("resolved:base", {"base": base, "url": url})

    params = {
        "star_name": star_name,
        "time_of_capture": time_of_capture,  # empty -> _iso_now()
        "star_id_a": star_id_a,
        "star_id_b": star_id_b,
        "label_a": label_a,
        "label_b": label_b,
        "spirit": spirit,
        "user_id": user_id,
    }

    body_a = _make_payload(params, "a")
    body_b = _make_payload(params, "b")

    results = []

    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        # ── Step 1 ────────────────────────────────────────────────────────────
        log.debug("step:1:POST", {"url": url, "body": body_a})
        try:
            r1 = await client.post(url, json=body_a)
            log.debug("step:1:resp", {"status": r1.status_code})
            r1.raise_for_status()
            data1 = r1.json() if "application/json" in (r1.headers.get("content-type") or "") else {"text": r1.text}
            results.append({"step": "A", "status": r1.status_code, "response": data1})
        except Exception as e:
            log.debug("step:1:error", repr(e))
            raise AppError(
                "DUAL_DATAENTRY_WRITE_FAILED",
                f"Dual DataEntry write failed at step A: {e}",
                status=502,
                details={"step": "A", "error": str(e), "project": project},
            )

        # ── Step 2 ────────────────────────────────────────────────────────────
        log.debug("step:2:POST", {"url": url, "body": body_b})
        try:
            r2 = await client.post(url, json=body_b)
            log.debug("step:2:resp", {"status": r2.status_code})
            r2.raise_for_status()
            data2 = r2.json() if "application/json" in (r2.headers.get("content-type") or "") else {"text": r2.text}
            results.append({"step": "B", "status": r2.status_code, "response": data2})
        except Exception as e:
            log.debug("step:2:error", repr(e))
            raise AppError(
                "DUAL_DATAENTRY_WRITE_FAILED",
                f"Dual DataEntry write failed at step B: {e}",
                status=502,
                details={"step": "B", "error": str(e), "project": project},
            )

    ok = True
    # If the noun API returns {"ok": False}, we still return 200 here with details;
    # callers can inspect results[*].response.ok
    log.debug("run:end", {"ok": ok, "results": results})
    return {"ok": ok, "writes": results}

# ──────────────────────────────────────────────────────────────────────────────
# Node
# ──────────────────────────────────────────────────────────────────────────────

dual_dataentry_node = Node(
    name="Dual DataEntry Writer",
    kind=NodeKind.CHAIN,
    router=router,  # <-- now mounted via FastAPI
    meta={"label": "Dual DataEntry"},
)
log.debug("node:created", {"name": dual_dataentry_node.name})
