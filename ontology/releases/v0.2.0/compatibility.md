# Zenith ontology v0.2.0 compatibility

v0.2.0 is additive over v0.1.0. The v0.1.0 release remains immutable and supported.

The `EventReceipt` storage contract adds `idempotency_claim_digest`, a domain-separated principal-scoped retry identity, plus a Neo4j uniqueness constraint on that field. New receipts write only the claim digest. Readers and the dry-run dispatcher continue to accept legacy v0.1.0 receipts carrying `idempotency_key_digest` without rewriting them.

Legacy raw-key digests cannot be converted because the original raw key, issuer, and audience were intentionally never persisted. A post-upgrade request matching a legacy raw-key digest therefore fails closed and must use a new idempotency key; it never receives the legacy receipt or correlation metadata.

No Work class, lifecycle status, predicate, transport opcode, authority scope, or external-delivery claim changes in this release.
