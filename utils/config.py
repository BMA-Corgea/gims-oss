"""Typed configuration accessors — env-first, file fallback, never a committed default.

Replaces scattered ``os.getenv`` reads and hard-coded flags (e.g. ``RDS_ENABLED = False``
in ``api/manifest/resolver.py``). One place decides how each setting is read, so enabling
cloud, raising the log level, or supplying the JWT secret is configuration, not a code edit.
"""
from __future__ import annotations

import os
import secrets
import shutil
from functools import lru_cache

from utils.paths import repo_root

_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def _int_env(name: str, default: int) -> int:
    """Read a positive int from ``name``; fall back to ``default`` on missing/garbage."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        val = int(raw.strip())
    except ValueError:
        return default
    return val if val > 0 else default


def log_level() -> str:
    """Global log level (default ``WARNING``); replaces 49 per-file ``DEBUG_ENABLED`` flags."""
    return (os.environ.get("GIMS_LOG_LEVEL") or "WARNING").upper()


def rds_enabled() -> bool:
    """Whether to route records to Postgres/RDS (``GIMS_RDS_ENABLED``, default false)."""
    return _truthy(os.environ.get("GIMS_RDS_ENABLED"))


def storage_provider() -> str:
    """Active storage provider: ``local`` | ``aws`` | ``azure`` | ``gcp`` (default ``local``)."""
    return (os.environ.get("GIMS_STORAGE_PROVIDER") or "local").lower()


def allow_inprocess_tools() -> bool:
    """Whether in-process tool execution is permitted (default false; container is default)."""
    return _truthy(os.environ.get("GIMS_ALLOW_INPROCESS_TOOLS"))


# ──────────────────────────────────────────────────────────────────────────────
# Execution backend / container sandbox (Phase 6 / R15)
#
# Custom (untrusted) tools — parsers and prepositional phrases — run by default in a
# hardened container (decision 4: container isolation is the default, in-process is the
# opt-in for trusted self-hosted installs via ``allow_inprocess_tools``). These accessors
# centralise the runtime selection, resource caps, and artifact-validation policy so the
# sandbox can be tuned by configuration, not code edits. All env-first with safe defaults.
# ──────────────────────────────────────────────────────────────────────────────

def container_runtime() -> str:
    """Preferred container runtime: ``auto`` | ``podman`` | ``docker`` (``GIMS_CONTAINER_RUNTIME``).

    ``auto`` (default) prefers rootless **podman**, falling back to docker — matching the
    long-standing ``shutil.which("podman") or "docker"`` convention and the R15 steer to
    prefer rootless podman for untrusted execution."""
    return (os.environ.get("GIMS_CONTAINER_RUNTIME") or "auto").lower()


def container_runtime_binary() -> str | None:
    """Resolve the container runtime executable path, or ``None`` if unavailable.

    Lazy on purpose: resolved at run time, never at import, so a host with no container
    runtime can still import the modules (the in-process/wasm backends stay usable, and the
    caller raises a clear ``AppError`` only when a container run is actually attempted)."""
    pref = container_runtime()
    if pref == "podman":
        return shutil.which("podman")
    if pref == "docker":
        return shutil.which("docker")
    # auto: prefer rootless podman, fall back to docker
    return shutil.which("podman") or shutil.which("docker")


def container_base_image() -> str:
    """Base image used to run single-file custom tools in a container (``GIMS_CONTAINER_BASE_IMAGE``,
    default ``python:3.12-slim``). Tools that declare ``dependencies`` get a derived, pip-installed
    image cached off this base."""
    return (os.environ.get("GIMS_CONTAINER_BASE_IMAGE") or "python:3.12-slim").strip()


def container_network() -> str:
    """Network mode for tool containers (``GIMS_CONTAINER_NETWORK``, default ``none``).

    Default ``none`` keeps untrusted tools offline (the one good isolation property the
    legacy runners already had); override only for a deployment that genuinely needs it."""
    return (os.environ.get("GIMS_CONTAINER_NETWORK") or "none").strip()


def container_memory_limit() -> str:
    """Per-container memory cap as a docker/podman size string (``GIMS_CONTAINER_MEMORY``,
    default ``2g``). Bounds fork/alloc DoS while staying generous for real lab tools."""
    return (os.environ.get("GIMS_CONTAINER_MEMORY") or "2g").strip()


def container_cpu_limit() -> str:
    """Per-container CPU cap passed to ``--cpus`` (``GIMS_CONTAINER_CPUS``, default ``2``)."""
    return (os.environ.get("GIMS_CONTAINER_CPUS") or "2").strip()


def container_pids_limit() -> int:
    """Per-container process cap passed to ``--pids-limit`` (``GIMS_CONTAINER_PIDS``,
    default ``256``) — blocks fork-bombs."""
    return _int_env("GIMS_CONTAINER_PIDS", 256)


def chain_handler_timeout() -> float:
    """Per-handler wall-clock cap (seconds) for chain/trigger event handlers
    (``GIMS_CHAIN_HANDLER_TIMEOUT``, default ``10``). A hung side-effect handler is logged and
    abandoned rather than blocking the publish loop forever."""
    raw = os.environ.get("GIMS_CHAIN_HANDLER_TIMEOUT")
    if raw and raw.strip():
        try:
            v = float(raw.strip())
            if v > 0:
                return v
        except ValueError:
            pass
    return 10.0


def hook_call_timeout() -> float:
    """Wall-clock cap (seconds) for a single orchestrate guard-hook HTTP call
    (``GIMS_HOOK_CALL_TIMEOUT``, default ``10``). Separate from the 120s archive-proxy timeout so a
    hung policy hook fails fast (and, per :func:`fail_closed_hooks`, closed)."""
    raw = os.environ.get("GIMS_HOOK_CALL_TIMEOUT")
    if raw and raw.strip():
        try:
            v = float(raw.strip())
            if v > 0:
                return v
        except ValueError:
            pass
    return 10.0


def fail_closed_hooks() -> bool:
    """Whether deny-capable orchestrate PRE guard hooks fail CLOSED on error/timeout/garbage
    (``GIMS_FAIL_CLOSED_HOOKS``, default TRUE — owner decision).

    A genuinely-absent hook (HTTP 404/405 = not configured) still ALLOWS; but a guard hook that
    errors, times out, or returns non-JSON must DENY rather than silently letting the request
    through. Set to false only to restore the legacy fail-open behaviour."""
    raw = os.environ.get("GIMS_FAIL_CLOSED_HOOKS")
    if raw is None or not raw.strip():
        return True  # secure default
    return _truthy(raw)


def container_tmpfs_size() -> str:
    """Size cap for the container's writable ``/tmp`` tmpfs (``GIMS_CONTAINER_TMPFS_SIZE``,
    default ``512m``). Bounds in-container scratch growth (defense-in-depth on top of ``--memory``,
    which already charges tmpfs writes to the container cgroup)."""
    return (os.environ.get("GIMS_CONTAINER_TMPFS_SIZE") or "512m").strip()


def container_run_timeout() -> int:
    """Wall-clock seconds before a tool container is killed (``GIMS_CONTAINER_TIMEOUT``,
    default ``300``)."""
    return _int_env("GIMS_CONTAINER_TIMEOUT", 300)


# Default artifact type-whitelist (owner decision, 2026-06-24): the file types the real
# tools legitimately emit — parsers→csv, coa_generator→pdf/docx, the post-doc step→json/txt
# manifests — plus xlsx/png and html (owner writes reports in html; inert to the host, no
# riskier than the txt/json already allowed; served as an attachment, never inline, so it
# cannot execute same-origin downstream).
_DEFAULT_ARTIFACT_TYPES = ("csv", "json", "pdf", "docx", "xlsx", "png", "txt", "html")


def allowed_artifact_types() -> set[str]:
    """Lowercase extensions (no dot) the artifact broker may copy OUT of the sandbox into the
    project tree (``GIMS_ALLOWED_ARTIFACT_TYPES``, comma-separated, overrides the default set).
    Anything not listed is rejected by the host gateway after magic-byte verification."""
    raw = os.environ.get("GIMS_ALLOWED_ARTIFACT_TYPES")
    if raw and raw.strip():
        return {p.strip().lower().lstrip(".") for p in raw.split(",") if p.strip()}
    return set(_DEFAULT_ARTIFACT_TYPES)


def artifact_max_bytes() -> int:
    """Per-file artifact size cap in bytes (``GIMS_ARTIFACT_MAX_BYTES``, default 100 MiB)."""
    return _int_env("GIMS_ARTIFACT_MAX_BYTES", 100 * 1024 * 1024)


def artifact_max_count() -> int:
    """Max number of artifacts a single run may emit (``GIMS_ARTIFACT_MAX_COUNT``, default 500)."""
    return _int_env("GIMS_ARTIFACT_MAX_COUNT", 500)


def audit_engine() -> bool:
    """Whether the integrity auditor validates instances through the one validation engine
    (``GIMS_AUDIT_ENGINE``, **default true**).

    The legacy auditor read schema keys (``required_fields``/``field_types``) that do not exist on
    disk, so its noun-instance checks silently no-op (R19). The engine-backed path routes those
    checks through ``core.words.validation`` — the SAME contract the editor + workbench use — so
    they are real (required/type/date/reference). It is ON by default so the audit actually audits;
    set ``GIMS_AUDIT_ENGINE=false`` to fall back to the legacy (no-op) noun-instance checks if a
    deployment needs to defer acting on the pre-existing data problems it surfaces."""
    raw = os.environ.get("GIMS_AUDIT_ENGINE")
    # Unset OR empty/whitespace => default (on); an explicit value is parsed normally.
    return True if not (raw and raw.strip()) else _truthy(raw)


@lru_cache(maxsize=1)
def jwt_secret() -> str:
    """The single JWT signing/verification secret for the whole app.

    Resolution: ``GIMS_JWT_SECRET`` env → ``.dev_jwt_secret`` file (repo root) → a freshly
    generated value persisted to that (git-ignored) file so it is stable across restarts.
    No secret is ever hard-coded in source, so nothing security-sensitive is committed.
    """
    env = os.environ.get("GIMS_JWT_SECRET")
    if env:
        return env

    secret_file = repo_root() / ".dev_jwt_secret"
    if secret_file.exists():
        try:
            txt = secret_file.read_text(encoding="utf-8").strip()
            if txt:
                return txt
        except OSError:
            pass

    value = secrets.token_urlsafe(48)
    try:
        secret_file.write_text(value, encoding="utf-8")
    except OSError:
        pass
    return value


def time_ntp_server() -> str | None:
    """Optional NTP reference (e.g. ``pool.ntp.org`` or an internal time server) used to
    VALIDATE the host clock that stamps the compliance/audit trail — 21 CFR Part 11 §11.70(i)
    "reliable, secure, validated time source" (P9). When unset, timestamps are the unvalidated
    host clock and ``/compliance/time`` reports them as such. The check is best-effort and
    never fatal."""
    return os.environ.get("GIMS_TIME_NTP_SERVER") or None


def time_skew_threshold_seconds() -> float:
    """Max tolerated |host-clock − NTP| before the clock is flagged un-synced (default 2s)."""
    raw = os.environ.get("GIMS_TIME_SKEW_THRESHOLD")
    try:
        return float(raw) if raw else 2.0
    except ValueError:
        return 2.0


def compliance_dsn() -> str | None:
    """Optional dedicated Postgres DSN the app uses for compliance-trail RUNTIME ops
    (INSERT/SELECT), so it can connect as a LEAST-PRIVILEGE role (no UPDATE/DELETE/TRUNCATE)
    — 21 CFR Part 11 custody defense-in-depth beyond the append-only triggers (P8). When
    unset (the default, and always under local SQLite), compliance uses the normal
    ``nodes_db`` connection. Schema creation always uses the owner connection, not this.
    See ``migrations/compliance_restricted_role.sql`` for the role/grants to create.
    """
    return os.environ.get("GIMS_COMPLIANCE_DSN") or None


def require_esign_reauth() -> bool:
    """Whether an e-signature must be re-authenticated (fresh password) server-side at the
    moment of signing — 21 CFR Part 11 §11.200 two-component. Default ON; set
    ``GIMS_REQUIRE_ESIGN_REAUTH=0`` only as a deliberate, documented escape hatch. Ordinary
    (non-signature) compliance logging is unaffected either way."""
    raw = os.environ.get("GIMS_REQUIRE_ESIGN_REAUTH")
    return True if raw is None else _truthy(raw)


def compliance_hmac_key() -> bytes:
    """The HMAC key that signs the compliance/audit tamper-evidence chain.

    This is what makes the chain *keyed* (HMAC-SHA256) rather than a bare, publicly
    recomputable SHA-256: forging a valid chain requires this key, which a holder of the
    ``nodes.db`` file does not possess. It is DISTINCT from :func:`jwt_secret` so that
    rotating login tokens never invalidates the historical compliance chain (and vice
    versa), and so the two can live in different custody (e.g. a server-only secret store
    for the compliance key under RDS).

    Resolution mirrors :func:`jwt_secret`: ``GIMS_COMPLIANCE_HMAC_KEY`` env →
    ``.compliance_hmac_key`` file (repo root, git-ignored, 0600) → a freshly generated
    value persisted to that file so the chain is stable across restarts. Under RDS/cloud
    this is the seam where a ``SecretProvider`` would supply a server-held key instead.
    """
    env = os.environ.get("GIMS_COMPLIANCE_HMAC_KEY")
    if env:
        return env.encode("utf-8")

    key_file = repo_root() / ".compliance_hmac_key"
    if key_file.exists():
        try:
            txt = key_file.read_text(encoding="utf-8").strip()
            if txt:
                return txt.encode("utf-8")
        except OSError:
            pass

    value = secrets.token_urlsafe(48)
    try:
        key_file.write_text(value, encoding="utf-8")
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    return value.encode("utf-8")
