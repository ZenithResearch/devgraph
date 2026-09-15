# Named Work v1

This source implements the named Work CLI, HTTP transport, canonical mutations,
and separate Wallet/secS authority contract. Installation and an active operator
trust bundle are required before agents can use the new write path. The existing
Issue-create v1 and monitor contracts keep their original bytes and domains.
The separate [bounded Cypher read contract](cypher-read-v1.md) provides Phase 2
queries without changing named mutation authority.

## Commands and transport

Reads use the existing private `devgraph.read` credential through the fixed
loopback HTTP service:

```text
devgraph query work Issue [id] [--include-archived] [--descending] [--after-id id] [--limit 1..100]
devgraph query children Project id [--after-resource Issue/id] [--limit 1..100]
devgraph query parent Task id
devgraph query dependencies Issue id
devgraph query dependents Issue id
devgraph query blockers task-id
devgraph query blocked task-id
```

All relationship commands accept `--after-resource Kind/id` and `--limit 1..100`.
`blockers` returns tasks blocking the subject; `blocked` returns tasks the subject
blocks. Work lists sort by ID; relationship pages sort by `(kind, id)`. Use the
last returned ID or `Kind/id` as the next cursor. A full page can be followed by
an empty final page. Keep filters and ordering unchanged across pages. These
are current-state pages, not a snapshot: concurrent insertions before the cursor
can be missed until a new traversal. Relationship reads include archived Work
so archive does not conceal retained hierarchy or dependency links.

The list envelope stays `{"items": [...]}` for old strict clients. Historical
children/blockers endpoints keep their array envelope and now return at most
50 records. Use the new relationship endpoint for continuation:

```text
GET /work/{kind}/{id}/relationships/{children|parent|dependencies|dependents|blockers|blocked}?limit=50&after_resource=Kind/id
```

Named writes all have an explicit CLI command:

```text
devgraph work create|patch|status|archive|accept|convert|parent.set|dependency.add|dependency.remove|blocker.add|blocker.remove \
  --request-file /absolute/private/request.json \
  --idempotency-key-file /absolute/private/idempotency.txt
```

The selected command must match the operation inside the request. Both files
must be private regular files owned by the caller. The CLI snapshots validated
content before invoking either native component. It normalizes the idempotency
file to one final newline; the key is 16–128 ASCII letters, digits, `.`, `_`, `~`,
or `-`. Reuse that key for retries of the same request.

The CLI invokes the fixed binaries under the account's actual home directory:

- `Library/Application Support/Zenith/CastaliaWallet/bin/castalia-wallet-devgraph-work-v1`
- `Library/Application Support/Zenith/secS/bin/secs-devgraph-work-v1`

Only Wallet receives `DEVGRAPH_SIGNING_KEY_FILE` and
`DEVGRAPH_SIGNING_PUBLIC_KEY`. The former references the existing raw 32-byte
Ed25519 seed; the latter is its expected 64-character lowercase public-key hex.
Devgraph does not open the seed. `devgraph auth setup --key-file PATH --public-key HEX`
asks Wallet to verify the existing identity and saves a private reference for
subsequent CLI commands. `devgraph auth status --check` checks it again and reports
read-credential and grant-bundle state. See [CLI authentication setup](runbooks/cli-auth-setup.md).
No component automatically sources `.env`; a trusted launcher may export both
variables to override the saved profile. Partial pairs fail closed. Using the same Dregg identity
for Codex and Hermes gives them the same authenticated principal. Process or
agent names do not provide separate authorization.

Wallet signs a presentation, secS verifies all resource grants and reserves its
replay identity, and Devgraph receives:

```text
POST /work-operations/v1
X-Devgraph-Work-Authority: <unpadded base64url of signed projection JSON>
Idempotency-Key: <key>
Content-Type: application/json
```

The body is the semantic request below. Bearer authorization is rejected on this
endpoint. The generic legacy mutation routes still cannot use the local read
credential to write. The typed client provides `DevgraphWorkContext` and
`DevgraphHttpClient.execute_named_work`; it transports a supplied projection and
does not sign or grant authority. CLI HTTP traffic ignores environment proxies
and redirects and always targets `http://127.0.0.1:8080`.

## Closed request contract

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

All six root fields are mandatory. Kinds are Proposal, Initiative, Project,
Issue, and Task. IDs use the existing canonical lowercase identifier profile.
Create requires a null version; every other operation requires a positive safe
integer matching the subject's current version. Unknown fields, duplicate JSON
keys, floating-point numbers, unsafe integers, excessive nesting, and invalid
UTF-8 fail closed. Raw JSON is limited to 128 KiB; canonical requests to 64 KiB.
Arrays retain order, Unicode is not normalized, and default create values are
materialized identically in Python and Rust before hashing.

| Operation | Payload | Required resource grants |
| --- | --- | --- |
| `create` | `id`, `title`; optional description, priority, artifact_ids, external_link_ids | Subject |
| `patch` | Nonempty subset of title, description, priority, artifact_ids, external_link_ids; no nulls | Subject |
| `status` | `status` | Subject |
| `archive` | `{}` | Subject |
| `accept` | `decision_id`, `decision_title`; Proposal only | Proposal and new Decision |
| `convert` | `issue_id`, `decision_id`; accepted Proposal only | Proposal, new Issue, and its existing acceptance Decision |
| `parent.set` | Mandatory `previous_parent` and `parent`, each null or a reference | Child and both non-null parents |
| `dependency.add/remove` | `target` reference | Subject and target |
| `blocker.add/remove` | `target` reference; Task only | Both Tasks |

A reference is `{"kind":"Project","id":"project-a","expected_version":2}`.
Resources are derived from validated content, deduplicated, and sorted. Their
signed form is an array of `Kind/id` strings. Decision is provenance, not a sixth
CLI Work kind. Conversion must name the actual immutable acceptance Decision;
its source Proposal version and all three resources are bound.

Parentage follows Initiative → Project → Issue → Task. Attaching requires an
explicit null previous parent. Detaching supplies the actual previous parent
and a null new parent. Reparenting binds the old and new parent versions. Both
null is rejected. Relationships cannot involve archived endpoints. Dependency
means subject → target `DEPENDS_ON`; blocker means subject → target `BLOCKS`.
Self-links and directed cycles are rejected. Relationship changes advance the
versions of both Work endpoints, including accepted no-op add/remove requests;
retries return the original receipt without another increment. Reparenting also
advances the old parent's version. Conversion advances the source Proposal.
Archive retains existing relationships and does not cascade.

## Signatures, policy, and execution

The authority operation is `devgraph.work.<operation>.v1`. These domains are
separate from Issue-create v1 (each displayed `\0` is a single NUL byte):

| Binding | Domain |
| --- | --- |
| Request SHA-256 | `devgraph.work-request.v1\0` |
| Wallet Ed25519 signature | `devgraph.work.wallet-presentation.v1/signature\0` |
| Wallet presentation SHA-256 | `devgraph.work.wallet-presentation.v1/presentation\0` |
| Policy SHA-256 | `secs-devgraph-work-policy.v1\0` |
| secS context SHA-256 | `secs-devgraph-work-context.v1\0` |
| secS Ed25519 signature | `secs-devgraph-work-authority.v1/signature\0` |
| Full projection correlation SHA-256 | `secs-devgraph-work-authority.v1/projection\0` |

Presentations and projections bind the audience `devgraph://receiver-local`,
request digest, idempotency digest, operation, resources, principal/session,
nonce, and lifetime. Maximum lifetime is 60 seconds. Current deny rules override
allows; issuance expiry is capped by grant expiry or an approaching deny rule.
Every resource requires its own matching current grant. secS requires a pinned
production authority key. Devgraph verifies the signed projection and exact
owner-held policy binding before accessing mutation storage, then reloads the
current receiver manifest and verifier registry and checks authority again after
acquiring the database lock. A request waiting for that lock is denied if its
grant or verifier was revoked or replaced while it waited.

Authorization takes effect at this latest-pin verification inside the mutation
transaction. Revocation prevents subsequent authorization; a transaction that
already passed this check may finish and commit. Revocation does not cancel or
wait for already-authorized transactions. Their normal transaction budget still
applies. This distinction also applies to verifier rotation and policy renewal.

secS uses its existing SQLite replay reservation table with a separate
operation namespace. For named Work entries, the table's `resource` text column
contains canonical JSON of the sorted resource array. Historical Issue entries
keep their single-resource encoding. Exact retries survive a producer restart;
a conflicting `(session_id, operation, nonce)` reservation is denied.

Migration 25 adds the private `WorkMutationGuard` uniqueness constraint. A
singleton write lock serializes named mutations, cycle checks, all version
checks, and receipt creation in one Neo4j transaction across processes. The
transaction budget is 10 seconds. Relationship/provenance validation reads at
most 10,001 edges of a type and rejects graphs above the current 10,000-edge
validation budget. This is an explicit current capacity limit. The private
mutex is excluded from the public ontology constraint mirror; published
ontology release bytes remain immutable.

Responses use the existing `work`/`receipt` envelope. A new create returns 201;
other operations and duplicate retries return 200. Duplicate `work` is null and
its receipt ID is unchanged, including retries from fresh Wallet sessions.
Version conflicts return 412, relationship/idempotency conflicts 409, and
invalid authority 403 with a redacted problem envelope. Mutation, canonical
relationships, receipt, and retained authority summary commit atomically.
`pending` receipts indicate local commit, not external event delivery. A client
timeout is an unknown outcome; retry the identical request and idempotency key.

## Provisioning and release boundary

secS reads only its owner-private fixed bundle:
`~/Library/Application Support/Zenith/secS/authority/devgraph.work.v1/`.
It contains `producer-manifest.json`, `receiver-policy.json`,
`secs-public-key-registry.json`, `verifier.key`, and `replay.sqlite3`.
The manifest pins the policy digest, raw registry SHA-256, verifier key ID,
audience, and `secs-devgraph-work-replay.v1` replay schema. Files must be owner
private, with no symlinks or access-granting ACLs; output is create-only and
cannot be placed inside the service data root.

Devgraph separately reads `<data-root>/secrets/secs-magik/devgraph.work.v1/`.
Its `receiver.json` schema is `devgraph-secs-work-receiver.v1`, version 1, with
`audience`, `stable_issuer`, and `policy_binding` (`policy_id`, `policy_version`,
`policy_digest_sha256`). `secs-public-key-registry.json` uses the existing
trusted-key registry format. The receiver reloads both at every verification,
including the verification performed after waiting for the mutation lock. An absent,
unsafe, revoked, or mismatched bundle denies writes; it does not prevent reads.
Policy rotation must update the producer and receiver pins coherently.

No identity, active grant, or service trust bundle is provisioned by installing
the source. Operator-approved grants and the actual identity's public pin are
required for live acceptance. Take a fresh consistent backup before migration
25. A release limited to migration 24 will hold on the newer journal; rollback
requires either a compatible application build or a separately validated restore.
Do not delete the migration journal or modify published migration payloads.

The current evidence covers native producer interoperability, canonical
mutations, denials, retries, and a disposable Neo4j restart. Complete retained
read/denial audit, scheduled backup/retention, an independent restore drill for
the new release, storage alerts, and installed identity/grant acceptance remain
production release gates. Source implementation alone does not close them.
