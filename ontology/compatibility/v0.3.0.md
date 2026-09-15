# Zenith ontology v0.3.0 compatibility

v0.3.0 is additive over v0.2.0. The immutable v0.1.0 and v0.2.0 releases remain
supported and byte-identical.

- Existing operations and generic receipt decoding are unchanged.
- `devgraph.issue.create.v1` is an exact Issue-create specialization with no
  ontology-assigned transport, handler, route, or numeric opcode.
- Its receipts require both `request_digest_sha256` and
  `idempotency_key_digest_sha256`; other operations may omit them.
- Duplicate retries compare the request digest but do not rewrite the original
  receipt or change `idempotency_claim_digest` identity.
- `idempotency_key_digest_sha256` is telemetry, not the legacy
  `idempotency_key_digest`, and has no uniqueness constraint.
- No runtime label, relationship, lifecycle transition, or migration changes.

Consumers must continue pinning a version and manifest bundle digest. A
consumer that does not implement the exact operation may reject it without
affecting older catalog entries.

