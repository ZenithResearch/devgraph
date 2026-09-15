"""Bounded, read-only inspection of the configured Neo4j PID file.

Unloading a launch agent does not prove its database process exited. These
checks never signal a process or remove a PID/lock file. An ambiguous result
holds lifecycle admission for operator inspection.
"""

from __future__ import annotations

import os
import stat
import time
from collections.abc import Callable
from math import isfinite
from typing import Any

from devgraph.local_host import LocalHostConfig


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def neo4j_process_snapshot(
    config: LocalHostConfig,
    *,
    probe: Callable[[int], bool] = _process_exists,
) -> dict[str, Any]:
    """Report only PID/state/reason; a live PID is not identity proof."""

    unknown = {"pid": None, "state": "unknown", "reason": "neo4j_pid_unavailable"}
    if not config.data_root.is_dir() or (
        config.storage_mode == "mounted_volume" and not config.availability_path.is_mount()
    ):
        return {**unknown, "reason": "configured_storage_unavailable"}
    path = config.data_root / "neo4j" / "run" / "neo4j.pid"
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
        )
    except FileNotFoundError:
        return {"pid": None, "state": "stopped", "reason": "neo4j_pid_absent"}
    except OSError:
        return unknown
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o022
            or not 1 <= info.st_size <= 32
        ):
            return unknown
        raw = os.read(descriptor, 33).strip()
        if not raw.isdigit() or len(raw) > 10:
            return unknown
        pid = int(raw)
        if not 1 < pid <= 2_147_483_647:
            return unknown
    except OSError:
        return unknown
    finally:
        os.close(descriptor)
    try:
        exists = probe(pid)
    except OSError:
        return {"pid": pid, "state": "unknown", "reason": "neo4j_process_unavailable"}
    if type(exists) is not bool:
        return unknown
    return {
        "pid": pid,
        "state": "running" if exists else "stopped",
        "reason": "neo4j_pid_alive" if exists else "neo4j_pid_stale",
    }


def wait_for_neo4j_exit(
    config: LocalHostConfig,
    *,
    tracked_pid: int | None = None,
    probe: Callable[[int], bool] = _process_exists,
    timeout_seconds: float = 120.0,
    snapshot: Callable[..., dict[str, Any]] = neo4j_process_snapshot,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if (
        type(timeout_seconds) not in {int, float}
        or not isfinite(timeout_seconds)
        or not 0 <= timeout_seconds <= 120
    ):
        raise ValueError("shutdown timeout must be between zero and 120 seconds")
    if tracked_pid is not None and (
        type(tracked_pid) is not int or not 1 < tracked_pid <= 2_147_483_647
    ):
        raise ValueError("invalid tracked process")
    deadline = clock() + timeout_seconds
    while True:
        result = snapshot(config)
        if result["state"] == "unknown":
            return {**result, "successful": False}
        if tracked_pid is not None:
            try:
                exists = probe(tracked_pid)
            except OSError:
                exists = None
            if type(exists) is not bool:
                return {
                    "pid": tracked_pid,
                    "state": "unknown",
                    "reason": "neo4j_process_unavailable",
                    "successful": False,
                }
            if exists:
                # Neo4j may remove its PID file before JVM shutdown completes.
                # PID reuse conservatively holds admission; never signal here.
                result = {
                    "pid": tracked_pid,
                    "state": "running",
                    "reason": "tracked_neo4j_pid_alive",
                }
        if result["state"] != "running":
            return {**result, "successful": result["state"] == "stopped"}
        if clock() >= deadline:
            return {**result, "reason": "neo4j_shutdown_timeout", "successful": False}
        sleeper(min(0.2, max(0.0, deadline - clock())))
