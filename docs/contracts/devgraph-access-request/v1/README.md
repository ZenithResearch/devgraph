# Access request inbox v1 — source preview

This is the first backend slice of Task/devgraph-access-http-request-inbox.
It records a member's request for operator consideration; it never issues a
credential, activates a grant, approves a scope or changes canonical Work.
Requesting access and receiving access are different operations.

## Ownership and compatibility

Public `ZenithResearch/devgraph` owns the request schema, persistence and routes:

- `auth/access_requests.py`: closed action/challenge types and durable inbox.
- `api/access_requests.py`: explicitly registered HTTPS routes.
- `auth/monitor_read.py`: existing six-Todo read scope vocabulary.
- `auth/access_policy.py`: existing exact-operation write selection vocabulary.

The new application-request signing domain is
`devgraph/access-request-approval/v1\0` (one trailing NUL). It is not a credential,
Work signature, enrollment challenge or child delegation. Existing Wallet/secS
credential, Work, Arena and enrollment bytes are unchanged. The schema files
here are generated from the Python models; semantic validation remains required.

**Matching Wallet/core admission, user disclosure and client integration are not
implemented by this backend slice.** Applications must not ask for arbitrary raw
signatures or route these challenges through old read-enrollment/signing methods.
A future Wallet change must validate the exact request type, bind the actual
origin/receiver, display the complete intent and require explicit consent using
the shared custody core. Browser/iOS qualification remains outstanding.

## Explicit composition

The host supplies an absolute SQLite path in an existing owner-private directory,
a fixed issuer and HTTPS receiver origin, and its independently configured
`ZenithMembershipValidator` (including current revocation/trust policy). No file
or request may install membership roots. There are no live defaults, automatic
registration, new environment switches or changes to production composition.

```python
from devgraph.auth.access_requests import AccessRequestInbox
from devgraph.api.access_requests import register_access_request_routes

inbox = AccessRequestInbox(
    path=private_directory / "access-requests.sqlite",
    issuer=configured_issuer,
    receiver=configured_https_origin,
    membership_check=approved_membership_validator,
)
register_access_request_routes(app, inbox)
```

Use reviewed TLS/proxy and ingress-abuse controls before remote deployment.
The routes require HTTPS and the configured receiver authority; they do not
trust a raw `X-Forwarded-Proto` header. The app registers no CORS exceptions.
Quotas bound storage, but are not an Internet-facing DDoS protection service.

## Request and recovery protocol

1. Generate a random 16-byte lowercase-hex request ID once. Retain the exact
   requested intent and ID through uncertain delivery; do not silently replace it.
2. POST `/devgraph/access-requests/challenges` with exactly `{"request": action}`.
   The action has schema `devgraph.access-request-action.v1`, `action=submit`,
   `request_id`, `subject_public_key` (32-byte hex), `installation_id` (16-byte hex)
   and `intent`. Null intent requests access without selecting permissions;
   **null never means global access**. Otherwise use nonempty `read_grants` and/or
   `write_grants` from the existing supported vocabulary. Unsupported restrictions
   fail validation; a requested permission is not an approved permission.
3. The server returns a closed challenge binding that exact action, issuer,
   receiver, nonce and 120-second validity. Sign domain plus canonical JSON
   (UTF-8, sorted keys, compact separators, no NaN; preserve array order).
4. POST `/devgraph/access-requests/actions` with exactly `challenge`, trusted
   membership `proof`, and the holder's unpadded base64url Ed25519 `signature`.
   Verify the signature, current membership and exact stored challenge before
   using an existing record or inserting one. A public membership proof alone
   cannot submit or read status; a read bearer cannot authorize this path.
5. A durable response is `devgraph.access-request-result.v1` with request ID,
   `status=pending`, original request digest, received/expiry timestamps and `authority_issued=false`.
   The response is returned only after the SQLite transaction commits.

For explicit recovery, request a fresh challenge with `action=status`, the
**original request ID**, same holder/installation and `intent=null`. Obtain fresh
Wallet consent and submit its signed challenge. This only reads status. Missing
records return `status=unknown`; they do not trigger submission. A matching
explicit submit retry returns the original record/timestamps without another
request or audit entry. Different content under an existing ID is rejected.

All endpoints return `Cache-Control: no-store`. Errors are bounded and redacted;
503 means unavailable, not success or proof of non-commit. Keep the original
request and ID for reconciliation. Authorization headers, including read bearers,
are rejected rather than treated as request authority.

## Persistence, bounds and operator boundary

The separate owner-private SQLite database uses FULL synchronous transactions.
Request insertion, its local audit entry and challenge consumption commit
atomically. The audit is an inbox record, **not** a Work EventReceipt or a signed
portable attestation. Membership proofs, signatures, recovery material and issuer
keys are not retained. Only public member/device identifiers, requested scope,
digest and timestamps are retained as sensitive local application metadata.

The first slice retains pending requests for a fixed seven-day review window;
after that status is expired. There is no approval/issuance state yet. Default
limits are 10,000 retained records, 32 active requests per member across devices,
256 unexpired challenges globally and eight per member. Exhaustion fails closed.
No history is deleted to make a duplicate ID usable again. A separately reviewed
retention/backup policy is required before exposing this remotely.

`inbox.pending(limit=1..100, after_id=...)` is an **owner-process-only** projection.
It returns a transaction-consistent complete pending count plus a bounded page,
including exact requested scope and holder/device bindings. Pages sort by request
ID. Separate page traversals are current-state reads, not a frozen global snapshot.
There is deliberately no public inbox-list/approve/grant route. Future monitor
routes must independently authorize the operator, and version-bind decisions to
request and policy state. Applicant membership is never operator authority.

The private store is additive. It does not backfill progress, alter migration 28,
rewrite existing credentials or touch production data. Reopening with a different
issuer/receiver fails. Interrupted/failed transactions preserve committed records.

## Verification and remaining work

Tests use disposable real Zenith membership and Ed25519 holder signatures. They
cover all six Todo read intents, signed submission, tampering, invalid membership,
revocation, cross-member/device isolation, expiry, capacity, concurrent exact
retries, restart/status recovery, unknown results, private storage, aborted audit
writes, HTTPS/receiver checks and closed HTTP fields. Existing native read and
member-policy regressions run separately.

Still required: matching Wallet/core/SDK request support and consent; independent
operator authorization and monitor inbox; versioned decisions and issuer activation;
Arena-scoped write semantics; cancellation/decision lifecycle; installed browser,
iOS/native and remote transport qualification. No source test activates trust,
expands a grant, deploys this API or closes the scoped-access MVP release gate.
