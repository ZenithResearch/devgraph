# Authorization

Devgraph authorizes every facade call from an injected `CredentialVerifier`.
The verifier returns a `CredentialEnvelope` containing actor, session,
correlation, scopes, expiry, issuer, audience, and redaction partitions.

## Exact-scope policy

| Operation category | Required scope |
|---|---|
| read | `devgraph.read` |
| write | `devgraph.write` |
| admin | `devgraph.admin` |
| internal export | `devgraph.export.internal` |
| redacted and summary export | `devgraph.export.redacted` |
| tool use | `devgraph.tool.use` |
| skill use | `devgraph.skill.use` |

Each category accepts only its dedicated grant. Admin does not imply read,
write, or export. There is no trusted-localhost bypass.

`AuthorizedWorkGraph` verifies per call, checks the category, delegates to the
domain service, and records actor/session/correlation/category/operation in an
in-memory audit sink. Idempotent writes reuse
`AuthorizedWorkGraph.authorize_write`; transport code does not create
authority. Denied operations do not reach the delegate, append successful
audit state, or disclose a prior receipt.

The repository packages a loopback-only local-host workflow and an in-process
HTTP adapter. Local installation provisions one non-canonical operator access
capability, but Devgraph does not mint canonical identity and is not a secS
credential minter, Dregg contract issuer, or Matrix identity authority.
Export/redaction behavior is
documented separately in [export and redaction](export-redaction.md).

## Included verifier

`LocalDevVerifier` is a test/development fixture. It is enabled only when its
mode is exactly `local-dev`, accepts only credential strings explicitly
registered in memory, and checks expiry and audience. In every other mode it
fails closed.

`LocalReadCredentialVerifier` is the private-host verifier selected only by
`DEVGRAPH_AUTH_MODE=local-read`. `devgraph local configure` creates a 256-bit
random bearer value in an owner-only client file and a separate receiver-owned
registry containing only its SHA-256 digest, actor/session/correlation claims,
audience, and expiry. The verifier reloads that registry for each call so
rotation revokes the old value immediately. It constructs a
`CredentialEnvelope` with exactly `devgraph.read`; the file format has no scope
field and cannot authorize write, admin, or export operations. This is local
operator access authority, not Castalia membership or a generic production
credential format.

The repository does not include a generic production credential parser,
identity provider, token minting service, generic secS/Dregg/macaroon adapter,
or durable audit sink. Its exact Issue-create and monitor-read consumers do not
widen the bearer verifier seam. `devgraph.local_app` registers one synthetic
`devgraph.read` credential; it cannot authorize writes or exports.

Separately, `SecSIssueCreateAdapter` is a transport-free exact-operation
consumer for `devgraph.issue.create.v1`. It accepts no bearer credential or
generic `AuthorityContext`: it verifies raw signed projection bytes with
receiver-owned policy and secS production-key registries, derives exactly
`devgraph.write`, and privately opens one graph-owned session exposing only
`create_issue()`. The adapter constructs repository and outbox over one storage
instance. Invalid verification, conflicting retry scope, audit failure, or
receipt/edge failure leaves no successful mutation. This narrow seam is not
installed into FastAPI or its local runtime. The separate fixed
`devgraph secs-issue-create-v1` command loads receiver-owned trust roots and
the configured loopback Neo4j store, then delegates only to this adapter. It
does not create a generic credential path or change fail-closed HTTP auth.

Separately, `SecSMonitorViewReadAdapter` is the exact HTTP consumer for
`devgraph.monitor.view.read.v1`. It never constructs a `CredentialEnvelope` or
grants `devgraph.read`. A secS production key verifies a session binding the
fixed audience, origin, receiver policy, and ephemeral page Ed25519 key; that
key verifies one exact GET proof bound to signed-session digest, raw
path/query, timestamp, nonce, and empty-body digest. The only delegate is the
existing safe monitor snapshot. An owner-private durable exact-operation store
claims the proof digest/nonce before the read, rejects replay across restart,
and fails closed on clock rollback or capacity exhaustion. It mutates durable
state only after every signature, policy, currentness, and transport check has
passed and one unique claim can commit; denials do not rewrite it. See
[monitor proof of possession](monitor-proof-of-possession.md).

## Caller/session authority flow

1. The caller presents an opaque credential string to a credential-taking `AuthorizedWorkGraph` method or to the thin API adapter (`src/devgraph/auth/enforcement.py`, `src/devgraph/api/`).
2. Read/export façade calls verify directly. Idempotent API writes call `AuthorizedWorkGraph.authorize_write` exactly once; verifier and `devgraph.write` scope checks complete before any duplicate lookup.
3. Successful write authorization yields an immutable graph-bound write session. The verified `AuthorityContext` remains in a graph-owned weak-key registry rather than on the session object; registry validation and single-use removal are one atomic locked operation. Supplying or mutating a caller-constructed session cannot alter authority.
4. A verified caller without the required scope gets a `ForbiddenError` before mutation, audit, receipt lookup, or receipt disclosure.
5. Direct façade writes atomically consume the session and delegate. API writes use the same registry-owned context for mutation plus receipt attribution inside the storage/outbox transaction. Audit entries accumulate in an execution-context-local `ContextVar` buffer and publish under a lock only after the guarded transaction commits; rollback discards only that execution context's entries, including when asyncio tasks share a thread. Cancellation and other `BaseException` exits restore the prior execution context and discard pending entries before propagation.

Properties, each proven by tests in `tests/auth/`:

- **One verification per guarded request.** Read/export façade calls verify once. Each idempotent API write verifies once through `authorize_write` before duplicate lookup; the resulting session is the only mutation capability and evidence context.
- **Same contract for local and remote callers.** The façade depends only on the `CredentialVerifier` protocol; the substitutability test in `tests/auth/test_fail_closed.py` runs the same call path against the local fixture verifier and a remote-style stand-in and asserts identical results and audit records.
- **Denied calls never reach the delegate and never write an audit record.**

## Scope vocabulary and policy matrix

The v0 vocabulary is exactly seven scopes; each operation category requires exactly its own dedicated scope (`src/devgraph/auth/scopes.py`):

| Operation category | Required scope             |
| ------------------ | -------------------------- |
| read               | `devgraph.read`            |
| write              | `devgraph.write`           |
| admin              | `devgraph.admin`           |
| export.internal    | `devgraph.export.internal` |
| export.redacted    | `devgraph.export.redacted` |
| tool.use           | `devgraph.tool.use`        |
| skill.use          | `devgraph.skill.use`       |

Every grant is explicit: the admin scope does not implicitly satisfy any other category — broad DB access is a granted scope, not implied by agent identity. The tool and skill categories remain policy vocabulary enforced at the `require_scope` level only. Export operations (record-input, from GitHub #16) and the export-by-ids record source (GitHub #25) gate through the export categories.

## Authorized work-object operations (GitHub #25 / Issue 16)

The façade composes a third unauthorized delegate, `WorkObjectRepository` (`src/devgraph/model/repository.py`), which owns kind-aware node↔model rehydration over the `GraphStorage` protocol for the full `devgraph.model.work` class set. Credential-taking façade writes and API idempotent writes both enter through `authorize_write`; only its graph-registered, single-use session can invoke write delegates:

| Operation | Category | Notes |
| --- | --- | --- |
| `get_work_object`, `query_work_objects` | read | Query scope is kind plus archived-inclusion only. |
| `create_work_object` | write | Duplicate kind/id fails closed. |
| `update_work_object` | write | Compare-and-set on `version` inside `storage.transaction()`; a stale expected version raises a safe conflict error carrying identifiers and version numbers only, with zero mutation. Update cannot change status. |
| `archive_work_object` | write | The only path into `ARCHIVED`. |
| `transition_work_object_status` | write | Validated against the explicit `GENERIC_STATUS_TRANSITIONS` table; cannot reach `ACCEPTED` for Proposals (Decision provenance stays exclusively `accept_proposal`), cannot leave terminal statuses. |
| `get_initiative_observation`, `query_initiative_observations` | read | Reads the append-only inferred observation projection. |
| `create_initiative_observation` | write | Appends one observation; claiming, amendment, and rejection mutations are absent. |
| `monitor_snapshot` | read | Returns the safe aggregate/topology projection; Project and Issue progress is derived, not stored. |
| `export_internal_by_ids` | export.internal | Fetches records internally via the repository as part of the export: an export-scoped credential needs no read scope, and a read-scoped credential cannot export. A missing id fails closed — never a partial export. |
| `export_redacted_by_ids`, `export_public_safe_summary_by_ids` | export.redacted | Same record-source rule as internal export. |

API idempotent writes authorize and scope-check before entering `EventOutbox.record_mutation_with_receipt`. The graph-bound write session then supplies mutation authority and receipt attribution. Wrong-scope requests reject before duplicate lookup; denied calls never reach the delegate, append audit state, or receive a prior receipt.

## Credential envelope

`CredentialEnvelope` (`src/devgraph/auth/credentials.py`) is the parsed claim set: actor id, session id, correlation id, scopes, expiry, issuer, audience, and redaction partition claims. It never stores the opaque credential string, so its repr/str cannot leak a raw token value, and it rejects unknown scopes at construction. Field semantics and the governing decision are documented in `docs/auth/credential-envelope.md`.

## Environment gate and fail-closed posture

The fixture verifier `LocalDevVerifier` is gated by the `DEVGRAPH_AUTH_MODE` environment variable. Only the literal value `local-dev` enables verification of explicitly registered fixtures; every other value — including unset — fails closed with a safe unauthenticated error, even for a credential with full scopes. This is the production-like posture and it is the default.

- There is no trusted-localhost bypass: nothing in the auth package inspects hostnames, IPs, or network interfaces.
- devgraph does not mint canonical identity: the verifier never fabricates envelopes; only registered fixtures verify, and its public surface is exactly `from_env`, `register`, and `verify` (asserted by test).
- Test/dev fixtures use obviously fake credential strings only (e.g. `fake-credential-alpha`). No raw token values appear in code, tests, or this document.

## Safe errors

Typed errors in `src/devgraph/auth/errors.py`, aligned with the "Safe errors" rule in `docs/service-contract.md`:

- `UnauthenticatedError` — 401-style denial: missing, unknown, or expired credential; audience mismatch; or verification unavailable in the current mode.
- `ForbiddenError` — 403-style denial: verified caller lacks the scope its operation category requires. The message names the denied category only (e.g. `scope for category 'write' not granted`) and carries the caller's correlation id for audit lookup.

Error messages never echo credential values, envelope claim dumps, or driver traces.

## Future adapters

Generic secS envelopes, Dregg contract credentials, and macaroon-shaped
attenuation remain future adapters of the `CredentialVerifier` seam. The exact
Issue-create and monitor-read consumers do not ratify or implement any of those
generic credential formats. The bearer-oriented HTTP API still ships only the
environment-gated fixture and fixed local read verifier behind that protocol.

## HTTP behavior

Data routes accept `Authorization: Bearer <credential>` and pass the extracted
credential to the facade. Missing/unrecognized/expired/audience-mismatched
credentials return safe 401 problems; insufficient scope returns 403. The
static frontend shell at `/` and `/monitor` is unprotected, but it contains no
graph data or embedded credential.

The memory-backed `devgraph.local_app` registers only a synthetic read
credential for local inspection. The private Neo4j-backed local host accepts
only its owner-provisioned local read capability on the general read surface.
Without that capability, the general protected HTTP surface has no local credential bypass.
The monitor snapshot accepts the same read capability and can independently
accept its exact PoP receiver; that receiver creates no credential bypass for
any other route.
Separately, the operator CLI exposes exactly one local `secs-issue-create-v1` mutation.
The projection-consumer form accepts the three owner-only artifacts: request,
signed-authority-projection, and idempotency-key.
The one-shot Wallet form invokes only the fixed installed secS adapter, holds
its projection in an owner-private temporary directory, and immediately invokes
the same exact Issue-create receiver. It exposes no generic credential verifier
or generic Work mutation; it accepts no executable selector or authority-selection
surface.

`DevgraphHttpClient` receives an explicit opaque credential per call. It does
not discover, issue, persist, refresh, or print credentials, and it has no
trusted-localhost fallback. The operator CLI loads the owner-only local value
outside the client and passes it only to bounded get/list calls.

## Verification

```bash
uv run pytest tests/auth tests/api/test_route_boundaries.py -q
```
