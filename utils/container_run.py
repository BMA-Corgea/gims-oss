"""One hardened container-invocation layer shared by every tool-execution path (Phase 6 / R15).

Before this module, two copies of the container command (``utils/runner_env.run_parser_container``
and ``utils/handlers/prepositional_phrase.run_prepositional_phrase_container``) each ran untrusted
tools as **root**, with a writable rootfs, **all** Linux capabilities, no ``no-new-privileges``,
and no pid/cpu/mem limits — i.e. only ``--network=none`` stood between an untrusted parser and the
host. This module centralises the runtime selection and the hardening flag-set so every container
launch gets the same baseline:

  --network=none            offline (overridable via GIMS_CONTAINER_NETWORK)
  --user / --userns         non-root: docker runs as the host uid:gid; rootless podman keeps-id
  --cap-drop=ALL            no Linux capabilities
  --security-opt=no-new-privileges
  --read-only               immutable rootfs; only declared mounts + a small /tmp tmpfs are writable
  --tmpfs=/tmp              writable scratch (nosuid,nodev) so the RO rootfs doesn't break tools
  --pids-limit/--memory/--cpus   bound fork-bomb / alloc / cpu DoS

Resource caps and the runtime come from :mod:`utils.config` (env-overridable). Runtime resolution
is **lazy** — a host with no docker/podman still imports this module; the missing-runtime error is
raised (as an :class:`~core.errors.AppError`) only when a run is actually attempted.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from utils import config
from utils.logger import get_logger
from core.errors import AppError

log = get_logger(__name__)


@dataclass(frozen=True)
class Mount:
    """One bind mount. ``mode`` is ``"ro"`` (default) or ``"rw"``."""
    host_path: str
    container_path: str
    mode: str = "ro"

    def to_flag(self) -> str:
        suffix = ":ro" if self.mode == "ro" else ""
        return f"{self.host_path}:{self.container_path}{suffix}"


def runtime_binary_or_raise() -> str:
    """Resolve the container runtime path, or raise a clear ``AppError`` if none is installed."""
    binary = config.container_runtime_binary()
    if not binary:
        raise AppError(
            "CONTAINER_RUNTIME_NOT_FOUND",
            "No container runtime (podman/docker) is available to run this tool. "
            "Install one, or enable in-process execution with GIMS_ALLOW_INPROCESS_TOOLS=true "
            "(trusted self-hosted installs only).",
            status=503,
            details={"preferred": config.container_runtime()},
        )
    return binary


def _user_flags(runtime_binary: str) -> List[str]:
    """Non-root execution flags, runtime-aware so bind-mount writes stay host-owned.

    podman (rootless): ``--userns=keep-id`` maps the unprivileged host user into the container,
    so the process is non-root AND files it writes to mounts are owned by the host user.
    docker: run as the host ``uid:gid`` (non-root) so the same ownership holds; if the server is
    (ill-advisedly) running as root, fall back to ``nobody`` rather than ever giving a tool uid 0.
    """
    base = os.path.basename(runtime_binary or "").lower()
    if "podman" in base:
        return ["--userns=keep-id"]
    try:
        uid, gid = os.getuid(), os.getgid()  # type: ignore[attr-defined]
    except AttributeError:
        return []  # non-POSIX host; nothing sensible to pass
    if uid == 0:
        uid = gid = 65534  # nobody:nogroup — never run an untrusted tool as root
    return [f"--user={uid}:{gid}"]


def hardening_flags(
    runtime_binary: str,
    *,
    network: Optional[str] = None,
    read_only: bool = True,
    tmpfs: Sequence[str] = ("/tmp",),
) -> List[str]:
    """The standard ``run`` flags (everything after ``<runtime> run``, before mounts/env/image)."""
    net = config.container_network() if network is None else network
    flags: List[str] = ["--rm", f"--network={net}"]
    flags += _user_flags(runtime_binary)
    flags += [
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--pids-limit={config.container_pids_limit()}",
        f"--memory={config.container_memory_limit()}",
        f"--cpus={config.container_cpu_limit()}",
    ]
    if read_only:
        flags.append("--read-only")
        size = config.container_tmpfs_size()
        for t in tmpfs:
            flags.append(f"--tmpfs={t}:rw,nosuid,nodev,mode=1777,size={size}")
    return flags


# Safe defaults so a read-only rootfs doesn't surprise common tools.
_DEFAULT_ENV = {
    "PYTHONDONTWRITEBYTECODE": "1",  # don't try to write .pyc onto the RO rootfs / :ro code mount
    "PYTHONUNBUFFERED": "1",
    "HOME": "/tmp",                  # a writable HOME (tmpfs) for tools that need one
}


def env_flags(env: Optional[Dict[str, str]] = None) -> List[str]:
    """``-e KEY=VALUE`` flags, with the safe defaults merged (caller values win)."""
    merged = dict(_DEFAULT_ENV)
    merged.update(env or {})
    out: List[str] = []
    for k, v in merged.items():
        out += ["-e", f"{k}={v}"]
    return out


def build_hardened_run_cmd(
    *,
    runtime_binary: str,
    image: str,
    mounts: Optional[Sequence[Mount]] = None,
    env: Optional[Dict[str, str]] = None,
    network: Optional[str] = None,
    read_only: bool = True,
    tmpfs: Sequence[str] = ("/tmp",),
    workdir: Optional[str] = None,
    extra_flags: Optional[Sequence[str]] = None,
    command: Optional[Sequence[str]] = None,
) -> List[str]:
    """Compose a full hardened ``run`` argv for the simple (single-output) case used by the
    container ExecutionBackend. Track-B callers with bespoke nested mounts use the building
    blocks (:func:`hardening_flags` / :func:`env_flags`) directly.

    ``command`` (if given) is appended AFTER the image, overriding the image's default CMD."""
    cmd = [runtime_binary, "run", *hardening_flags(runtime_binary, network=network,
                                                   read_only=read_only, tmpfs=tmpfs)]
    cmd += env_flags(env)
    for m in (mounts or []):
        cmd += ["-v", m.to_flag()]
    if workdir:
        cmd += ["--workdir", workdir]
    cmd += list(extra_flags or [])
    cmd.append(image)
    cmd += list(command or [])
    return cmd


def run_container(
    cmd: Sequence[str],
    *,
    timeout: Optional[int] = None,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    """Invoke a built container command, translating runtime failures into ``AppError``.

    A wall-clock ``timeout`` (default :func:`config.container_run_timeout`) kills a runaway tool.
    """
    t = config.container_run_timeout() if timeout is None else timeout
    log.debug("[container_run] exec:", " ".join(map(str, cmd)))
    try:
        return subprocess.run(
            list(cmd),
            timeout=t,
            capture_output=capture,
            text=True if capture else None,
        )
    except FileNotFoundError as e:
        raise AppError(
            "CONTAINER_RUNTIME_NOT_FOUND",
            "Container runtime not available",
            status=503,
            details={"binary": cmd[0] if cmd else None},
        ) from e
    except subprocess.TimeoutExpired as e:
        raise AppError(
            "CONTAINER_RUN_TIMEOUT",
            f"Tool container exceeded {t}s and was terminated",
            status=504,
            details={"timeout_s": t},
        ) from e
    except PermissionError as e:
        raise AppError(
            "CONTAINER_RUNTIME_PERMISSION_DENIED",
            "Permission denied invoking the container runtime",
            status=500,
        ) from e
