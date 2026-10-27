# devgraph readiness assessment ontology

Specified by GitHub Issue #4: https://github.com/ZenithResearch/devgraph/issues/4.

`ReadinessAssessment` makes work-quality attributes graph-representable without turning discussion notes into hidden process state.

## Implemented now

This document defines the schema contract. No runtime model is implemented yet.

## Shape

Required fields:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Stable assessment id. |
| `assessed_at` | datetime | When assessment was recorded. |
| `assessor_ref` | string | Entity reference for the assessor. |
| `target_ref` | string | Work object being assessed. |
| `objective_clarity_score` | integer 1..10 | How clear the objective is. |
| `isolation_boundary_score` | integer 1..10 | How well the PR/work boundary is isolated. |
| `ease_to_understand_score` | integer 1..10 | How easy the work is for an implementer/reviewer to understand. |
| `rigour_score` | integer 1..10 | How rigorous the spec/evidence/acceptance criteria are. |
| `resource_adequacy_score` | integer 1..10 | Whether required docs/tools/context exist. |
| `dependency_certainty_score` | integer 1..10 | Whether dependencies and starting state are known. |
| `verification_executability_score` | integer 1..10 | Whether verification can be run deterministically. |
| `pr_size_safety_score` | integer 1..10 | Whether the work is safe as one PR boundary. |
| `security_control_score` | integer 1..10 | Whether sensitive/security boundaries are explicit. |
| `overall_readiness_score` | decimal | Summary score, usually average or curated weighted score. |
| `recommendation` | enum | `ready`, `harden`, `split`, or `block`. |
| `rationale` | string | Human-readable reason for score/recommendation. |

## Edges

| Predicate | From | To | Meaning |
|---|---|---|---|
| `HAS_READINESS_ASSESSMENT` | Todo/Proposal/Issue/Task | ReadinessAssessment | Work has readiness assessment evidence. |
| `IDENTIFIED_GAP` | ReadinessAssessment | Requirement/Blocker/Issue/Task | Assessment identified a gap. |
| `USES_RUBRIC` | ReadinessAssessment | Artifact/ExternalLink | Assessment used a rubric. |
| `SUPPORTED_BY` | ReadinessAssessment | Artifact/ExternalLink/ReviewPacket | Assessment is supported by evidence. |

## Invariants

- A ReadinessAssessment targets exactly one work object in v0.
- Score fields are 1..10.
- Recommendation is one of `ready`, `harden`, `split`, `block`.
- ReadinessAssessment is evidence; it does not replace Issue, Task, Requirement, AcceptanceCriterion, Blocker, or Decision.
- This schema is docs/schema-only in Issue #4. UI, dashboards, automatic scoring, and runtime enforcement are out of scope.

## Verification

```bash
rg "ReadinessAssessment|objective_clarity|isolation_boundary|verification_executability|HAS_READINESS_ASSESSMENT" ontology/
python3 -m pytest tests/docs/test_readiness_assessment_docs.py -q
```
