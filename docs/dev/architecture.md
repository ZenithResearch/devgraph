# Architecture

Devgraph is layered around an injected `GraphStorage` protocol. Domain
repositories and relationship services sit above storage. `AuthorizedWorkGraph`
adds per-call verification, exact-scope enforcement, and safe audit identifiers.
The FastAPI routes adapt HTTP to that facade; mutation routes additionally use
the transactional outbox. The official frontend is a same-origin read client.

No layer below the API starts a server or chooses deployment configuration.
Read the [technical reference](../technical-reference.md) for the complete
current architecture.

## Verification

```bash
uv run pytest -q
bash docs/dev/verification.md
```
