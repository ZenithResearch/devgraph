# Native Wallet reads through shared authority

Status: source candidate, not activated on the installed Devgraph receiver.
The old native bearer preview is compatibility-only and is not the target path.

The boundaries are:

1. Wallet proves control of the Member Key and confirms the exact read scope and
   issuer pin. Backup verification and active membership remain prerequisites.
2. The issuer validates membership under separately configured trust and applies
   the operator's Work policy and the saved enrollment ceiling.
3. The shared Rust factory signs `castalia.read-credential.v1`. secS validates
   that credential and a fresh holder presentation for each exact read call.
4. Devgraph applies the verified Work/Arena selectors before pagination. An
   unrestricted Arena selector means all Arenas; an explicit selector includes
   inherited Arena membership. Archived visibility remains independent.

The credential binds holder key, installation, issuer/key, audience, receiver,
policy digest, exact permissions, identifier, issued time and expiry. It is
public but not usable alone. Each `castalia.read-presentation.v1` binds its digest,
the exact operation/resource/parameters, receiver, timestamp and random nonce.
Presentations expire after 60 seconds; credentials last at most one hour. The
receiver checks current membership/credential revocation and policy, then consumes
the nonce transactionally before reading. The factory accepts only permission
records explicitly admitted by policy. Renewal cannot expand an enrollment's
original ceiling; wider access requires a separately reviewed operator action.

## HTTP and compatibility

- `POST /devgraph/native-read/v2/challenges`: holder and installation disclosure.
- `POST /devgraph/native-read/v2/enrollments`: membership and signed approval.
- `POST /devgraph/native-read/v2/requests`: signed `devgraph.work.list` or
  `devgraph.work.get`; list limits are 1–100. POST carries the read proof; it
  exposes no Work mutation handler.

The v2 receiver exposes no bearer fallback. Existing Work/Arena/Todo mutation
bytes, membership v3/v4 signatures, Dregg adapters and legacy response envelopes
are unchanged. This is not a Dregg child-delegation implementation. The old
native-v1 fixture remains available only through its explicitly separate factory.

The native client now requires an independently supplied issuer pin in
`WorkAccess.json` (`authority`: issuer, key_id, public_key, receiver, audience).
A response cannot install its own trust. Rust verifies the issued credential
against that pin and the in-memory approval before staging encrypted custody.
The app reports success only after its existing durable compare-and-swap commit.
Lock/cancel invalidates pending approval. Connection credentials are excluded from
recovery/Castaway exports. Existing wallets load without a migration; a preview
that has stored the new local `read_credentials` field requires a compatible core
until disconnected. Do not downgrade such an installation to the old core.

## Explicit host composition

`devgraph.signed_read_runtime:create_app` is an opt-in Uvicorn factory. It uses
the same canonical Neo4j storage configuration as the production composition,
without reading an owner bearer or running migrations. No installed service is
automatically changed. Bind this process to loopback and route only the v2 prefix
through the operator's existing HTTPS/Tailscale listener.

Set `DEVGRAPH_DATA_ROOT` to the managed data root. Authorized read operations
use the same bounded, private durable audit store as the main API; missing or
unavailable audit storage fails closed.

Set `DEVGRAPH_SIGNED_READ_CONFIG` to an owner-private JSON file with schema
`devgraph.signed-read-runtime.v1` and these absolute paths:

| Field | Operator-controlled input |
|---|---|
| enrollment_policy_file | `devgraph.native-read-policy.v1`, explicit read grants |
| authority_policy_file | `castalia.read-policy.v1`, same grants projected by `SecsReadAuthority.policy_for` |
| membership_trust_file | Approved v3/v4 trust configuration |
| revoked_memberships_file | `devgraph.revoked-memberships.v1`, explicit `membership_ids` array |
| enrollment_database_file | SQLite file in an existing owner-private directory |
| secs_binary | Matched `secs-read-authority-v1` executable |
| secs_binary_sha256 | SHA-256 of that exact executable |
| issuer_key_file | Owner-private Ed25519 issuer seed file, lowercase hex |

Configuration does not create keys, approve trust or expand policy. Missing trust,
revocation registry, binary, policy or a mismatched artifact fails setup. The
public issuer key must reach the app through reviewed configuration, separately
from the enrollment response. secS reads current policy on each invocation; the
receiver provides current trusted registry state, never caller-supplied status.

The first requested profile is all Arenas, unarchived concrete Work types,
list/get only, one hour. This source has not provisioned that profile on the live
host or copied any owner read key into the app.

## Qualification

Run `tests/native_read` with `CASTALIA_RECOVERY_TEST_DRIVER` and
`SECS_READ_AUTHORITY_TEST_BINARY` pointing at the matched candidate binaries.
The source harness creates disposable issuer/member keys and tests actual Rust
approval, issuance, holder signing, secS verification, guarded reads, replay,
wrong holder, scope tampering, expiry, revocation, Arena pagination, recovery
exclusion and denied bearer use. Existing delegated filtering tests cover storage
projection. Actual installed iPhone enrollment and reading remain a separate gate.
