# Historical preview bridge v1

This is retained compatibility documentation. Merged Wallet removed this bridge.
New clients use [generic transport](../dev/kanban-generic-wallet-migration.md).

This implements the approved SDK plan. The sole preview is the top-level document at
`http://127.0.0.1:8080/sdk-preview/`. Origin approval trusts the whole origin. The
native messaging host is `org.devgraph.wallet_bridge`; installation registers the
actual packaged extension ID, never a wildcard. This is a developer custody preview.

## Envelopes and ownership

The page uses `window.castaliaWallet.devgraph(payload)`, which sends the closed
provider method `castalia.wallet.devgraph`. `payload` is
`{v: 1, request_id, action, ...fields}`. The extension uses the unique page
`request_id` as native envelope `id`, including pending calls canceled before their
stream/authorization result arrives. The extension checks setup, unlock, approved origin,
frame zero, sender document ID and current exact preview URL for every action.
Extension-to-native messages are `{v:1,id,action,...fields}`; responses are
`{v:1,id,ok:true,result}` or `{v:1,id,ok:false,error:{code,dispatched}}`.
IDs are unique nonempty ASCII strings, maximum 128 characters. Only fixed safe
error codes cross the bridge; no raw exception, credential, request or projection.

The native port is scoped to one connection. All post-connect commands carry its
`connection_id`. The extension derives `origin` and `document_id` from Chrome sender
metadata, never from page claims. Handles are random opaque port-local strings.
The page cannot send the native-only `authorize` form or obtain its presentation.
The extension retains prepared intent and authorization handles internally.

Top-frame navigation revokes the port whenever document ID or the exact allowed URL
changes, including history-state and fragment events. Returning cannot restore old
authority. Same-URL history updates and unrelated tabs/subframes do not revoke it.
Cleanup cancels only that connection's prompts and resources; stale responses cannot
close a replacement connection. Failed metadata inspection is terminal for the port.

| Action | Page fields | Native fields / result |
| --- | --- | --- |
| `connect` | none | Extension adds `origin`, `document_id`, `wallet_public_key` (lowercase Ed25519 hex64). Result `{connection_id,actor_id,receiver_profile,stable_issuer,audience,origin,capabilities:["read","work.v1"]}`. |
| `request_read_access` | `connection_id` | Extension requires explicit read consent; native returns `{read_context,expires_at}` (at most 900 seconds). |
| `read` | `connection_id,read_context,request` | `request` is a closed read descriptor as defined below. Returns stream header. |
| `authorize` | `connection_id,request_b64,idempotency_key` | Extension prepares with shared Wallet WASM, prompts with immutable summary, rechecks custody/document/profile, signs with fresh nonce/session/time, then sends the same fields plus `presentation_b64` natively. Host verifies bindings, invokes trusted secS, verifies projection. Returns `{authorization_id,expires_at}`; no signed projection leaves native custody. |
| `execute` | `connection_id,authorization_id` | Consumes authorization once, checks expiry/profile/actor again and dispatches guarded POST. Returns stream header. |
| `pull` | `connection_id,stream_id,ack_seq` | Returns the next chunk; first `ack_seq` is `-1`, subsequently exactly the prior consumed sequence. |
| `cancel` | `connection_id,target_id` | Cancels pending control ID, authorization or stream; `{cancelled:true,dispatched}`. Late results cannot restore it. |
| `dispose` | `connection_id` | Revokes all handles, aborts readers, closes port; `{disposed:true}`. |

`request` is one of `{kind:"get_work",work_kind,id}`, `{kind:"list_work",work_kind,
filters,limit,after_id}`, `{kind:"list_relations",subject,relation_kind,limit,
after_resource}`, `{kind:"cypher",request_b64,expected_result}`. Optional fields may
be omitted; core validates exact filters, identifiers, limits and expected Cypher
results against receiver schema. Neither arbitrary URLs, HTTP methods, headers,
issuer programs, credential paths nor receiver configuration are accepted from pages.

## Pull streaming

A stream header is `{stream_id,status,content_type,content_encoding,content_length,
limit,dispatched}`. `content_length` is a decimal string or null; it is advisory.
Only identity content encoding is accepted. `dispatched` is true for an execute
that may have sent bytes, false for reads. A chunk is `{stream_id,seq,total,
chunk_b64,done}`. `total` counts raw bytes, `seq` starts at zero. `done:true` is an
explicit terminal frame and may contain the final bytes. Each raw chunk is at most
49152 bytes; the entire UTF-8 JSON envelope is at most 131072 bytes. Unpadded canonical
base64url is required. One outstanding pull per stream means at most one unacknowledged
chunk, within the plan's maximum of two. A next pull acknowledges downstream
consumption; do not prefetch. Replayed/gapped acknowledgements, cross-stream IDs,
late terminals and pulls after terminal are denied. All relay layers enforce limits.

Four active reads and one active mutation are permitted per connection. Stream
limits are 8 MiB single/mutation, 16 MiB pages, 256 KiB Cypher, 64 KiB error bodies.
No larger override is advertised until measured qualification. Readers are dropped
on cancel/dispose/disconnect/timeout. Prompt timeout is 120 seconds, native issuance
10 seconds, HTTP including consumption 30 seconds. A canceled execute or lost port
after submission is `outcome_unknown` unless non-dispatch is affirmatively proved.
HTTP 503 and invalid/truncated response are always unknown after dispatch. No retry
is automatic. Authorization is renewed only by another explicit confirmation.

Cancellation follows one native mutation lifecycle across authorize control, consumed
authorization, execute control, stream and pull aliases. A successful false dispatch
report excludes subsequent dispatch through any of those aliases. Terminal truth is
retained until port disconnect, bounded to 131072 alias/history entries and 65536
accepted controls; exhaustion revokes the connection before admitting more work.
Generation-checked cleanup prevents late completion from releasing a newer mutation's
slot. Unused authorizations expire at `expires_at <= now`; pending execution and
dispatched streams retain their slot until terminal cleanup.

Generated connection, read-grant, authorization and stream IDs remain reserved until
disconnect, including after their resources finish. Incoming controls cannot reuse
them, and generated handles cannot reuse accepted controls or forward-cancel targets.
Accepted explicit disposal acknowledges `{disposed:true}` before closing its owning
page port; all authority is already revoked when that acknowledgement is delivered.

SDK errors report mutation-level dispatch evidence: `false` proves non-dispatch,
`true` means dispatch happened or may have happened, and `null` means unproven.
Execute headers require `dispatched:true`; a later pull error cannot downgrade it.
The SDK connection is ready, reconnecting, disconnected or disposed. Failed reconnect
permits another explicit same-identity attempt, while explicit disposal is terminal.
Every reconnect invalidates old grants and attempts; prepared bytes and keys survive.

## Native installation configuration

An operator-owned, owner-private profile selects the exact fixed origin, audience,
stable issuer, approved extension IDs, existing read credential file, native secS
executable and existing secS data root. All paths are absolute, checked private
files/directories without symlink following. The host checks its Chrome origin argv
against installed IDs. It cannot initialize Wallet identity, issuer keys, grants or
operator trust. Fixture installation creates its own synthetic keys/grants only.
SecS invocation uses its existing private-file CLI with bounded temporary 0700/0600
input/output paths, no shell interpolation, a timeout and cleanup. Its output is
bound to the prepared request, key, actor, installed policy/key registry and current
time before a handle is issued. Issuer locks are released before HTTP starts.

Native HTTP uses a fixed receiver with no redirects, cookies, proxy environment or
automatic decompression; `Accept-Encoding: identity` and `Cache-Control: no-store`.
Reads alone add the private bearer. Writes alone add idempotency key, canonical
base64url projection and `X-Devgraph-Receiver-Profile` on `/sdk/work-operations/v1`.
The profile is the shared domain-separated hash of fixed origin, audience and stable
issuer. Reconnect requires the same profile and actor and invalidates all old handles.
