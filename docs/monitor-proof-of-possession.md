# Exact monitor proof-of-possession contract

`devgraph.monitor.view.read.v1` is a receiver-local authentication contract for
exactly `GET /monitor/snapshot`. It authorizes no generic read scope, Work
route, initiative-observation route, export, mutation, or caller-selected
operation. The response remains the existing safe monitor snapshot, including
the derived `devgraph.work-progress.v0` projection.

This contract is operational API security, not new graph vocabulary or stored
state. Immutable ontology releases `v0.1.0` through `v0.4.0` remain
byte-for-byte unchanged. Publishing this receiver-local opcode through ontology
discovery later would require a deliberate new ontology release.

## Fixed transport

The exact request is:

```text
GET /monitor/snapshot HTTP/1.1
Host: 127.0.0.1:8080
SecS-Devgraph-Monitor-Origin: http://127.0.0.1:8080
SecS-Devgraph-Monitor-Session: <unpadded-base64url-canonical-JSON>
SecS-Devgraph-Monitor-Proof: <unpadded-base64url-canonical-JSON>
```

The method is `GET`, the literal path/query is `/monitor/snapshot`, the query
is empty, `Host` is exactly `127.0.0.1:8080`, and the body is empty. Any
`Content-Length` or `Transfer-Encoding` header denies, and the receiver still
streams the ASGI body only far enough to prove that no non-empty chunk exists.
`Authorization`, a missing exact header, a
mixed bearer-plus-PoP request, another origin, or any query denies with a safe
401. Devgraph counts the raw ASGI header list before FastAPI normalization:
`Host`, each exact header, and `Authorization` must be singletons; duplicate
values deny before verifier dispatch. The origin header is browser-settable,
and its
value is accepted only
when the secS session and page-key proof independently bind the same exact
value. It is not trusted as an unsigned transport claim. Devgraph adds no CORS
policy or origin wildcard.

After the async route has proved the request body empty, both this exact PoP
snapshot and the legacy bearer snapshot execute their synchronous graph read in
the application threadpool. A slow snapshot therefore does not block `/live`
on the event loop.

## Canonical JSON and binary encoding

Both header values decode to strict UTF-8 JSON objects. JSON must have no BOM,
duplicate field, floating-point value, non-finite constant, integer outside
the JavaScript-safe range, excessive nesting, missing field, or unknown field.
The decoded bytes must equal UTF-8 JSON encoded with lexicographically sorted
keys, `,` and `:` separators, and no insignificant whitespace. The enclosing
base64url is canonical, unpadded, and round-trips byte-for-byte.

Ed25519 public keys are 32 bytes encoded as 43 base64url characters. Signatures
are 64 bytes encoded as 86 base64url characters. Session IDs are 16 bytes / 22
characters; nonces are 12 bytes / 16 characters. Digests are lowercase
64-character SHA-256 hex.

## secS-signed session

The session schema is `secs-devgraph-monitor-session.v1`, version `1`, with
exactly these fields:

```text
actor_id
actor_signature_suite                 = Ed25519
audience                              = devgraph://receiver-local
expires_at                            integer Unix seconds
issued_at                             integer Unix seconds
nonce                                 12-byte base64url
operation                             = devgraph.monitor.view.read.v1
origin                                = http://127.0.0.1:8080
page_public_key_base64url             strict Ed25519 public key
receiver_policy_digest_sha256
receiver_policy_id
receiver_policy_version
schema                                = secs-devgraph-monitor-session.v1
schema_version                        = 1
secs_context_id
secs_verifier_key_id
secs_verifier_signature
secs_verifier_signature_suite         = Ed25519
session_id                            16-byte base64url
wallet_presentation_digest_sha256
```

The secS signature is computed after removing `secs_verifier_signature`:

```text
"secs-devgraph-monitor-session.v1/signature\0" || canonical_unsigned_session
```

The receiver admits only an active production Ed25519 key from its fixed public
registry and the exact policy binding from its fixed manifest. A session must
be current and last at most 300 seconds. Restarting Devgraph does not invalidate
an otherwise current signed session; it remains usable until its signed expiry.
Replay safety is attached to each independently signed request proof instead
of inventing a receiver-only session field that a producer could not bind.

The session digest used by the request proof is:

```text
SHA-256("secs-devgraph-monitor-session.v1/session\0" || canonical_signed_session)
```

## Page-key request proof

The proof schema is `devgraph-monitor-request-proof.v1`, version `1`, with
exactly these fields:

```text
body_digest_sha256                    = SHA-256(empty bytes)
method                                = GET
nonce                                 12-byte base64url
operation                             = devgraph.monitor.view.read.v1
origin                                = http://127.0.0.1:8080
path_query                            = /monitor/snapshot
schema                                = devgraph-monitor-request-proof.v1
schema_version                        = 1
session_digest_sha256
session_id
signature
signature_suite                       = Ed25519
timestamp                             integer Unix seconds
```

The ephemeral page private key signs after removing `signature`:

```text
"devgraph.monitor.view.read.v1/request-proof\0" || canonical_unsigned_proof
```

The proof timestamp must be inside the session lifetime and within 30 seconds
of receiver time. Session ID, signed-session digest, origin, method, exact raw
path/query, and empty-body digest must all match. The page public key must be
the one bound into the secS-signed session.

Only after the secS key/signature, session policy/currentness, transport
binding, and page-key signature all verify does the receiver enter the stable
replay lock and attempt `(session_digest_sha256, nonce)`. The atomic accepted
claim persists both that unique proof and the new receiver-time high-water
mark before snapshot construction. Claims survive API process restart, are
expiry-pruned, and are bounded to 4,096 entries. A duplicate, full store, or
time below the durable high-water mark denies without rewriting replay state.
All earlier authority denials also leave the durable bytes unchanged. No raw session,
proof, signature, page key, or private key is logged or persisted; the replay
file contains only proof claim digests, nonces, expiries, and the clock
high-water mark. A successful read adds one redacted in-memory audit record
only after snapshot construction; denials add no audit, graph mutation,
receipt, or outbox edge.

## Fixed receiver bundle

The production composition looks only at:

```text
<data-root>/secrets/secs-magik/devgraph.monitor.view.read.v1/
  receiver.json
  secs-public-key-registry.json
  replay/
    claims.json
    claims.lock
```

Despite the shared `secrets/secs-magik` receiver convention, these two files
contain public policy-binding and verification material only. Files must be
regular, receiver-owned, and not group/world-writable. `receiver.json` is a
strict `devgraph-secs-monitor-view-read-receiver.v1` object containing the
fixed schema/version, operation, audience, origin, stable issuer, and one
`{policy_id, policy_version, policy_digest_sha256}` binding. A missing bundle
leaves the exact path unavailable; a partial, unsafe, or malformed bundle fails
runtime construction closed. Below the configured data root, every existing
directory through `secrets/`, `secrets/secs-magik/`, and the operation directory
must be a non-symlink, effective-user-owned, non-group/world-writable directory.
The only root-ownership exception is an ownership-enabled macOS native mount
root that is exactly the configured data root, `root:wheel`, not world-writable,
free of extended ACLs, and used by a service whose effective UID, effective
GID, and supplementary groups carry no UID/GID `0` authority. Group lookup
failure denies startup. The exception never applies to descendants.

The `replay/` directory, permanent `claims.lock`, and replaceable `claims.json`
are created only at this exact
operation path with modes `0700` and `0600`; symlinks, hard-linked files,
broader permissions, malformed/non-canonical state, or an unavailable durable
write fail closed. The receiver locks the permanent no-follow lock inode,
strictly reads the current claims only after locking, writes the next canonical
state to a create-new same-directory owner-private temporary file, fsyncs it,
atomically replaces `claims.json`, then fsyncs the parent before releasing the
lock. An interruption therefore leaves either the complete old or complete new
state; replacement never splits the lock domain. Every macOS trust-path
directory and open file descriptor is rejected if it carries any extended ACL.
On macOS, both this monitor receiver and the exact
Issue-create receiver additionally reject a data-root filesystem mounted with
ownership disabled (`noowners`), because apparent UID/mode values are not an
integrity boundary in that state.

## Deliberate next slice

This repository does not mint the session or page key. The current frontend is
also not migrated by this receiver slice: it still contains the local-dev
bearer field, stores that synthetic bearer in `sessionStorage`, and separately
calls `/initiative-observations`. A later coordinated secS/Wallet/frontend
producer slice must keep the page private key in JavaScript memory only, obtain
the exact session through an explicit Wallet approval, sign every refresh, use
only the three exact headers above, consume the snapshot as the graph/progress
source, and remove bearer/sessionStorage fallback from the production monitor
flow. This receiver alone is not evidence that the persisted graph is visibly
available in the browser.
