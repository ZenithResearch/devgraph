# HTTP API reference

The API is created by `devgraph.api.create_app(ApiServices(...))`. The app
factory remains an injected composition boundary rather than a deployment
launcher. The package separately includes a loopback-only macOS local-host
workflow and operator CLI. Except for the separately configured monitor PoP and signed named Work
paths described below, every data route requires an injected verifier to
accept the bearer credential and grant the listed exact scope.
`create_app` does not provide a general deployment launcher.

The transport-free `devgraph.issue.create.v1` secS consumer is not an HTTP
route and is not mapped onto `POST /work/Issue`. The separate
[Named Work v1](named-work-v1.md) contract now defines `POST /work-operations/v1`
with its own signed projection, exact operations, and owner-held policy pins.
The old Issue-create authority cannot authorize that endpoint.

The separate [Arena runtime](../ontology/arena-runtime.md) uses
`POST /arena-operations/v1` with schema `devgraph.arena-request.v1` and four
explicit Arena operations. Reads use `/arenas`, `/arenas/{id}`,
`/arenas/{id}/members`, and `/work/{kind}/{id}/arena` under `devgraph.read`.
Arena is not accepted as a Work kind. The monitor adds an `arena` category;
Work totals and Work status semantics are unchanged.

`GET /monitor/snapshot` additionally has one exact, non-bearer receiver for
`devgraph.monitor.view.read.v1`. It accepts only the three canonical PoP headers
and never maps them into a generic `CredentialEnvelope` or `devgraph.read`
grant. See [monitor proof of possession](monitor-proof-of-possession.md).

## Common headers

```text
Authorization: Bearer <credential>
```

The exact monitor receiver instead requires:

```text
SecS-Devgraph-Monitor-Origin: http://127.0.0.1:8080
SecS-Devgraph-Monitor-Session: <canonical-base64url-session>
SecS-Devgraph-Monitor-Proof: <canonical-base64url-request-proof>
```

Every mutation also requires:

```text
Idempotency-Key: <caller-generated non-empty value>
```

`PATCH` additionally requires `If-Match: "<positive-version>"`.

## Routes

| Method | Path | Scope | Result |
|---|---|---|---|
| GET | `/live` | none | `{"live": true}` |
| GET | `/ready` | none | readiness body; 200 ready or 503 not ready |
| GET | `/` | none | official frontend HTML |
| GET | `/monitor` | none | alias of `/` |
| GET | `/monitor/snapshot` | `devgraph.read` bearer or exact `devgraph.monitor.view.read.v1` PoP | safe aggregate and topology |
| GET | `/work/{kind}` | `devgraph.read` | work list |
| GET | `/work/{kind}/{id}` | `devgraph.read` | one work object |
| GET | `/work/{kind}/{id}/children` | `devgraph.read` | stored children |
| GET | `/tasks/{id}/blockers` | `devgraph.read` | blockers for a Task |
| POST | `/work/{kind}` | `devgraph.write` | create plus receipt |
| PATCH | `/work/{kind}/{id}` | `devgraph.write` | content update plus receipt |
| POST | `/work/{kind}/{id}/archive` | `devgraph.write` | archive plus receipt |
| POST | `/work/{kind}/{id}/status` | `devgraph.write` | generic transition plus receipt |
| POST | `/proposals/{id}/accept` | `devgraph.write` | accepted Proposal plus receipt |
| POST | `/proposals/{id}/convert` | `devgraph.write` | new Issue plus receipt |
| GET | `/initiative-observations` | `devgraph.read` | observation list |
| GET | `/initiative-observations/{id}` | `devgraph.read` | one observation |
| POST | `/initiative-observations` | `devgraph.write` | appended observation plus receipt |
| POST | `/exports/internal` | `devgraph.export.internal` | full selected records |
| POST | `/exports/redacted` | `devgraph.export.redacted` | filtered/redacted records |
| POST | `/exports/public-safe-summary` | `devgraph.export.redacted` | count-only summary |

`Project` and `Issue` entries in the monitor snapshot's `graph_nodes`
additionally carry an optional `progress` object with schema version
`devgraph.work-progress.v0`, basis, completed/total counts, an integer
percentage, and canonical status counts. The projection is computed from local
`HAS_CHILD` edges and terminal Work statuses for each response. It is not a
stored Work field and does not change the generic Work API contract.

`{kind}` is exactly one of `Proposal`, `Initiative`, `Project`, `Issue`, or
`Task`. Case is significant. `Decision` has no generic route.

The canonical work mutation paths are `POST /work/{kind}`,
`PATCH /work/{kind}/{id}`, `POST /work/{kind}/{id}/archive`, and
`POST /work/{kind}/{id}/status`. The internal export path is
`POST /exports/internal`.

## Writes, authority, and concurrency

The credential verifier runs exactly once per idempotent write, before any
duplicate lookup. The verified context remains in a
graph-owned authority registry; an immutable single-use `WriteSession` references it without storing
mutable attribution. Session validation and removal are atomic under lock
before mutation. The same registry-owned context is the
sole source for scope enforcement, mutation, audit, and receipt attribution.
Its issuer, audience, and actor join the caller key in a domain-separated
idempotency claim; session is excluded so verified retries can cross sessions.
The receipt is atomically claimed before mutation. Concurrent same-claim
writers therefore have one winner, while different principals cannot receive
one another's receipt or correlation metadata.

The execution-context-local audit transaction encloses the storage/outbox
transaction. Its audit append therefore rolls back whenever the mutation,
receipt ID, receipt node, or `EMITTED_EVENT` edge fails to persist, without
erasing another thread or asyncio task's committed audit. Cancellation and
other `BaseException` exits restore the prior audit context and discard that
execution context's pending entries before propagation. The actor, session, correlation, issuer, and audience identifiers must satisfy the printable closed grammar before reaching audit, receipts, logs, or responses.

PATCH additionally requires `If-Match` matching exactly
`^"[1-9][0-9]*"$`. Missing is 428 `Precondition required`, malformed is 400
`Invalid version precondition`, and stale is 412 `Version precondition failed`.
The repository transaction loads current state, checks the version, merges only
supplied content fields, preserves identity/lifecycle/server fields, and writes
once. The route performs no pre-read.

## Work envelopes

Create body:

```json
{
  "id": "issue-api",
  "title": "Document the API",
  "description": "",
  "priority": 0,
  "artifact_ids": [],
  "external_link_ids": []
}
```

All fields except `id` and `title` have the displayed defaults. Unknown fields
are rejected. A successful create returns HTTP 201; a same-scope idempotent
retry returns HTTP 200 with the original receipt and `work: null`.

Work response:

```json
{
  "id": "issue-api",
  "kind": "Issue",
  "title": "Document the API",
  "description": "",
  "status": "draft",
  "version": 1,
  "priority": 0,
  "artifact_ids": [],
  "external_link_ids": []
}
```

List responses wrap records as `{"items": [...]}`. The list route accepts
`include_archived=false`; it does not expose cursor, limit, or descending query
parameters even though the repository facade supports them in Python.

PATCH accepts any non-empty subset of `title`, `description`, `priority`,
`artifact_ids`, and `external_link_ids`. Explicit `null`, unknown fields, and an
empty object are rejected. Identity, status, timestamps, and version are
server-owned. Missing `If-Match` returns 428, malformed syntax returns 400, and
a stale version returns 412.

Status body:

```json
{"status":"review"}
```

The schema accepts all four status strings, but the repository enforces the
transition table. Archive uses the dedicated archive route. Generic Proposal
acceptance is rejected.

Proposal acceptance body:

```json
{"decision_id":"decision-accept-api","decision_title":"Accept API work"}
```

Proposal conversion body:

```json
{"issue_id":"issue-from-proposal"}
```

## Mutation receipt

Every successful mutation result contains:

```json
{
  "work": null,
  "receipt": {
    "receipt_id": "event-receipt-...",
    "operation": "create_work_object",
    "subject_label": "Issue",
    "subject_id": "issue-api",
    "receipt_status": "pending",
    "duplicate": true,
    "correlation_id": "..."
  }
}
```

`work` is non-null on the first successful work mutation and null on a duplicate.
Observation mutations use `observation` in the same position. A duplicate key
is valid only for the same operation, subject label, and subject ID; other reuse
returns 409.

## Initiative observations

Creation requires:

```json
{
  "id": "observation-example",
  "project_id": "external-example",
  "subject_kind": "github_repository",
  "subject_url": "https://github.com/example/project",
  "title": "Inferred project initiative",
  "problem": "Evidence-backed problem statement",
  "desired_state": "Evidence-backed apparent desired state",
  "evidence_urls": ["https://github.com/example/project"],
  "confidence": 0.8,
  "observed_by": "scout-id"
}
```

`subject_kind` is `github_repository` or `github_organization`; confidence is
from 0 through 1. Optional strings are `github_node_id`, `source_commit`, and
`observation_signature`. The server sets the schema version, inferred
authorship, unclaimed status, and observation time. The list route accepts
`descending` (default false), `after_id`, and `limit` from 1 through 100
(default 50).

## Exports

All three routes accept a strict body:

```json
{"kind":"Issue","ids":["issue-a","issue-b"]}
```

Empty lists are valid. Input order and duplicate IDs are retained. A missing
record fails the request instead of returning a partial export. Internal output
has `{mode, records}`; redacted output adds `omitted_private`; the public-safe
summary has `{mode, total, by_kind, private_records}`.

## Bounded Python client

`DevgraphHttpClient` mirrors the bounded Work API with `create_work`,
`get_work`, `list_work`, `patch_work`, `transition_work_status`,
`archive_work`, `get_work_children`, `get_task_blockers`, `accept_proposal`,
and `convert_proposal`. The original Issue-specific create/get/list/review
methods remain compatibility wrappers.

Each call receives an explicit immutable `DevgraphRequestContext` containing
one opaque credential. The client forwards caller-provided idempotency and
version preconditions, validates success and problem envelopes strictly, and
makes at most one transport call. It does not discover or persist credentials,
generate idempotency keys, retry, access storage, or expose export,
initiative-observation, or monitor methods.

## Local hosting boundary

The package can configure and operate a private loopback macOS host with an
operator-selected existing data directory. New local launch agents use the
fixed-scope local read verifier: missing credentials remain HTTP 401 and the
generated capability can call only read routes. These repository capabilities
do not prove that a particular machine is running this commit or that canonical
identity, availability, backup, or remote access exists.

## Errors

Errors use `Content-Type: application/problem+json`:

```json
{
  "type": "about:blank",
  "title": "Unauthenticated",
  "status": 401,
  "detail": "credential required",
  "correlation_id": null
}
```

Implemented mappings include 400 invalid request/precondition, 401
unauthenticated, 403 forbidden, 404 missing, 409 duplicate/lifecycle/status or
idempotency conflict, 412 stale version, 422 invalid path kind or request-schema
validation, and 428 missing `If-Match`. Safe handlers redact details and do not
echo rejected input, raw credentials, or raw idempotency keys.
