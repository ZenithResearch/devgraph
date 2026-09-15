# Observability

The observability package implements storage attribution and local seams; it
does not connect to an external telemetry system.

## Implemented components

- `HermesSessionRef`: validated reference metadata with pinned identity fields.
- Attachment service: stores `ATTRIBUTED_TO_HERMES_SESSION` relationships.
- Ingestion protocol and manual-import adapter: normalizes explicitly supplied
  synthetic observation records.
- Standard-library structured logging: carries safe correlation fields through
  the redaction helper.
- Tracing protocol and no-op implementation: lets callers instrument without a
  configured exporter.

The separately packaged [Hermes plugin](user/agent-integrations.md) exposes local
Devgraph CLI operations. It does not implement HermesSessionRef ingestion or
telemetry. There is no transcript importer, telemetry collector, metrics server,
OpenTelemetry exporter, or remote trace backend in this package. Test fixtures
do not establish those integrations.

```bash
uv run pytest tests/observability -q
```
