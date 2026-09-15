# Glossary

| Term | Current meaning |
|---|---|
| Work object | Frozen, versioned domain record with identity, content, status, priority, and evidence references. |
| Generic work kind | Proposal, Initiative, Project, Issue, or Task; accepted by `/work/{kind}` routes. |
| Decision | Provenance object required to accept a Proposal; not a generic HTTP resource. |
| Artifact | Evidence metadata stored in the graph. |
| InitiativeObservation | Append-only inferred/unclaimed Artifact profile describing a GitHub subject from evidence. |
| GraphStorage | Protocol implemented by memory and Neo4j adapters. |
| AuthorityContext | Verified envelope exposed as actor/session/correlation/scopes for one call. |
| EventReceipt | Local transactional record of an idempotent mutation. |
| Monitor snapshot | Read-scoped, redaction-safe aggregate of visible work, observations, receipts, and stored topology. |
| Local fixture | Memory-backed app with a synthetic read credential and optional demo data. |
| Ready | Storage and optional migration checks passed for the assembled app; not a deployment or availability claim. |
