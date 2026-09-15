# Zenith Ontology v0.2.0

Devgraph is the canonical authority for this additive release of the Zenith ontology.

This release makes idempotent mutation claims safe across concurrent local and Neo4j writers. `EventReceipt.idempotency_claim_digest` binds the verified issuer, audience, actor, and caller-owned key while allowing the same principal to retry across sessions. Migration 24 adds the matching Neo4j uniqueness constraint. The receipt, domain mutation, provenance edge, and audit outcome remain one transaction.

The published v0.1.0 bundle is unchanged and remains supported. Legacy receipt digests remain readable for dispatch, but matching legacy retries fail closed because their missing issuer/audience/raw-key inputs cannot be safely reconstructed.

This is local mutation evidence, not exactly-once external delivery, public ingress, a secS consumer, or production deployment proof.
