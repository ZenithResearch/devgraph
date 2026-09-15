# API implementation

The FastAPI adapter exposes typed work, proposal, export, initiative-observation,
monitor, health, and frontend routes over injected services. Request bodies are
strict. Data routes are scoped. Mutations are idempotent; PATCH is also
version-conditional. Problems use safe RFC 7807-compatible envelopes.

`GET /monitor/snapshot` also has one exact proof-of-possession receiver for
`devgraph.monitor.view.read.v1`. It verifies canonical session and request
headers through an injected exact adapter and does not authorize another route
or create a generic credential. Both its synchronous snapshot build and the
legacy bearer snapshot build run in the application threadpool so `/live`
remains responsive during a slow graph read.

Generated OpenAPI, Swagger UI, and ReDoc are disabled. See the complete
[HTTP API reference](../api.md).

## Verification

```bash
uv run pytest tests/api -q
```
