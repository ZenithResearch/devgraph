# devgraph v0 invariants

Specified by GitHub Issue #3 and extended by Issue #4.

## Proposal acceptance

An accepted Proposal requires Decision provenance.

Required invariant:

```text
(Proposal {status: "accepted"})-[:ACCEPTED_BY_DECISION]->(Decision)
```

A Proposal must not create accepted-work edges without a Decision provenance record.

## Archive-by-default

Planning and evidence records are non-destructively archived by default. Direct delete is not part of the v0 service contract for planning/evidence records.

Applies to:

- Todo-derived work;
- Proposal;
- Decision;
- Handoff;
- ReviewPacket;
- Artifact;
- ExternalLink;
- EventReceipt;
- HermesSessionRef;
- ReadinessAssessment.

## Forbidden v0 classes

The v0 runtime model must not export or persist these as first-class classes:

- `WorkRequest`
- `Case`
- `Subcase`
- `Repository`
- `Release`
- `Deployment`
- `Commit`
- `Deliverable`
- `LinearSync`
- `PublicPortal`

Mentioning these strings in explicit deferred/excluded/non-goal sections is allowed. Creating runtime classes or Neo4j constraints for them is not.

## Direct Neo4j access

Client adapters must not receive direct Neo4j credentials or import database drivers. Neo4j access belongs behind the storage adapter/service boundary.

## Canonical authority and publication

Devgraph is the canonical ontology authority. Public hosts distribute generated versioned bundles and must not hand-edit or reinterpret them. Consumers pin both the ontology version and bundle digest; a digest mismatch fails closed.

Semantic operation names and schema identifiers are canonical. Numeric secS opcodes are receiver-local bindings and must not be treated as ontology identifiers. Actor identity, authority, and scopes derive from verified transport context, never from an unverified operation payload.

## Zenith Repository projection

`ZenithRepository` is canonical vocabulary with `runtimeLabel: false`. Conformance must not create a Devgraph runtime node or authority claim. The v0 runtime may reference a conforming repository through `ExternalLink`, attach non-authoritative provider metadata through `SyncShadow`, and link repository outputs as `Artifact` evidence.

## HermesSessionRef boundary

HermesSessionRef stores a reference to an external Hermes session id and redundant/queryable metrics. It must not store raw transcripts, raw prompts, raw tool payloads, raw Matrix messages, credentials, tokens, capability bodies, or macaroon contents.

## ReadinessAssessment boundary

ReadinessAssessment is evidence/rubric data attached to work objects. It is not a replacement for Issue, Task, Requirement, AcceptanceCriterion, Blocker, or Decision.

## Verification

```bash
rg "accepted Proposal|Decision|archive|WorkRequest|Case|Subcase|direct delete|HermesSessionRef|ReadinessAssessment" ontology/invariants.md
python3 -m pytest tests/docs/test_ontology_boundaries.py tests/docs/test_readiness_assessment_docs.py -q
```
