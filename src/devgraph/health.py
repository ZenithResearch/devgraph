from __future__ import annotations

from typing import Protocol

from devgraph.storage.base import HealthStatus


class Healthcheckable(Protocol):
    def health(self) -> HealthStatus: ...


def liveness(storage: Healthcheckable | None = None) -> HealthStatus:
    if storage is None:
        return HealthStatus(live=True, ready=False, detail="process live; no storage configured")
    try:
        status = storage.health()
    except Exception:  # liveness should not fail just because storage readiness failed.
        return HealthStatus(live=True, ready=False, detail="storage healthcheck failed")
    return HealthStatus(live=True, ready=status.ready, detail=status.detail)


def readiness(storage: Healthcheckable) -> HealthStatus:
    try:
        status = storage.health()
    except Exception:
        return HealthStatus(live=True, ready=False, detail="storage healthcheck failed")
    if not status.ready:
        return status
    return HealthStatus(live=status.live, ready=True, detail=status.detail)
