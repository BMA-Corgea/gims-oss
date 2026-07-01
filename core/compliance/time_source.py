"""Trusted/validated time for the compliance + audit trails (21 CFR Part 11 §11.70(i)) — P9.

The trail timestamp is only as trustworthy as the clock that stamps it. This module is the
ONE place compliance time comes from (:func:`now_iso_ms`), and it can VALIDATE the host clock
against an NTP reference so an operator/auditor can confirm the trail is not being stamped by a
drifted or tampered clock (:func:`get_time_status`, surfaced at ``GET /compliance/time``).

Design:
  * The wall-clock stamp itself is still the host clock (records also carry ``recorded_at_utc``,
    the server-receive time, so edge↔server skew is visible). What P9 adds is *validation*: a
    best-effort SNTP query that measures the offset and flags it when it exceeds tolerance.
  * The NTP check is best-effort and NEVER fatal — no network access (or no configured server)
    degrades gracefully to ``synced=None`` ("unvalidated host clock"), it does not block writes.
  * The offset measurement is injectable (``offset_fn``) so the decision logic is unit-tested
    without touching the network.
"""
from __future__ import annotations

import socket
import struct
import time as _time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

# Seconds between the NTP epoch (1900-01-01) and the Unix epoch (1970-01-01).
_NTP_EPOCH_DELTA = 2_208_988_800


def now_iso_ms() -> str:
    """UTC timestamp, millisecond precision, e.g. ``2026-06-26T12:00:00.123Z`` — the single
    canonical compliance-time format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def query_ntp_offset(server: str, timeout: float = 3.0) -> Optional[float]:
    """Best-effort SNTP: return ``ntp_time − host_time`` in seconds, or ``None`` on any failure.

    A positive value means the host clock is *behind* the reference. Uses a single UDP round
    trip and the send/receive midpoint to approximate the offset. Never raises.
    """
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.settimeout(timeout)
        try:
            packet = b"\x1b" + 47 * b"\0"  # LI=0, VN=3, Mode=3 (client)
            t0 = _time.time()
            client.sendto(packet, (server, 123))
            resp, _addr = client.recvfrom(48)
            t3 = _time.time()
        finally:
            client.close()
        if len(resp) < 48:
            return None
        transmit_seconds = struct.unpack("!12I", resp)[10]
        ntp_time = transmit_seconds - _NTP_EPOCH_DELTA
        return ntp_time - (t0 + t3) / 2.0
    except Exception:
        return None


@dataclass
class TimeStatus:
    now_utc: str
    source: str                       # "ntp_validated" | "system_clock"
    synced: Optional[bool]            # True/False, or None when unvalidated
    offset_seconds: Optional[float]
    skew_threshold_seconds: float
    ntp_server: Optional[str]
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def get_time_status(
    server: Optional[str],
    threshold_seconds: float,
    offset_fn: Callable[[str], Optional[float]] = query_ntp_offset,
) -> TimeStatus:
    """Assess the host clock against ``server`` (if configured). Pure decision logic given
    ``offset_fn`` — best-effort, never raises."""
    now = now_iso_ms()
    if not server:
        return TimeStatus(
            now, "system_clock", None, None, threshold_seconds, None,
            "No NTP reference configured (GIMS_TIME_NTP_SERVER); timestamps are the "
            "unvalidated host clock.",
        )
    offset = offset_fn(server)
    if offset is None:
        return TimeStatus(
            now, "system_clock", None, None, threshold_seconds, server,
            f"NTP reference '{server}' unreachable; falling back to the unvalidated host clock.",
        )
    synced = abs(offset) <= threshold_seconds
    note = (
        "Host clock within tolerance of the NTP reference."
        if synced
        else f"CLOCK SKEW {offset:+.3f}s exceeds ±{threshold_seconds}s tolerance — investigate."
    )
    return TimeStatus(now, "ntp_validated", synced, round(offset, 3), threshold_seconds, server, note)


# Process-local cache of the last validated TimeStatus. The NTP query is best-effort but can cost a
# UDP round trip (up to the 3 s timeout), so a polling UI badge memoizes it here, and hot paths (the
# grid duration endpoint) can read the last verdict WITHOUT ever issuing a query. `now_utc` is always
# refreshed to the current instant so the stamp never looks stale.
_STATUS_CACHE: dict = {"status": None, "at": 0.0, "key": None}
_STATUS_CACHE_TTL = 60.0  # seconds


def _restamp(st: TimeStatus) -> TimeStatus:
    return TimeStatus(now_iso_ms(), st.source, st.synced, st.offset_seconds,
                      st.skew_threshold_seconds, st.ntp_server, st.note)


def get_time_status_cached(
    server: Optional[str],
    threshold_seconds: float,
    *,
    ttl: float = _STATUS_CACHE_TTL,
    offset_fn: Callable[[str], Optional[float]] = query_ntp_offset,
) -> TimeStatus:
    """Like :func:`get_time_status` but memoized for ``ttl`` seconds (keyed on server+threshold), so
    repeated polls (a clock-trust badge) don't issue an NTP query each time. Never raises."""
    key = (server, round(float(threshold_seconds), 6))
    c = _STATUS_CACHE
    if c["status"] is not None and c["key"] == key and (_time.monotonic() - c["at"]) < ttl:
        return _restamp(c["status"])
    status = get_time_status(server, threshold_seconds, offset_fn)
    _STATUS_CACHE.update(status=status, at=_time.monotonic(), key=key)
    return status


def peek_time_status() -> Optional[TimeStatus]:
    """Return the last cached :class:`TimeStatus` WITHOUT ever issuing a network query (``None`` if
    never populated). For hot paths that want a clock-trust signal but must not block on NTP."""
    st = _STATUS_CACHE["status"]
    return _restamp(st) if st is not None else None
