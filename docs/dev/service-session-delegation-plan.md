# Service-session delegation: audited integration plan

Status: design candidate, not an implemented or advertised write profile. No
runtime, policy, grant, Wallet data or installed application is changed by this
document. The existing read-only holder-session v1 profile stays compatible.

## Intended authority model

After explicit Wallet consent, an application holds an issuer-verifiable,
proof-of-possession credential bound to its own key. Its permitted operations
may include reads or writes, but must remain within the selected signed parent,
current receiver policy, resource constraints and human-approved expiry. The
application does not receive the Wallet key, reusable parent, root signer or
permission to issue further authority. Wallet locking is independent of the
already-issued application credential. Account/transaction signatures outside
the approved service authority still require their own Wallet approval.

The browser stores a non-extractable CryptoKey and the exact credential in
origin-local IndexedDB. Refresh/navigation restores the same authority and
absolute expiry; it does not renew it. Origin storage is shared across pages and
tabs and is not cryptographic per-tab isolation. A child signature binds each
protected request. Only allowlisted service requests receive credentials, not
arbitrary assets, external links or redirects. A public application shell can
restore authority before requesting protected data; ordinary document navigation
does not automatically attach JavaScript-created credential headers.

This model does not require a backend login cookie. It does require a service
verifier and authoritative current-parent, policy, replay and mutation-result
state. Non-exportability does not prevent same-origin XSS from using a key for
signing, and does not guarantee hardware custody or secure disk erasure.

## Source evidence and actual limits

Audited canonical sources:

- secS `cbe2be36cd81648092a6f9c2e39473e63d20f65a`.
- Wallet/Core `690ad3ee38ae3f429ddb08ba897367be9292abfc`.
- DevGraph baseline `38d7c2af3d5a69600057d3eecd0d37ed9da4bacb`.

Shared secS `holder_sessions::Challenge.permissions` is generic. Parent binding
uses `scoped_grants::permissions_within`, enforces parent validity and maximum
child lifetime, and requires current-parent verification at the receiver. Core
`PreparedHolderSession` accepts a signed v2 parent and keeps root signing separate
from public returned-token verification.

However, the **one-hour ceiling is also in the shared signed-parent contract**:
`crates/secs-grant-contract/src/scoped_grants.rs::validate_grant` validates v2
`max_credential_lifetime_seconds` in `60..=3600`. The v1 validator in `lib.rs`
has the same bound. `holder_sessions::parent_binding` then limits the session to
that parent maximum. Wallet's vendored contract retains these checks. Removing
DevGraph/browser `3600` checks alone cannot create a valid longer-lived session.
Longer lifetimes require a separately versioned shared parent contract and a
coordinated producer/consumer migration. Existing signed v1/v2 records retain
their meaning and limits. User-selected duration is bounded by these genuine
parent/service ceilings; fifteen minutes is not a universal architectural rule.

The current DevGraph profile has additional implementation restrictions:

- `auth/holder_sessions.py` accepts four Work/Arena read operations only and
  bounds `HolderSessionPolicy`/`PrepareRequest` TTL to one hour.
- `api/holder_sessions.py` emits closed v1 capabilities with `read_only:true`.
- The browser and extension strictly validate that capability and cap TTL;
  the old browser path chooses `min(900, advertised maximum)`.
- `HolderGrantHost.mint` compiles reusable parent permissions from read
  enrollment. `revision` binds its read assignment/epoch/activation, not a
  complete write assignment. Separate write access does not enlarge that parent.
- `auth/selector_grants.py` supports one `target` role and a transaction-local
  read view. `_verified_context` returns `read_grants` and consumes a SQLite
  replay record before the domain transaction. That is not a write adapter.

## Proposed additive discovery and policy contract

Names below are coordination proposals, not deployed capabilities. Confirm them
with secS/Core, Wallet and browser owners before freezing or implementing them.

| Surface | Candidate |
| --- | --- |
| HTTP base | `/service-sessions/v1` |
| Negotiated profile | `secs.service-session.v1` |
| Capabilities | `secs.service-session-capabilities.v1` |
| Prepare input | `secs.service-session-prepare.v1` |
| Receiver-owned policy | `devgraph.service-session-policy.v1` |

The proposed closed capability fields are `schema`, `supported`, `profile`,
`issuer_pin`, `audience`, `grant_schemas`, `credential_schema`,
`presentation_schema`, `operations`, `max_ttl_seconds`, `review_ttl_seconds`,
`proof_max_ttl_seconds`, `challenge_path`, `exchange_path`, `status_path`,
`call_path`, and `call_status_path`. `operations` lists implemented receiver
operations; it neither grants them nor implies an operation exists merely
because it was placed in configuration. `read_only:false` is not a replacement
for an explicit operation catalogue.

Prepare retains the existing named fields but requires an explicit nonempty
`permissions` array. It must not interpret null/absence as every parent grant
permission. The policy has `schema`, `enabled`, `allowed_origins`, `audience`,
`allowed_operations` and `max_ttl_seconds`. Policy is trusted operator input,
not discovery-derived authority. Initially v2 parents still imply a maximum
3600-second child. Broader lifetime support is a distinct contract migration.

The existing signed session challenge/authorization/credential/presentation v1
shapes already bind service, origin, audience, keys, permissions and expiry.
Reusing those shapes is possible only where their exact validation semantics
remain unchanged and a trusted retained record binds profile/policy selection.
An untrusted route or page-supplied profile must never select weaker verification.
No new signed fields or schema semantics are silently added to old envelopes.
Existing read v1 capability field sets, routes, grants and signature domains stay
unchanged; older clients reject unsupported new profiles safely.

New call transport can reuse the `{credential,call,proof}` presentation envelope.
The typed `call.parameters` must bind the full closed named command and the
idempotency key/digest. Mutating that key, command, resource, operation or any
effect invalidates the proof. Status must be a non-mutating, authenticated
original-result lookup with exact request/credential binding; it never dispatches
the mutation, creates a new key, or renews authority. Recovery after hard
credential expiry requires a separately reviewed mechanism, not acceptance of
expired proofs. Tokens, signatures and keys never appear in URLs or logs.

## Required backend integration and ownership

| Component | Required change and retained boundary |
| --- | --- |
| New service-session admission/router | Explicit general profile, implemented operation catalogue, exact parent subset and independently observed origin. Preserve `auth/holder_sessions.py`/`api/holder_sessions.py` read v1. |
| Reusable parent compiler | Version the provider compiler/registry binding for approved read plus write authority. Include write assignment/policy generations in current-parent checks. Existing parents are never rewritten or widened; new authority requires its own reviewed issuance. |
| Credential-to-command verifier | Verify native child proof, current parent, current receiver policy and exact named request. Do not forge a trusted `secs-devgraph-work-authority.v2` projection or treat a child signature as a Wallet-root presentation. |
| Resource effect guard | Enforce the child scope in addition to provider parent/write policy. Resolve identities/ancestry from current graph; check subject, source, destination, related and affected objects. Preserve exact objects versus descendants/all and every original hierarchy guard. |
| Transactional execution | Integrate with `ScopedSecSWorkAdapter`, `PairedGuard`, graph mutation/receipt/outbox and current authority fences. Same-domain mutation and retained outcome must commit once. Do not consume a SQLite proof and claim it is atomic with a separate graph commit. |
| Factory/configuration | Explicit opt-in through the reviewed composition; no new listener, discovered policy, automatic parent migration or live write activation. Coordinate `signed_read_runtime.py` with its owner. |
| secS/Core/Chrome/native clients | Negotiate the additive profile, exact constraints and actual duration ceiling. Show operation/resource/expiry consent; persist only child application custody. Existing v1/v2 grant readers and read profile stay supported. |

Current named writes use `BrowserCredentialWorkHost`, a native signed
`secs-devgraph-work-authority.v2` projection, `ProvisionedWriteReceiver`,
`ScopedSecSWorkAdapter`, `PairedGuard` and the graph's mutation transaction.
This path requires per-operation Wallet presentation today. It is not already
a receiver for browser-owned session credentials. A general session receiver
must either add an independently validated admission path into the same domain
command engine or define a reviewed native session-to-command projection. It
must not bypass the existing receiver because a shared signature verified.

For moves and other compound effects, a single complete permission rule must
authorize all required effects; unrelated rules cannot be pooled to launder a
move. Recheck resource placement/version and current authority inside the same
transaction that commits the mutation. Reject unsupported operations and unknown
constraint/role forms; do not approximate them as unrestricted scope.

## Deployment and release gates

The independent Fly.io-perspective review was performed by an agent, not a
Fly.io employee or representative. Its principal deployment finding is that
independent machine-local SQLite copies cannot provide globally consistent
replay, revocation and original-write outcomes behind a load balancer. Fly
Volumes are per-Machine and are not automatically replicated. Start with one
authoritative durable transaction path or deliberately design a consistent
authority service/database. Route before effect/proof consumption when forwarding;
the final executor owns replay and mutation outcome. Unavailable authority state
fails closed. Routing, region or access point never changes canonical service
authority or expands separately approved origin restrictions.

Required end-to-end gates before advertising write support:

1. An explicitly issued eligible parent permits a narrowed child write; an old
   read-only parent, unsupported grant version or wider operation/scope is denied.
2. Equal/narrower scopes survive derivation; cross-hierarchy moves require both
   ends and all affected resources under one applicable rule.
3. Child reads and writes work after Wallet lock/extension closure and same-origin
   navigation/refresh with byte-identical credential and original absolute expiry.
4. Replay on another replica cannot repeat a mutation; timeout/restart recovers
   the original command result with the same idempotency identity.
5. Parent revocation/replacement, policy change, queued expiry and a storage crash
   cannot produce a partially applied or newly authorized write.
6. Lost/corrupted browser storage, mismatched keys, hostile origins/documents,
   logout/slow callbacks and MV3 suspension do not restore root signing or renew.
7. Old read clients and stored grants retain exact behavior; mixed-version
   deployment rejects unknown scope semantics rather than weakening checks.

No allowed-write test or production qualification is claimed until the parent
compiler and transactional receiver actually exist. The current bounded source
fix only makes authenticated unissued read-review expiry terminal and durable;
it does not enable the proposed profile.

For the bounded expiry fix, drain all old exchange writers during rollout. The
database trigger protects a recorded terminal outcome against a stale writer;
it cannot repair an old writer that admits a late request before any tombstone
exists. Rolling back to the old binary must leave exchange admission disabled
or retain the patched admission path. This server requirement does not change
old signed credential or client compatibility.

References: [Web Crypto security](https://www.w3.org/TR/webcrypto-1/#security-considerations),
[proof-of-possession guidance](https://www.rfc-editor.org/info/rfc9449/),
[Fly Volumes](https://docs.fly.io/volumes/overview),
[Fly Proxy](https://docs.fly.io/reference/fly-proxy), and
[Fly request routing](https://docs.fly.io/networking/dynamic-request-routing).
