# Work and Arena operations

## Vocabulary and reads

| Kind | Meaning |
|---|---|
| Proposal | A suggested change awaiting a decision. |
| Initiative | A strategic body of work spanning projects. |
| Project | A bounded delivery effort within an initiative. |
| Issue | An implementation or operational problem. |
| Task | A concrete executable unit. |
| Arena | A continuing area of responsibility, separate from Work. |

Work parentage is Initiative → Project → Issue → Task. Proposal acceptance
records a Decision; Decision is not a sixth generic Work kind. Do not invent
Plan, Repository, Release, Commit, or directory Entity HTTP CRUD. Plans can be
descriptions or attached Artifact documents. `ZenithRepository` and the Rolodex
Entity vocabulary are published vocabulary with `runtimeLabel: false`.

```sh
devgraph query work Initiative --limit 50
devgraph query work Issue issue-id
devgraph query children Project project-id --limit 50
devgraph query parent Task task-id
devgraph query dependencies Issue issue-id
devgraph query dependents Issue issue-id
devgraph query blockers task-id
devgraph query blocked task-id
devgraph query arena --limit 50
devgraph query arena-members arena-id --limit 50
devgraph query arena-of Task task-id
```

Use Work `--after-id ID`; relationship and Arena member pages use
`--after-resource Kind/id` (a slash, not a colon). Keep filters/order unchanged
across pages. Limits are 1–100. These are current-state pages, not a frozen
snapshot. Relationship reads include archived Work; Work/Arena lists omit it
unless `--include-archived` is selected. `blockers` returns tasks blocking the
subject; `blocked` returns tasks the subject blocks.

The monitor reads full selected Work through `GET /work/{kind}/{id}`. Its
supporting-material reader uses
`GET /work/{kind}/{id}/supporting-material?limit=50` and opaque `after` cursors.
It resolves stored references to Artifact, ExternalLink, Requirement, and
AcceptanceCriterion metadata. Follow returned cursors for the same parent and
limit; 409 `supporting_material_changed_restart_pagination` requires starting
the page set again. Do not replace an error with “no documents.”

Attached Artifact text is available through
`GET /work/{kind}/{id}/supporting-material/Artifact/{artifact_id}/document`.
Only explicitly configured narrow document roots admit local previews. Remote
URLs provide source links, not fetched content. Honor `readable`, `metadata_only`,
`unconfigured`, `missing`, `unsupported`, and `unavailable` states. Never turn a
document URI into arbitrary filesystem access or forward the bearer to a source
link. The current CLI has no general supporting-material/document subcommand;
use the monitor or a documented authenticated client, not an invented command.

Observations have dedicated `GET /initiative-observations[/{id}]` routes.
Their `problem`, `desired_state`, `evidence_urls`, and confidence describe what
the scout inferred. `authorship=inferred` and `claim_status=unclaimed` are
server-owned. Observation creation is not a named Work or Arena operation and
is not authorized by the local read bearer or a named Work grant.

## Signed Work

Check `devgraph auth status --check` for the selected identity and
`devgraph auth work status` for actual grant validity. Bundle presence alone
does not prove current authority. Read current subject and related-record
versions before constructing an update.

```text
devgraph work create|patch|status|archive|accept|convert|parent.set|dependency.add|dependency.remove|blocker.add|blocker.remove --request-file /absolute/private/request.json --idempotency-key-file /absolute/private/idempotency.txt
```

Create request example:

```json
{
  "schema": "devgraph.work-request.v1",
  "operation": "create",
  "kind": "Issue",
  "id": "example-issue",
  "expected_version": null,
  "payload": {"id": "example-issue", "title": "Example"}
}
```

All six envelope fields are mandatory. Create uses null `expected_version`;
other operations require the current positive integer version. The CLI command
must match the request's operation. Both files must be absolute, owner-private
regular files in private directories. Idempotency keys contain 16–128 ASCII
letters, digits, `.`, `_`, `~`, or `-`. Use private temporary files and preserve
them through uncertain outcomes; do not place them in a repository.

| Operation | Payload |
|---|---|
| create | id, title; optional description, priority, artifact_ids, external_link_ids |
| patch | Nonempty subset of editable create fields, excluding id; no nulls |
| status | status |
| archive | Empty object |
| accept | decision_id, decision_title; Proposal only |
| convert | issue_id, decision_id; accepted Proposal only |
| parent.set | previous_parent and parent, each null or a versioned reference |
| dependency.add / dependency.remove | target reference |
| blocker.add / blocker.remove | target reference; Task endpoints only |

A reference is `{"kind":"Project","id":"project-id","expected_version":2}`.
Reparenting binds old and new parents, not just the child. Dependencies point
subject → target; blockers point blocking Task → blocked Task. Archive retains
relationships and does not cascade. Confirm legal lifecycle transitions against
the selected ontology/release; do not invent status values.

Native Wallet signs; native secS checks resource grants and issues a short-lived
projection; Devgraph verifies it and commits the mutation plus EventReceipt.
The HTTP path is `POST /work-operations/v1`. Generic bearer writes stay closed.
A timeout has an unknown outcome: retry identical request bytes and idempotency
key with the same principal. Never generate a new key for that retry. A changed
request requires a distinct key. 412 means stale version; 409 can mean a
relationship/idempotency conflict. Resolve the cause before attempting a changed
operation. Duplicate replies keep the same receipt ID and may have `work=null`.

## Arenas

An Arena has id, title, description, version, archived, and timestamps; it has
no Work status or priority. `CONTAINS_WORK` points Arena → parentless Initiative
or Task. Membership is optional, at most one direct Arena; no Arena nesting.
Projects, Issues, and child Tasks inherit from their Work root. Never persist
duplicate inherited membership. Dependencies/blockers never confer membership.
The monitor's Arena filter follows outgoing links to show context and may show
cross-Arena dependencies; it is not an authoritative membership query.

```text
devgraph arena create|patch|archive|member.set --request-file /absolute/private/request.json --idempotency-key-file /absolute/private/idempotency.txt
```

Use the same six-field envelope with `schema=devgraph.arena-request.v1` and
`POST /arena-operations/v1`. `create` uses kind Arena, null expected_version,
and id/title/optional description. `patch` changes nonempty title/description;
`archive` uses an empty payload. `member.set` names the Initiative or Task as
subject, its current Work version, and required `previous_arena` and `arena`
references. Each reference is null or
`{"kind":"Arena","id":"arena-id","expected_version":2}`; at least one must
be non-null. Moving membership binds both current Arenas. Touched records
advance versions atomically. Archived Arenas can be read and moved out of, but
cannot receive assignments or content edits. Archival does not cascade Work
status or remove retained membership.

When assigning a Work parent to a directly assigned standalone Task,
`parent.set` must include its actual `previous_arena` reference. The transaction
removes direct membership and inherits the new Work root's membership. Removing
a parent does not invent a new Arena membership.

Arena writes require compatible Wallet/secS binaries and an explicit Arena
grant extension. Existing Work-only grants remain Work-only. For authorized
extension, prepare `devgraph auth work plan --renew --include-arenas
--output-file /absolute/private/arena-grant.json`, inspect actor/resources/expiry,
then apply that plan. Ordinary renewal preserves the installed profile.

## Bounded query language

`devgraph query cypher --request-file /absolute/private/query.json` accepts the
`devgraph.cypher-read-request.v1` schema. This is a compiled, allowlisted Work
read language, not arbitrary Neo4j access. It supports the five Work kinds,
at most one directed HAS_CHILD/DEPENDS_ON/BLOCKS hop, parameter comparisons,
explicit public scalar projections, and mandatory LIMIT 1–100. It does not
support Arena, Entity, whole-node projections, write clauses, procedures,
subqueries, arbitrary functions, or private properties. Prefer named reads
unless a bounded query materially helps.
