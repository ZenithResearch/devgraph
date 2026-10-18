# Wallet requests and operator selection — source candidate

This extends the first access-inbox slice with application-neutral Wallet consent
and a separately authorized operator monitor at `/monitor/access/`. It does not
activate grants or issue credentials. Decisions clearly report
`approved_pending_activation`, never connected/issued/successful Work completion.

## Source owners and compatibility

- Devgraph: `auth/access_approval.py` verifies the generic consent envelope;
  `auth/access_requests.py` adapts it to canonical access requests;
  `auth/access_operator.py` owns operator policy, narrowing, reviews and decisions.
- Wallet: `castalia-wallet-core/src/access_request.rs` owns the generic parser,
  one-shot native/WASM handle and signature. Chrome uses its document-bound
  consent broker and advertises `access_request_v1`.
- Monitor: `frontend/static/access/` owns selection/client/page modules. The
  shared Wallet connection includes request/status controls when explicitly enabled.

`vector.json` is copied verbatim from Wallet's public synthetic
`docs/contracts/castalia-access-request-v1.vector.json`. It includes an actual
WASM signature; the Python test verifies the same bytes and signature. Preserve
both this new generic domain and the earlier Devgraph-specific source-preview
vector. Existing Work, Arena, credential, membership and recovery bytes are unchanged.

The generic signature domain is `castalia/access-request-approval/v1\0`.
The closed challenge binds issuer, receiver, member, installation, operation,
nonce, exact detail object and a 120-second window. Browser consent requires the
actual HTTPS origin to equal the receiver. ASCII object keys and safe integer
numbers keep canonical Python/Rust encoding equivalent. Details remain application
owned; Wallet does not interpret Devgraph roles or know who is an operator.

## Requester path

Opt-in `register_access_request_routes` adds `/devgraph/access-requests/v2/challenges`
and `/devgraph/access-requests/v2/actions`. The original preview endpoints remain
closed and byte-compatible. Generic challenges embed the original
`devgraph.access-request-action.v1` in details. Devgraph checks duplicate identity
bindings and maps to the same stored request/digest; a new transport cannot create
a duplicate or change an existing request's intent.

Devgraph requester consent explicitly selects an active membership proof. Operator consent needs the configured operator identity only; it does not silently attach or require community membership.

The monitor's Request access control obtains a challenge, then starts Wallet
consent from a separate explicit click. It retains the original unsigned request
ID/details in session recovery storage. Recovery asks for a fresh signed status
challenge; missing results remain unknown, without automatic submission.
Membership proofs, signatures, keys, encrypted recovery files and credentials are
not persisted there. Issued read access still requires the existing distinct
issuer enrollment and credential validation path.

## Operator composition and policy

The configured runtime can opt in with `devgraph.signed-read-runtime.v4`,
using the existing v3 fields plus absolute owner-private
`access_inbox_database_file` and `access_operator_policy_file` paths. The inbox
must use a separate database from credential enrollment. V1/v2/v3 reject these
administration fields and retain their behavior. V4 requires both; missing or
malformed policy fails setup. This composes routes only: it creates no issuer key,
trust, active credential or grant and does not edit the member assignment registry.

For a custom host composition, explicitly supply an `OperatorPolicy` and private inbox:

```python
from devgraph.auth.access_operator import AccessOperator, OperatorPolicy
from devgraph.api.access_operator import register_access_operator

operator = AccessOperator(inbox=inbox, policy=load_reviewed_operator_policy)
register_access_operator(app, operator)
```

The closed policy schema `devgraph.access-operator-policy.v1` has a positive
`revision`, exact `operator_public_keys` and an approved `AccessDraftV2` ceiling.
The ceiling must match the inbox's issuer/receiver. An empty operator list denies
all operators. The ceiling’s absolute validity interval also bounds operator calls; expiry never renews automatically. No localhost, ordinary membership, read credential or requester
status grants administration. Actual policy selection/install is an explicit
operator action outside these source changes.

Highest observed policy revision/digest is committed separately before handling
action transactions. Rejected revoked callers cannot roll that observation back.
Same-revision changes and older policy files are rejected across restart. Policy
is checked again inside the action transaction, including duplicate-result lookup.
A changed policy requires a new review; an old signed review cannot activate it.

HTTPS-only `/devgraph/access-operator/v1/challenges` takes exactly holder,
installation and an `OperatorAction`. It discloses no queue, request details or
ceiling before holder authentication. `/actions` takes only challenge/signature.
There is no bearer-admin path. The public static page reveals no private data.
The host may inject an independently scope-filtered inventory provider; this
candidate UI also accepts explicit Arena/object IDs without expanding relationships.

## Selection, review and decision

The operator loads the inbox with Wallet consent, selects a request, then chooses
read/write operations, all permitted work versus specific Arenas/objects, and
expiry. Counts describe the complete pending queue independently of pagination.
Each write operation stays paired with its own resources; there is no cross-product
of unrelated permissions. All matching/future-item semantics are explicit.

Selections have separate `read` and `write` drafts. Reads require exact member and
installation; current writes require exact member and explicitly disclose that
they are not installation-bound. Unsupported installation/Arena write predicates
are rejected, never dropped. Root ceiling, exact bindings, previous digest,
validity and operation/resource subsets are validated centrally. Workflow,
evidence, review, version and completion gates remain in the Work receiver.

`review` records the exact selection and existing-policy projections without
issuing anything. `approve` requires that same operator's prior review, unchanged
request digest/version, identical selection and current policy. `deny` requires
a reason. Request version advances from 1 to 2 exactly once on decision; concurrent
or stale decisions fail. The selection, decision, result and accepted-action audit
commit in one SQLite transaction. Redacted rejection audit records persist
separately; if the audit cannot persist, the request fails closed. No unverified
actor attribution, proof bodies or credentials enter those rejection records.

Decision results are retained by action ID. The browser preserves only unsigned
recovery data. Definite bounded rejection permits correction; lost/malformed
results retain the original action. `status` signs an independently authorized
lookup of the original action ID and cannot execute it. Unknown stays unknown.
A recovered old approval is evidence of its recorded decision, not current grant
validity or permission to bypass fresh issuer checks.

Default bounds: 256 outstanding operator challenges, 1,024 retained action
results, 10,000 redacted rejection records and 256 KiB per result. Inbox pages
shrink with a continuation cursor if large requested scopes would exceed the byte
budget; complete counts remain unchanged. No history is silently deleted to make
an old action ID reusable. Configure retention/backup and ingress protections
before remote deployment; exhaustion fails closed.

## Evidence and unfinished integration

Tests cover source Rust/WASM/Python parity, real disposable holder signatures,
operator versus member authority, narrowing, exact review ordering, concurrent
versions, revocation/rollback, denied bearers, status-only recovery and malformed
results. The synthetic browser walkthrough covers selection/review/decision,
four themes and 375px/1280px layouts with intercepted fixture traffic.

Still outside this candidate: actual issuer activation/revocation composition,
Arena-scoped write enforcement, a hierarchical server-backed scope browser,
Castalia Web and iOS request adapters, loaded Chrome acceptance, and installed
end-to-end qualification with reviewed disposable operator policy. There is no
sync operation or sync permission in these contracts. Full Work administration
must not be described as active from selecting an unsigned draft.

For the separately opted-in read activation candidate, see [v2 activation](../v2/README.md). V4 retains the pending-only behavior documented above.
