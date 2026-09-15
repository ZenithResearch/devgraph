# Zenith Ontology v0.3.0

v0.3.0 adds the exact `devgraph.issue.create.v1` operation and its bounded
`EventReceipt` telemetry profile. The operation is an Issue-only specialization
that hands one verified request to canonical `create_work_object`; it is not a
generic machine-operation multiplexer and assigns no transport, route, handler,
or secS opcode.

For this exact operation, `request_digest_sha256` and
`idempotency_key_digest_sha256` are both required lowercase SHA-256 hex values.
The request digest extends duplicate-scope comparison. Neither digest changes
the principal-scoped `idempotency_claim_digest`, neither has a uniqueness
constraint, and the raw-key digest is distinct from the legacy
`idempotency_key_digest` field.

The v0.1.0 and v0.2.0 bundles are unchanged and remain supported. No class,
predicate, lifecycle, Neo4j constraint, or migration is added by this release.

