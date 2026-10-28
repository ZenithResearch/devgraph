# devgraph v0 predicates

Specified by GitHub Issue #3 and extended by Issue #4.

## Work structure

| Predicate | From | To | Meaning |
|---|---|---|---|
| `HAS_CHILD` | Initiative/Project/Issue | Project/Issue/Task | Optional containment. Exact permitted pairs are in workflows.json; one parent, no cycles. |
| `DEPENDS_ON` | Todo-derived work | Todo-derived work | Source cannot complete before target. |
| `BLOCKS` | Blocker/Task/Issue | Todo-derived work | Source prevents target progress. |
| `HAS_REQUIREMENT` | Todo-derived work | Requirement | Work must satisfy requirement. |
| `HAS_ACCEPTANCE_CRITERION` | Todo-derived work/Requirement | AcceptanceCriterion | Checkable completion condition. |

## Arena membership

| Predicate | From | To | Meaning |
|---|---|---|---|
| `CONTAINS_WORK` | Arena | Parentless Initiative or Task | Optional direct Arena membership; at most one Arena per member. |

This predicate has a runtime binding in v0.6.0. It does not create
a `HAS_CHILD` edge or broaden the existing Work parent rules. Descendants
inherit the Arena of their Work root for presentation; inherited memberships
are not stored as direct edges. See [`arenas.md`](arenas.md) and the normative
[`arena-contract.json`](arena-contract.json).

## Governance/provenance

| Predicate | From | To | Meaning |
|---|---|---|---|
| `REJECTED_BY_DECISION` | Proposal | Decision | Recorded rejection disposition; linked delivery work stays independent. |
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

## Rolodex and accountability

These predicates use the canonical Rolodex vocabulary but have no v0 runtime
storage or mutation binding.

| Predicate | From | To | Meaning |
|---|---|---|---|
| `MEMBER_OF` | Entity | Organization | Directory membership, including nested organizations; does not grant authority. |
| `OPERATED_BY` | Agent | Entity | Operational responsibility for an Agent, including an Agent delegation chain; does not delegate a credential. |
| `ASSIGNED_TO` | Todo-derived work | Entity | Entity expected to carry out work; does not authorize a mutation. |
| `OWNED_BY` | Todo-derived work | Entity | Accountable steward for work; does not assert data or credential ownership. |
| `ATTRIBUTED_TO` | Todo-derived work/Artifact/Decision/Handoff/EventReceipt/HermesSessionRef | Entity | Directory-level provenance attribution; not cryptographic proof by itself. |

`HAS_EXTERNAL_LINK` may also start from an `Entity` when the target is a
reviewed public or non-secret reference. It must not be used to publish private
contact points or credential material.

The complete profile and runtime admission boundary are defined in
[`rolodex.md`](rolodex.md).

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
rg "BLOCKS|DEPENDS_ON|HAS_REQUIREMENT|HAS_ACCEPTANCE_CRITERION|ACCEPTED_BY_DECISION|HAS_ARTIFACT|HAS_EXTERNAL_LINK|EMITTED_EVENT|ATTRIBUTED_TO_HERMES_SESSION|HAS_READINESS_ASSESSMENT|MEMBER_OF|OPERATED_BY|ASSIGNED_TO|OWNED_BY|ATTRIBUTED_TO" ontology/predicates.md
```

| Workflow evidence predicate | Source | Target | Meaning |
| --- | --- | --- | --- |
| `HAS_WORKFLOW_DECISION` | Todo-derived work | Decision | Workflow approval or Waiting resolution; distinct from acceptance provenance. |

## Zenith local discovery (v0.9.0)

| Predicate | Shape | Meaning |
| --- | --- | --- |
| `HAS_LOCAL_REGISTRY` | ZenithLocalSpace → LocalResourceRegistry | Coordination-space discovery index. |
| `HAS_LOCAL_REGISTRATION` | LocalResourceRegistry → LocalResourceRegistration | Typed public descriptor entry. |
| `DESCRIBES_SERVICE` | LocalResourceRegistration → ServiceInstance | Advisory configured-service reference. |

These descriptive relationships establish no ownership, liveness, trust or permission.

## Seats and credential recipes (v0.10.0)

- `HAS_SEAT`: `Organization` → `Seat`. An organization has an organizational position; this is not an authentication or ownership grant.
- `HAS_SEAT_TYPE`: `Seat` → `SeatType`. Classifies a position using a shared seat template.
- `ASSIGNS_SEAT`: `SeatAssignment` → `Seat`. The organizational position filled by this assignment.
- `HAS_SEAT_OCCUPANT`: `SeatAssignment` → `Actor`. The Person or Agent occupying a seat. Organization is not an allowed occupant in the seat profile.
- `SUGGESTS_CREDENTIAL_PRESET`: `SeatType` → `CredentialPreset`. Suggests a permission recipe. Does not issue authority or silently update existing grants.
- `HAS_PRESET_REVISION`: `CredentialPreset` → `CredentialPresetRevision`. Names an immutable permission recipe revision. Revocation is a separate receiver decision.

## Session provenance (v0.11.0)

ATTRIBUTED_TO_SESSION links declared Work scope to SessionRef. SESSION_LINK_ORIGIN and SESSION_LINK_TARGET retain SessionLink endpoints. Legacy ATTRIBUTED_TO_HERMES_SESSION is unchanged. See [session-references.md](session-references.md).
