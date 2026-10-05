# Shared Todo progress

Todo, Proposal, Initiative, Project, Issue and Task share exactly `not_started`, `in_progress`, `done`. Base Todo requires no detailed workflow. A signed progress attestation can complete a simple Todo; attached requirements still need evidence or reasoned exclusions and blockers must be resolved.

`progress.stage_progress` and the exported workflows.json define the mapping. Backlog is Not started. All other nonterminal stages, including Planning, Ready, approval, Waiting and rework, are In progress. Detailed Done is guarded by current review/evidence and boundary handoff requirements. Workflow assignment is independent of containment. Reopening Done clears the detailed workflow approvals and enters its first active stage. Rework cannot return started work to Not started.

Archive is a boolean independent of progress and detailed stage. Archive and restore preserve all classification and evidence fields. Archived is not a canonical progress value or detailed lifecycle stage. The old status field remains solely for compatibility and historical disposition APIs.

Proposal acceptance and rejection require Decision provenance. A rejected proposal may close its disposition with a signed attestation and handoff evidence, without traversing delivery approvals for work it will not execute. Linked delivery work remains independent. Child progress is an informational rollup and never grants parent approvals.

## Migration and unresolved history

Migration 28 maps explicit Backlog → Not started, explicit active stages → In progress, explicit Done → Done, subject to current evidence gates. Legacy Draft and Accepted alone are insufficient. Absent progress remains absent and is reported for classification, not included in Not started counts. An inconsistent stage/approval record also remains unclassified. Existing archived flags are retained. The migration journal and property writes commit together, after version checks and canonical preflight. No historical release, signed bytes, timestamps, or record versions are rewritten.

## Reads

`GET /todos/v2` supports kind, progress, archived=exclude|include|only, classification=required, q, limit (1–100), after and revision. Defaults include all six types, hide archived, and order by descending priority then kind and ID. `total` covers the complete same filtered list, not just the page. `classification_required` counts unresolved records across the graph. Detail is `/todos/v2/{kind}/{id}`. Kanban v2 adds progress filtering and a Needs classification diagnostic column; that column is not a canonical state. Read projections are bounded to 10,000 items and fail closed beyond that bound.

## Signed writes

`POST /todo-operations/v2` admits `devgraph.work-request.v2`; the SHA-256 digest domain is `devgraph.work-request.v2\0`. The exact semantic operation is `devgraph.work.<operation>.v2`. Existing versions, resource grants, idempotency keys, transaction and EventReceipt handling remain mandatory. Read credentials never authorize writes.

V2 create supports all six kinds, assigning new records Not started. `progress.set` takes progress, record_id, reason, evidence and requirements; it records a ReviewPacket attestation bound to the subject revision. Detailed stage changes use workflow.transition and reviews. `restore` has an empty payload. `proposal.reject` takes decision_id and reason. Legacy `status` is not admitted in v2. Existing operations have the same payload grammar as v1.

For uncertain outcomes preserve the exact canonical request and original idempotency key. Successful source tests do not establish installed Wallet → native host → secS qualification.
