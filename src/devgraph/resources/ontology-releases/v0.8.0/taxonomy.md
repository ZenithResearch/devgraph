# devgraph v0 taxonomy

Specified by GitHub Issue #3 and extended by Issue #4.

## Proposal statuses

| Status | Meaning |
|---|---|
| `draft` | Proposed but not ready for review. |
| `review` | Under review. |
| `accepted` | Accepted by Decision provenance. |
| `archived` | Non-destructive terminal state. |

`accepted` and `archived` are terminal for v0 unless later lifecycle policy says otherwise.

## Archive semantics

Archive means non-destructive terminalization. Planning and evidence records are not directly deleted by default. Archive preserves provenance and queryability.

## Artifact roles

Initial Artifact roles:

- `proposal_document`
- `review_packet`
- `implementation_evidence`
- `smoke_test_evidence`
- `export_artifact`
- `screenshot_summary`
- `log_summary`
- `decision_support_material`

## ExternalLink roles

Initial ExternalLink roles:

- `github_issue_url`
- `github_pr_url`
- `github_repo_url`
- `github_commit_url`
- `external_document_url`
- `external_runbook_url`
- `external_reference_url`

## EventReceipt types

Initial EventReceipt types:

- `work_created`
- `work_updated`
- `work_archived`
- `proposal_accepted`
- `decision_recorded`
- `artifact_attached`
- `external_link_attached`
- `export_created`
- `authorization_denied`

## Readiness metric categories

ReadinessAssessment metric categories use a 1..10 score range:

- `objective_clarity`
- `isolation_boundary`
- `ease_to_understand`
- `rigour`
- `resource_adequacy`
- `dependency_certainty`
- `verification_executability`
- `pr_size_safety`
- `security_control`

Recommendation values:

- `ready`
- `harden`
- `split`
- `block`

## HermesSessionRef metric categories

Allowed redundant/queryable metric categories:

- `token_input_count`
- `token_output_count`
- `tool_call_count`
- `started_at`
- `ended_at`
- `model_provider`
- `model_name`
- `source_channel`

Forbidden raw categories:

- raw transcript;
- raw prompt;
- raw tool payload;
- raw Matrix message;
- raw credential/capability/token material.

## Verification

```bash
rg "proposal statuses|artifact roles|external-link roles|event receipt types|readiness metric|Hermes" ontology/taxonomy.md -i
```
