# Generic Wallet presentation migration

This composed source candidate combines generic credential authority with the
canonical Todo and workflow contracts. It does not install or activate a live
service. Installed qualification and legacy removal are separate gates.

## Ownership and fixed contracts

Wallet Auth owns `castalia.credential-presentation-request.v2` and
`castalia.credential-presentation.v2`. Wallet owns custody, issuer-pin verification,
caller binding and explicit approval. Devgraph owns Work/Arena canonicalization,
operation names, resource inventories, disclosure, routing and execution. secS
issues a transient `castalia.request-credential.v1` using current installed grants
and rechecks them when accepting a Wallet presentation. This is not a Dregg
capability token, membership grant or reusable bearer credential.

The opaque bytes are `devgraph.credential-request.v2\0` followed by canonical JSON
with exactly `schema`, `request` (the original typed request object), and
`idempotency_key_digest_sha256`. The legacy Work/Arena request digest remains
unchanged. A separate SHA256 binds these new wrapper bytes. Disclosure contains
operation, sorted resources, expected version, idempotency digest and the complete
canonical request in numbered Unicode-safe chunks. Nothing is truncated. Unsafe
display control characters fail before approval.

The closed `secs-devgraph-work-authority.v2` projection replaces the old Wallet
presentation digest field with `credential_presentation_digest_sha256` and adds
`credential_digest_sha256`, `credential_request_digest_sha256` and
`disclosure_digest_sha256`. Its signature/projection domains are distinct from v1.
The credential nonce is its session ID; the presentation nonce remains a full
16-byte hexadecimal nonce. Replay scope is `credential:operation:nonce`.

`SecSWorkV2Verifier` independently reconstructs the exact Work/Arena request,
resources, idempotency binding, wrapper and disclosure. The existing transaction
adapter verifies again after acquiring the mutation lock. Mutation, receipt and
audit remain atomic. The owner-held v2 receiver bundle is reloaded on each check and must match the
current managed v1 policy/key admission pins. Receiver-first revocation therefore
invalidates both versions immediately; a stale v2 activation fails closed.
V1 decoders, signatures, persisted receipts and recovery formats are unchanged.

## Explicit operator configuration, never page-supplied trust

No configuration is created automatically. The existing secS authority bundle
remains authoritative; its new producer requires the separately reviewed
`authority/credential-presentation-v2.json` caller configuration. Devgraph requires
an owner-private `secrets/secs-magik/devgraph.work.v2/` directory under its configured
data root, with:

- `receiver.json`: schema `devgraph-secs-work-receiver.v2`, `schema_version: 2`,
  the existing receiver audience, stable issuer and current `policy_binding`.
- `secs-public-key-registry.json`: the existing verifier-registry format.
- `browser.json`: `{"schema":"devgraph.browser-credential-transport.v2","origins":[...]}`
  containing explicit canonical HTTPS or loopback HTTP origins.

The terminal command additionally requires owner-private Wallet trust at
`CastaliaWallet/trust/credential-presentation-v2.json` below the existing Zenith
application-support root. It reuses the current signing-key reference by passing
`CASTALIA_SIGNING_KEY_FILE` and `CASTALIA_SIGNING_PUBLIC_KEY` only to Wallet.
Neither Devgraph nor secS opens the Wallet seed.

`devgraph work <operation> --credential-v2 --request-file <file>
--idempotency-key-file <file>` (and the equivalent `arena` command) explicitly uses
secS preflight → `castalia-wallet-present-credential-v2` → secS authorization → the
v2 receiver. A controlling interactive terminal is required. Default commands
remain on their previously installed version until replacement qualification;
there is no failure fallback between versions.

After a lost response, repeat the original command with `--credential-v2 --reconcile`
and the exact original request and idempotency-key files. This obtains fresh terminal
approval and current authority, then calls only `/work-operations/v2/status` or
`/arena-operations/v2/status`, or `/todo-operations/v2/status` for Work request v2. It never dispatches a mutation. A matching receipt
returns `committed`; no matching receipt remains `unknown`, and a failed status
lookup remains unavailable. Neither outcome automatically retries the mutation.
`--reconcile` is rejected without `--credential-v2`; legacy commands are unchanged.

## Browser transport and reconciliation

`/credential-work/v2/{capabilities,provider-profile,prepare,execute,status}` is owned by Devgraph.
All are bounded, same-origin POST routes. Unknown or revoked origins, duplicate
origin headers, cross-site requests, bearer headers and malformed bodies fail
before producer invocation. Origin configuration is reread for each request.
The server invokes only the fixed secS binary and never returns admin credentials.

The shared application modules are served from `/assets/credential-v2/`.
`DevgraphCredentialClient` uses only `getCapabilities` and `presentCredential` on
Wallet. Its transport separately prepares, executes and reconciles operations.
Late approval after cancellation/disposal cannot dispatch. Once dispatch may
have occurred, another mutation is blocked until reconciliation. A status call
rechecks current authority and only returns that holder's matching receipt;
absence remains `unknown`, never proof of nonexecution. Expired proofs require
fresh explicit approval using the exact original request/key before status lookup.

The narrow dependent `codex/kanban-generic-wallet` branch is based on preserved
`4febd0d41cf3a1e8c4ce1a376ffb185fe23d22d4`. It removes `provider.devgraph` calls from
the board and checks Devgraph's supported operations before asking Wallet.
Wallet #25 is superseded and remains closed. The composed boundary supports the
existing Work/Arena compatibility operations plus workflows and canonical Todo
operations. `/todo-operations/v2` admits strict generic authority and returns
`devgraph.work-result.v2`; the generic browser transport preserves that result.
Contract v2 is independent of authority transport v2. Current policy still requires
explicit grants for each exact operation and every referenced resource.

## Evidence and remaining gates

`tests/fixtures/credential-v2/authority-vectors.json` contains 23 synthetic vectors
produced by the reviewed secS Rust v2 tests. Python reproduces the request wrapper,
complete disclosure, policy digest and verifies each actual secS signature. The
fixture contains only public synthetic identities and signatures. Existing v1
native Work/Arena vectors continue to verify.

Coverage includes strict version isolation, binding substitution, current trust
reload, lock-delay expiry, atomic receipt retries, same-origin admission, bounded
JSON, server-side origin revocation, generic browser lifecycle and terminal
subprocess orchestration. Native Wallet approval and installed Chrome package
qualification are coordinated separately in the Wallet review.

The production secS binary derives its authority root from the operating-system
user database, not an overridable HOME. An exact production-binary terminal →
secS → Devgraph journey therefore requires an isolated OS user/runner with real
operator-supplied trust configuration. Test-only `run_at` ceremonies and mocked
process composition are not installed-runtime acceptance. No live service or real
wallet was changed; Wallet has since independently retired its application-specific handlers. Devgraph
and secS retirement still requires their complete installed chain to pass. Hosted Buildkite status, installed-runtime acceptance
and production/staging qualification remain separate gates.

## SDK migration and qualification

See the [breaking web SDK preview](../../packages/web/README.md),
[native profile migration](native-generic-wallet.md), and
[disposable macOS qualification gate](generic-wallet-qualification.md).
The additional `progress-authority-vectors.json` contains 48 independently issued
secS workflow/Todo vectors; both Python and native Rust verify them. Historical
vectors remain unchanged. These source checks do not qualify installed artifacts.

Browser provider setup uses explicit runtime profiles in merged Wallet; see the
[current setup and pin compatibility guide](merged-wallet-provider-setup.md).
