# Arena runtime v1

An Arena is an independent record: `schema_version: 1`, `kind: "Arena"`, stable
`id`, `title`, `description`, positive integer `version`, boolean `archived`,
and timezone-aware `created_at`/`updated_at`. It has no Work status or priority.
IDs use the existing canonical Work identifier syntax. Titles contain 1–4096
characters and descriptions at most 32768; requests must canonicalize to at
most 65536 UTF-8 bytes. Unknown fields, duplicate keys, unsafe integers, and
floating-point JSON numbers are rejected.

## Signed operations

`POST /arena-operations/v1` accepts the closed `devgraph.arena-request.v1`
envelope with exactly `schema`, `operation`, `kind`, `id`, `expected_version`,
and `payload`. The request digest uses `devgraph.arena-request.v1\0`.
Wallet presentations and secS projections use the existing named-operation
transport, bind the full operation and sorted resources, and retain their
existing signature domains. Old Work request bytes remain unchanged.

| Operation | Subject | Version | Payload |
|---|---|---|---|
| `create` | Arena | null | Matching `id`, `title`, optional `description` (default empty). |
| `patch` | Arena | Current | Nonempty selection of `title` and `description`; null is invalid. |
| `archive` | Arena | Current | Empty object. |
| `member.set` | Initiative or Task | Current Work version | Required `previous_arena` and `arena` references, either nullable. |

Each non-null Arena reference contains exactly `kind: "Arena"`, `id`, and
`expected_version`. At least one reference must be non-null. Assignment uses
null as the previous Arena; removal uses null as the new Arena. Moving requires
both current versions. All touched Arenas and the Work subject increment their
versions in the same serialized transaction as the membership edges and audit
receipt. The signed resource inventory includes every touched Arena and Work
subject. A stale or omitted prior membership fails without partial changes.

Existing `devgraph.work-request.v1` `parent.set` accepts optional
`previous_arena` when assigning a parent to a directly assigned standalone Task.
It must match the current Arena and version; its resource is included in the
signed Work operation. Direct membership is removed atomically, and the Task
inherits its new Work root's membership. Omission preserves old canonical bytes
and succeeds only when no direct membership needs removal. Removing a Work
parent never invents a direct Arena membership.

Archived Arenas preserve their references and can be read. They cannot receive
new assignments or content edits; existing active Work can be removed or moved
out. Archived Work cannot change membership. No operation cascades Work status.
Idempotency, replay denial, receiver policy pins, and durable audit requirements
are shared with the named Work receiver. The two POST routes reject each
other's request schema.

## Reads and bounds

Reads require `devgraph.read` and the existing private API credential:

- `GET /arenas?limit=50&after_id=...&include_archived=false`
- `GET /arenas/{id}`
- `GET /arenas/{id}/members?limit=50&after_resource=Initiative/id`
- `GET /work/{kind}/{id}/arena`

Pages are bounded to 1–100 items. Members are direct roots, ordered by kind
then ID. Effective membership follows incoming `HAS_CHILD` to the Work root;
the response contains `arena` (nullable), `root_kind`, `root_id`, and `inherited`.
Archived parents still count. Multiple parents, cycles, or invalid direct
membership fail closed. Validation queries only the requested Work's parent
chain (at most Task → Issue → Project → Initiative) or the selected Arena page.
Each visited record returns at most two incoming edges of each containment
type: a second edge proves ambiguity and is rejected, including duplicate edges.
Page records and their validation edges are fetched together. There is no global
membership-count ceiling; unrelated graph growth cannot disable membership
reads or removals. Validation applies to the records visited by the request,
not to unrelated records or other pages. Read responses use `Cache-Control: no-store`.

## CLI and local grant administration

`devgraph arena create|patch|archive|member.set --request-file FILE
--idempotency-key-file FILE` uses the saved signer and the fixed native Wallet
and secS binaries. Files must be private and absolute, as for `devgraph work`.
Read commands are `devgraph query arena [ID]`, `devgraph query arena-members ID`,
and `devgraph query arena-of KIND ID`.

Example Gallery creation request (save privately, then sign with the CLI):

```json
{"schema":"devgraph.arena-request.v1","operation":"create","kind":"Arena","id":"gallery","expected_version":null,"payload":{"id":"gallery","title":"Gallery","description":"Gallery initiatives and standalone tasks."}}
```

After creation, assigning an Initiative requires its current Work version and
Gallery's current Arena version. Create the membership edge on the root only;
never persist inherited edges on its projects, issues, or tasks.

Existing local grants remain at 11 operations/41 rules. To prepare an explicit
extension, use `devgraph auth work plan --renew --include-arenas --output-file FILE`,
inspect the plan, then `devgraph auth work apply --plan-file FILE`. The Arena
profile has 15 operations/48 rules: four Arena operations plus permission to
remove direct Arena membership during Work parent assignment. Ordinary renewal
and verifier rotation preserve the installed profile. A partial or broader
managed policy is rejected. Membership itself grants no authority.

Deployment order is the compatible Wallet parser/signer, secS parser/verifier,
Devgraph migration 26 and runtime, then the explicit grant extension. Keep
Arena writes disabled while any consumer still uses the old contract. Take and
verify a backup through the local maintenance boundary before migration.
