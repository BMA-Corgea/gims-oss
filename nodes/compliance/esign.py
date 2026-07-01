# OPEN-CORE STUB — auto-written by tools/oss_export. The GIMS public build ships without
# the login/auth, roles, and 21 CFR Part 11 compliance layers; this file preserves the
# import surface the rest of the app needs, implemented as an OPEN single-user no-op.
# Do NOT deploy the open-core build as a shared/multi-user service.
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Request


async def enforce_gate_signoff_reauth(request: Request, project: str,
                                      password: Optional[str] = None,
                                      action: str = "gate_signoff") -> Optional[str]:
    """Open build: no e-signature re-auth. Returning None tells the gate handler the signing
    control is disabled, so the gate completes and no compliance event is recorded."""
    return None


async def enforce_two_component_signature(project: str, user_id: str,
                                          signer: Optional[str] = None,
                                          esig: Optional[Dict[str, Any]] = None):
    return None
