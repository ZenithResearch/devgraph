# Compatibility notes for v0.1.0

This release is additive relative to the existing Devgraph v0 documentation and runtime model.

- Existing runtime labels, predicates, statuses, constraints, persisted records, HTTP payloads, and decoders are unchanged.
- Devgraph becomes the canonical authority for the cross-Zenith ontology; the public domain remains a distribution surface rather than a competing source.
- `ZenithRepository` is added to the canonical vocabulary with `runtimeLabel: false`. It does not add a Neo4j label, constraint, migration, API resource, or authority claim.
- Semantic operation names and schema IDs are published for transport-independent discovery. Existing HTTP bindings remain valid; future secS bindings must map receiver-local opcodes to these names.
- Consumers must accept unknown additive classes, predicates, operations, and fields within the same major version. Consumers must fail closed on an unsupported major version or a mismatched pinned digest.

No legacy field or stored representation is removed by this release.
