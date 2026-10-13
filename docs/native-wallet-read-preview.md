# Native Wallet read access preview

Compatibility preview only. The replacement is [shared signed read authority](native-signed-read.md).
Do not activate this bearer prototype as the native production read path.

This opt-in source adapter lets a native Zenith app provision a bounded Devgraph read credential through its own Castalia Wallet identity. It does not change installed service configuration, browser credential transport, generic Wallet signing, secS, Work request bytes, lifecycle semantics, or existing read responses.

## Boundary and composition

The operator explicitly composes `devgraph.api.native_read.create_native_read_app` with graph storage, an audit log, a `NativeReadEnrollment` registry and independently configured membership verification. No environment variable automatically adds routes to the live host, and no owner read key is loaded. Use a fresh owner-private directory for the enrollment SQLite database. Deploying the composition or choosing live policy is a separate action.

`ZenithMembershipValidator` accepts frozen v3 and scoped v4 public membership credentials. It verifies the owner, membership ID, approved issuer and scope, canonical signature bytes, active status and an operator-supplied revocation predicate. Trust comes from a separate callback; a client/file cannot install it. Membership proves eligibility, not a resource grant. The same policy, proof and revocation checks run again on every read.

The preview `EnrollmentPolicy` schema is `devgraph.native-read-policy.v1`: an issuer identifier, exact HTTPS receiver origin (no path/trailing slash), 60–86,400-second lifetime, and 1–16 existing `WorkReadGrant` entries. Only `devgraph.work.read` with `list`/`get` is admitted. Work/arena selectors and archive visibility remain explicit. Configure the receiver's externally approved HTTPS origin when placing it behind a TLS proxy; do not infer trust from request headers. Apply rate/body limits and avoid authorization/body logging at that proxy.

```python
from devgraph.api.native_read import create_native_read_app
from devgraph.auth.native_read import NativeReadEnrollment
from devgraph.auth.native_membership import ZenithMembershipValidator

# Values below are explicit host dependencies, not global defaults.
registry = NativeReadEnrollment(
    path=private_enrollment_database,
    policy=load_approved_read_policy,
    membership_check=ZenithMembershipValidator(load_approved_trust, is_membership_revoked),
)
app = create_native_read_app(storage=graph_storage, enrollment=registry, audit_log=audit_log)
```

## Wire contract

1. `POST /devgraph/native-read/challenges` accepts exactly `subject_public_key` (32-byte lowercase hex) and `installation_id` (16-byte lowercase hex). It returns `castalia.read-access-challenge.v1`: request ID, random nonce, owner/installation, issuer, receiver, opaque application `details`, issue/expiry times and credential expiry. Challenge validity is 120 seconds.
2. Devgraph details use `devgraph.native-read-scope.v1` with the exact policy digest and read grants. The native UI displays issuer, server, scope, archive access and expiry before requesting internal Wallet approval. Wallet signs `castalia/read-access-approval/v1\0` followed by canonical JCS of the **complete** challenge.
3. `POST /devgraph/native-read/enrollments` accepts exactly `challenge`, `proof`, and `signature`. Strict bounded JSON rejects duplicate/unknown fields. The receiver validates the stored, unused challenge, current policy, trusted proof and Ed25519 possession. A database transaction consumes the challenge and registers one opaque `dgrn1_` credential. Responses have `Cache-Control: no-store`.
4. The result includes credential plus `devgraph.native-read-manifest.v1`, bound to owner, installation, issuer, receiver, policy digest, Devgraph audience/resource, expiry and exact grants. The native client validates this before committing encrypted local state. The server stores only the credential digest, its public manifest and proof. Logs/receipts must not contain bearer values.
5. Existing bounded `GET /devgraph/work/{kind}` and `GET /devgraph/work/{kind}/{id}` enforce the manifest on every read. Read credentials never authorize mutation routes; this composition registers none.

Renewal retains the installation's original authority ceiling and invalidates its older active credential. Changed policy fails current credentials closed; broader scopes cannot be gained by renewal. Revoking an installation blocks further renewal; restoring portable files creates no server enrollment and restores no read credential. A new enrollment remains an explicit member/operator-policy decision.

Read projections are deliberately legacy-compatible: five concrete Work types and their original envelopes. Base Todo and canonical progress are not added to this v1 delegated projection. Native UI does not infer progress from Draft/Accepted. Extending this read projection requires a versioned shared contract.

## Failure and recovery

Denial/replay/expiry/revocation return redacted access errors. No exception includes a credential. If enrollment succeeds but its response is lost, the app has no committed access and can explicitly reconnect after disclosure; a subsequent approved enrollment supersedes the lost credential. This read-enrollment protocol is not a Work mutation/status-reconciliation protocol.

The SQLite registry bounds active challenges and enrolled installations, but historical credential records require an operator retention/backup policy before public release. No cleanup or revocation action is exposed unauthenticated over HTTP. `registry.revoke(credential_id)` revokes the whole installation through an authorized local operator composition. Local disconnect merely removes the app's copy.

## Checks and release boundary

Run `tests/auth`, `tests/storage/test_delegated_filtering.py`, `tests/api/test_delegated_read.py` and `tests/native_read`. The latter includes public TypeScript v3/v4 vectors, invalid trust/scope cases, explicit grants, replay, policy change, expiry, revocation, pagination, hidden resources and denied mutation routes.

For actual cross-language execution, build Wallet's `recovery_contract_driver` and set `CASTALIA_RECOVERY_TEST_DRIVER` to its absolute executable path. That test creates disposable Rust custody, checks its backup, approves trust, imports an issuer proof, signs the real read challenge, obtains a scoped credential and reads through the guarded receiver. Recovery then proves membership data is portable while access remains absent. Without the driver, that test reports an explicit skip.

These are source integration checks. Physical iPhone qualification, approved deployment configuration, TLS deployment review and public service abuse controls remain outstanding. The separate installed Devgraph → Wallet → secS → receiver **mutation** gate is unchanged. No live identity, data, grant, trust or production installation is changed by this source delivery.
