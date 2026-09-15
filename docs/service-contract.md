# Service contract

The implemented service contract is the combination of
`AuthorizedWorkGraph`, `create_app(ApiServices(...))`, and the typed envelopes
in `src/devgraph/api/schemas.py`.

- Clients enter through facade/API operations, never storage credentials.
- Every data operation verifies a credential and exact scope.
- Every HTTP mutation requires an idempotency key and creates an atomic local
  receipt; PATCH also requires the current version.
- Exports use dedicated scopes and deterministic redaction policy.
- Errors are safe RFC 7807-compatible problem envelopes.
- `/live` is process liveness; `/ready` is dependency/migration readiness.

See the [API reference](api.md), [authorization](auth.md), [events](events.md),
and [technical reference](technical-reference.md) for the exact current shape.
