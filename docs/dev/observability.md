# Observability implementation

The package provides strict Hermes session references, graph attachment,
manual normalized ingestion, correlation-carrying redacted logging, and a
tracing protocol with a no-op implementation. It has no Hermes runtime or
telemetry backend integration. See [observability](../observability.md).

## Verification

```bash
uv run pytest tests/observability -q
```
