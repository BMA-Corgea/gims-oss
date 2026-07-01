# OPEN-CORE: the 21 CFR Part 11 HMAC audit chain (chain/canonical/verify) is removed.
# Only the general trusted-time utility survives; re-export it for `from core.compliance import ...`.
from core.compliance.time_source import (
    TimeStatus,
    now_iso_ms,
    get_time_status,
    get_time_status_cached,
    peek_time_status,
)

__all__ = ["TimeStatus", "now_iso_ms", "get_time_status", "get_time_status_cached", "peek_time_status"]
