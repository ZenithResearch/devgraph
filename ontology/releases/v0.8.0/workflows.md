# Workflow v1

Workflow assignment and exact stage are optional canonical Todo metadata, separate
from lifecycle and parentage. The five concrete kinds are Proposal, Initiative,
Project, Issue and Task. Absent metadata stays absent on migration and ordinary
writes. The board presents it as Backlog with “Stage not set”. New work persists
its default workflow and Backlog; converted Issues independently start execution.

The machine-readable definitions in `workflows.json` describe versioned stages,
columns, transition targets and entry reviews. The authenticated `/workflows/v1`
returns this same contract. Definition identifiers are immutable: a semantic
change after release needs a new workflow identifier and explicit migration.

`workflow.assign`, `workflow.review` and `workflow.transition` are closed named
Work operations. They use existing signature domains, transaction, expected
versions, exact resource grants, idempotency and EventReceipt handling. Existing
request bytes and legacy response envelopes do not change. New workflow reads
use their own versioned envelopes. Workflow reviews are authorized attestations,
not automatic verification of external source, CI or document quality.

Decision, ReviewPacket and Handoff records contain structured
`devgraph.workflow-review.v1` descriptions binding the subject, actor, relevant
content revision, evidence hashes and review verdict. Artifact and ExternalLink
remain evidence vocabulary. `HAS_WORKFLOW_DECISION`, `HAS_REVIEW_PACKET`, and
`HAS_HANDOFF` connect them to work. Superseded records remain readable history;
only current, matching evidence can clear a gate. Technical reviews cover six
layers, each approved or explicitly excluded with a reason. Completion requires
an approved requirement ledger, applicable prior approvals, evidence, technical
clearance and a verified boundary handoff. Task evidence addresses a commit or
checklist; Issue evidence addresses a PR or specification; higher work records
its agreed outcome. URLs are recorded references and are never fetched to assert
quality. HTTP(S) evidence authorities use ASCII hostnames or bracketed IPv6,
optional valid ports, no user information, backslashes or control characters.

Waiting records the interrupted stage and a reason. A current resolution Decision
with evidence is required to resume. Plan feedback returns to Planning; delivery
feedback returns to implementation. Explicit scope rework can return In progress
to Planning (Vibe CEO), or Implementing commit to Intake (execution). This clears
superseded approvals before the sequence starts again.

Containment is optional, one parent maximum and acyclic: Project → Initiative;
Issue → Initiative or Project; Task → Initiative, Project or Issue. Proposal is
outside containment. Arena inheritance follows this exact hierarchy. Scoped
boards show direct children unless descendants are explicitly requested; unrelated
standalone work is never added. Child completion does not authorize the parent's
own approvals. To explicitly exclude unfinished child work at completion, record
a reasoned ledger exclusion whose `subject` is the exact `Kind/id` reference. Attached requirements and
criteria also use exact typed subjects, preventing same-ID collisions.

Migration 27 validates optional metadata using the typed canonical storage
contract and journals admission atomically. It does not create assignments,
change lifecycle, rewrite evidence, or backfill legacy stages. Existing evidence
kinds already have uniqueness constraints; no new supporting-record class or
storage label is introduced.


## Shared progress (v0.8.0)

See [todo-progress.md](todo-progress.md) for the canonical three-state model, independent archival, and explicit historical classification. Legacy Draft/Review/Accepted/Archived vocabulary does not determine canonical progress.
