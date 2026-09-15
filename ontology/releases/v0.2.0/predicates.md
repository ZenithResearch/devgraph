# devgraph v0 predicates

Specified by GitHub Issue #3 and extended by Issue #4.

## Work structure

| Predicate | From | To | Meaning |
|---|---|---|---|
| `CONTAINS` | Initiative/Project/Issue | Project/Issue/Task | Hierarchical containment. |
| `DEPENDS_ON` | Todo-derived work | Todo-derived work | Source cannot complete before target. |
| `BLOCKS` | Blocker/Task/Issue | Todo-derived work | Source prevents target progress. |
| `HAS_REQUIREMENT` | Todo-derived work | Requirement | Work must satisfy requirement. |
| `HAS_ACCEPTANCE_CRITERION` | Todo-derived work/Requirement | AcceptanceCriterion | Checkable completion condition. |

## Governance/provenance

| Predicate | From | To | Meaning |
|---|---|---|---|
| `ACCEPTED_BY_DECISION` | Proposal | Decision | Proposal acceptance/conversion has Decision provenance. |
| `HAS_DECISION` | Todo-derived work | Decision | Work is governed or affected by decision. |
| `HAS_HANDOFF` | Todo-derived work | Handoff | Work has handoff context. |
| `HAS_REVIEW_PACKET` | Todo-derived work | ReviewPacket | Work has review packet/evidence bundle. |
| `HAS_MILESTONE` | Project/Initiative | Milestone | Work contributes to milestone. |

## Evidence/external references

| Predicate | From | To | Meaning |
|---|---|---|---|
| `HAS_ARTIFACT` | Todo-derived work/ReviewPacket/Decision | Artifact | Work/evidence links to artifact. |
| `HAS_EXTERNAL_LINK` | Todo-derived work/Artifact/Decision | ExternalLink | Work/evidence links to external URL/object. |
| `HAS_SYNC_SHADOW` | ExternalLink | SyncShadow | Optional non-authoritative external metadata. |

## Events and sessions

| Predicate | From | To | Meaning |
|---|---|---|---|
| `EMITTED_EVENT` | Todo-derived work/Decision | EventReceipt | Mutation produced an in-process receipt/outbox record in the same storage transaction. |
| `ATTRIBUTED_TO_HERMES_SESSION` | Todo-derived work/Artifact/EventReceipt/Decision | HermesSessionRef | Work/evidence attributed to external Hermes session reference. |

## Readiness assessment

| Predicate | From | To | Meaning |
|---|---|---|---|
| `HAS_READINESS_ASSESSMENT` | Todo/Proposal/Issue/Task | ReadinessAssessment | Work has readiness score/evaluation evidence. |
| `IDENTIFIED_GAP` | ReadinessAssessment | Requirement/Blocker/Issue/Task | Assessment identifies a gap or blocker. |
| `USES_RUBRIC` | ReadinessAssessment | Artifact/ExternalLink | Assessment cites rubric artifact/reference. |
| `SUPPORTED_BY` | ReadinessAssessment | Artifact/ExternalLink/ReviewPacket | Assessment cites supporting evidence. |

## Verification

`EMITTED_EVENT` is a provenance edge, not delivery evidence. It links the mutated subject to the local `EventReceipt`; it does not claim external message delivery, Matrix/webhook/eventbus publication, or exactly-once semantics.

```bash
rg "BLOCKS|DEPENDS_ON|HAS_REQUIREMENT|HAS_ACCEPTANCE_CRITERION|ACCEPTED_BY_DECISION|HAS_ARTIFACT|HAS_EXTERNAL_LINK|EMITTED_EVENT|ATTRIBUTED_TO_HERMES_SESSION|HAS_READINESS_ASSESSMENT" ontology/predicates.md
```
