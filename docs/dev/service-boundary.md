# Service boundary

The service boundary is the `AuthorizedWorkGraph` facade exposed through the
thin FastAPI adapter. Clients do not receive storage, repositories, policy,
outbox, or Neo4j handles. `create_app` requires those services to be injected.

The repository includes both an explicit read-only, memory-backed local fixture
and a private loopback-only production composition backed by Neo4j. The latter
uses one owner-provisioned local capability fixed to `devgraph.read`; absent or
wrong credentials remain HTTP 401 and mutations remain HTTP 403. The
`fail-closed` and legacy `disabled` modes retain denial-only behavior.
Neither composition provides public remote transport or direct Neo4j access to
clients. See [service boundaries](../boundaries.md), the [API reference](../api.md),
and the [private macOS runbook](../runbooks/local-macos-self-host.md).

## Verification

```bash
uv run pytest tests/api tests/integration -q
```
