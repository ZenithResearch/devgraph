"""No-op tracing seam carrying correlation context.

v0 ships only this deterministic, side-effect-free implementation —
the tracing provider is not configured in this environment, and the
GitHub #12 spec authorizes the no-op abstraction path. An
OpenTelemetry adapter is a future implementation of the
:class:`Tracer` protocol; no OTel dependency exists here.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SpanContext:
    name: str
    correlation_id: str


@runtime_checkable
class Tracer(Protocol):
    def span(
        self, name: str, *, correlation_id: str
    ) -> AbstractContextManager[SpanContext]: ...


class NoOpTracer:
    """Deterministic tracer that records nothing and never fails."""

    @contextmanager
    def span(self, name: str, *, correlation_id: str):
        yield SpanContext(name=name, correlation_id=correlation_id)
