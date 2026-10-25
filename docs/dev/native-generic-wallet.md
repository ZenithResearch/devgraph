# Native SDK generic Wallet migration

The additive `devgraph.native-profile.v2` profile opts native clients into generic
terminal approval. The existing browser-host profile remains compatibility-only;
it cannot implicitly enable the new signer. Both load bounded owner-private files
and bind secS storage to the actual OS account home, not an environment override.

```json
{
  "schema": "devgraph.native-profile.v2",
  "origin": "http://127.0.0.1:8080",
  "audience": "devgraph://receiver-local",
  "stable_issuer": "secs://devgraph-work",
  "read_credential_file": "/Users/QUALIFICATION/secure/devgraph-read-credential",
  "secs_executable": "/Users/QUALIFICATION/Library/Application Support/Zenith/secS/bin/secs-devgraph-work-v2",
  "secs_data_root": "/Users/QUALIFICATION/Library/Application Support/Zenith/secS"
}
```

This example is for a disposable VM account, not an installer or live activation.
Do not include `extension_ids`. Native reads continue to load the owner's private
read credential; no Wallet read grant is needed.

Use `InstallationProfile::load`, `NativeClient::new`, then
`execute_with_wallet(request_bytes, idempotency_key, &WalletSignerReference,
reconcile, &CancellationToken, Arc<DispatchState>)`. Supply canonical bytes from
the shared WorkRequest parser. `WalletSignerReference` contains the existing
custody **file reference** and public key, never seed bytes. The fixed Wallet
binary alone reads signing material. Its controlling terminal approval cannot be
replaced by stdin or unattended consent.

Every invocation issues a new credential and obtains fresh approval. Set
`reconcile=true` only for explicit recovery of the same request/key; the method
selects the matching status-only endpoint. Preserve those values after uncertain
dispatch. Missing status receipts remain unknown. Do not call the mutation again
as a recovery mechanism. Returned `NativeResponse` streams are bounded transport
responses; consumers must use the shared core result decoder before treating them
as a committed mutation. Recovery returns a receipt, not a new work snapshot.

The SDK independently verifies the generic presentation, issuer projection,
request/disclosure/resource bindings and current owner policy. Changed profiles,
trust, expired grants and unavailable fixed binaries fail closed. No grant or
trust configuration is created by this API.

Canonical request versions and authority transport versions remain independent.
Work v2 uses `/todo-operations/v2`; existing Work/Arena v1 uses generic authority
on `/work-operations/v2` or `/arena-operations/v2`. Each has a `/status` sibling.
Python terminal users choose `devgraph work <operation> --credential-v2` and add
`--reconcile` for recovery. The old explicit compatibility clients remain until
the separate installed qualification/removal gate is satisfied.

## Membership disclosure and operator consent budgets

Public Wallet configuration adds `membership_requirement` (`optional` or
`required`) independently of the provider-profile membership trust section.
An optional receiver accepts an empty membership proof while still requiring
valid holder signatures and an approved grant. Invalid supplied membership
proofs remain rejected. Older clients/configurations remain readable.

Operator consent envelopes permit up to 131072 canonical UTF-8 bytes; member
request/status envelopes retain their 16384-byte limit. The enclosing operator
HTTP/local RPC request is bounded to 524288 bytes. Every existing identity,
origin, expiry, operation, signature and scope check still applies. Deploy a
matched Wallet Core revision before using larger selections; older signers
continue to reject them. No saved credential or grant format changes.
