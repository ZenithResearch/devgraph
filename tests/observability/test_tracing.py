"""Commit 5: no-op tracing seam.

A minimal tracer interface carrying correlation context, with the
side-effect-free no-op implementation. An OpenTelemetry adapter is a
future implementation of this seam — no OTel dependency exists in v0.
"""

from __future__ import annotations

from devgraph.observability.tracing import NoOpTracer, Tracer


class TestNoOpTracer:
    def test_satisfies_tracer_protocol(self) -> None:
        assert isinstance(NoOpTracer(), Tracer)

    def test_span_is_a_deterministic_side_effect_free_context(self) -> None:
        tracer = NoOpTracer()
        for _ in range(2):
            with tracer.span("attach", correlation_id="corr-0001") as span:
                assert span.name == "attach"
                assert span.correlation_id == "corr-0001"

    def test_nested_spans_work(self) -> None:
        tracer = NoOpTracer()
        with tracer.span("outer", correlation_id="c1"):
            with tracer.span("inner", correlation_id="c1") as inner:
                assert inner.name == "inner"

    def test_no_opentelemetry_import(self) -> None:
        import devgraph.observability.tracing as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "opentelemetry" not in source
