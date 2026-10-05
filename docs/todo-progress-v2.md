# Todo progress v2

Todo, Proposal, Initiative, Project, Issue and Task share `not_started`,
`in_progress` and `done`. A new base Todo starts Not started without a workflow.
New subtypes start in their default workflow's Backlog. Every active stage,
including Planning, Ready, Waiting, approvals and rework, is In progress.
Workflow assignment is independent of containment. The central stage mapping
and transition gates are enforced on writes, not inferred by the interface.

`archived` is independent. Archive and restore preserve progress, detailed stage,
reviews, evidence and history. Legacy `status` is retained for historical and v1
compatibility; it does not define canonical progress. The v2 read and write
responses expose progress and archive separately and omit legacy lifecycle status.

Simple Todos can be completed by an authorized `progress.set` attestation without
a detailed workflow. Attached requirements still need supporting evidence or
reasoned exclusions; unresolved blockers prevent completion. Subtype workflows
retain approval ordering, evidence revision checks, requirement reconciliation and
handoff gates. Reopening Done work returns it to In progress and clears superseded
workflow reviews. Changes to completed scope require reopening. Parent gates
remain independent of child completion.

Proposal acceptance and rejection record Decision provenance. A rejected proposal
can complete its disposition with evidence of communication/handoff. This changes
only the proposal; linked delivery work remains independent.

## Contracts

- `GET /todos/v2` accepts `kind`, `progress`, `archived=exclude|include|only`,
  `classification=required`, `q`, `limit`, `after` and `revision`. Default archive
  visibility excludes archived items. Pages contain exact filtered `total`, bounded
  `items`, a continuation cursor and a revision. Changed data invalidates old cursors.
- `GET /todos/v2/{kind}/{id}` returns canonical reader details.
- `GET /todos/v2/classification-report` (also `devgraph query progress-report`)
  reports classification totals and each unresolved key with reasons. It is a
  bounded authenticated read; it never assigns progress.
- `GET /monitor/kanban/v2` adds the same progress filter and six-kind support.
  Detailed columns subdivide In progress. “Needs classification” is a diagnostic
  column for absent progress, not a fourth state.
- `POST /todo-operations/v2` consumes `devgraph.work-request.v2`. The signing
  domain is `devgraph.work-request.v2\0`; authority names end in `.v2` and results
  use `devgraph.work-result.v2`. The new operations are `progress.set`, `restore`
  and `proposal.reject`; legacy `status` is excluded. Common operations retain
  their existing payload rules. Base Todo admits create, patch, archive, restore
  and progress.set. The canonical Rust parser and Python parser share v2 vectors.

The Todo route is separate from the generic credential transport's existing
`/work-operations/v2` proposal. Contract version and credential transport version
are distinct; neither endpoint silently downgrades authority.

Historical v1 request bytes, digest domains and response envelopes remain pinned.
A v1 archive still reads as Archived to old clients; it does not erase canonical
progress. The additive topology fields are `todo_progress` and `child_progress`;
legacy rollup fields remain for compatibility. The new reader uses canonical
fields. Wallet and secS must admit the exact new operation/resource scopes;
read credentials never authorize writes. Grant planning/renewal requires the explicit
`--include-progress` flag to add v2 authority; ordinary renewal preserves the saved
scope. No installed grant is expanded by this source change.

## Forward migration 28

Migration 28 follows workflow metadata migration 27. It maps explicit Backlog to
Not started, active stages to In progress, and supported Done to Done. Stored
archive information becomes the independent archive attribute. It does not infer
progress from Draft, Review, Accepted or Archived.

Each item retains its historical status/workflow/progress/archive values and any
classification reasons in `progress_migration_json`. Contradictory progress/stage
pairs, stale or missing required reviews, incomplete requirements and unsupported
completion remain absent/null and are reported. Simple Todo and rejected-proposal
completion attestations are checked against current content and evidence. No
fourth canonical progress value is stored. Structurally corrupt records cause
preflight to fail closed for operator repair rather than being silently rewritten.

The migration uses one transaction for version-checked writes and its journal
marker. A version conflict or missing journal marker rolls back the migration.
It preserves item versions and timestamps so historical evidence is not invalidated
merely by introducing the new representation. Historical ontology bundles are
unchanged; the new publication is **v0.8.0**.

## Daily check-in

The default list includes all six types, including standalone work, with
`progress=not_started&archived=exclude`. It sorts by descending priority, then kind
and ID. “At a glance” uses the complete count of the same filtered query, independent
of loaded pages. Missing classifications are excluded and explained separately.
Type and search preferences persist; credentials do not. The reader, topology and
Kanban use the same three progress labels.

## Deployment boundary

Source verification does not qualify installed signing. The installed host was
healthy on schema 26 during the 2026-10-05 read-only audit; no migration or runtime
activation was performed. Its supported Work API exposed **404 records** (including
11 archived), all without canonical progress/workflow evidence. All 404 require
classification; none can truthfully be counted as Not started. The old API cannot
inventory base Todo, so this is explicitly a partial inventory. The per-record
report is kept locally, not in the public repository.

Activation still requires a backup, schema 27/28 migration, review of the resulting
classification report, and matched installed Chrome Wallet → native host → secS →
Devgraph qualification with disposable data. An upgraded source fixture or a green
WASM test alone does not qualify the installed signing chain.

The generic Wallet/credential client is a separate existing integration stack
(private Devgraph #66/#67, Wallet #28, secS #297). Its authority does not yet admit
the new progress operations. The older Wallet #25 is superseded and stays closed;
its pinned native/WASM signing tests are compatibility evidence only. Do not merge
or reactivate that application-specific browser bridge to bypass the generic
transport gate. The private SDK source has a guarded Todo route, canonical reads
and v2 request/result validation; generic browser transport composition and matched
installed qualification remain explicit follow-up gates.

## Verification recorded on 2026-10-05

The public repository verification script passed with 2,418 Python tests and five
skips; all 216 frontend tests passed. Ruff (including integrations), generated
ontology/token checks and whitespace checks passed. Tests cover the three states,
no-workflow Todo completion, subtype entry gates and rework, proposal dispositions,
archive/restore, contradictory and ambiguous legacy records, transaction rollback,
stale evidence, six-kind filtering, priority ordering, complete counts and bounded
250/1,500-item pagination. Existing v1 signing vectors remain unchanged.

The private SDK composition passed its repository checks (2,503 Python tests,
five skips), 220 frontend tests, 64 Rust tests, WASM build, 19 SDK JavaScript tests
and TypeScript checks. The final guarded-route/authority checks passed separately
(89 tests). secS source passed 808 Rust tests; the Wallet compatibility source
passed 32 Rust tests and its npm/typecheck suites. Python-produced v2 results are
consumed by Rust tests, including mismatched versions, progress, identity and
missing fields. Synthetic identities and data were used for mutation tests.

The browser preview was checked against live read-only data in Light, Dark, Aqua
and System modes, including a 390-pixel viewport and refresh behavior. Credentials
clear on refresh; ordinary filters remain. That preview is a schema-26 adapter
with explicitly incomplete coverage, not a migrated runtime or installed signing
qualification. The Neo4j migration transaction tests use a simulated driver;
an actual production migration was not run.
