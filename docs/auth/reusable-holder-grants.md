# Reusable holder grants: opt-in read integration

Status: implemented source and disposable contract tests; no installed/deployed
release qualification yet. Existing credentials and write execution retain their
historical contracts. This adapter does not turn a read grant into write authority.

The provider signs `secs.holder-grant.v1` for a canonical service and one Wallet.
Wallet Core verifies, reviews and saves the root encrypted. Chrome, iOS and Transfer
use the same Rust implementation to produce finite `secs.holder-credential.v1`
credentials with equal or narrower permissions for that Wallet. A fresh
`secs.holder-presentation.v1` binds each request and nonce. Onward delegation is
rejected. Transport origins and TLS consent are independent from grant authority.

Enablement requires the explicit holder authority binary, its SHA-256 and
`canonical_service_id`. Nothing enables this flow by default. Public configuration
advertises the fixed `/monitor/wallet-access/v1/grants` route only when enabled.
The opt-in browser/native client retains its original signed enrollment request
before dispatch, reviews the returned root separately, and saves only after consent.
An uncertain issuance recovers the exact committed original; it never remints.

## Canonical state and access points

The primary runtime's existing owner-private enrollment database owns new
`holder_roots` and `holder_replay` tables. An explicitly published access point
loads its original provider runtime and must match its service and binary pin.
New root status, assignment generation and replay are checked against that provider,
not the receiving endpoint's legacy enrollment registry. A saved grant works through
another admitted access point without a copied grant or another operator decision.

Initial enrollment challenges remain origin-bound. Issuance on an additional
endpoint requires its reviewed assignment to equal the canonical provider's
assignment after removing the transport receiver. A missing canonical assignment
fails closed; this code does not copy legacy per-origin grants or merge pending
request inboxes. Consolidating those existing assignments/inboxes is an explicit
runtime migration prerequisite, not a side effect of enabling a capability flag.

Root retention and challenge consumption commit in one SQLite transaction. A
secondary endpoint attaches the canonical provider database, using the existing
DELETE journal and FULL synchronous settings. Failed retention rolls both back.
Exact concurrent retries return the single retained root. Durable replay is shared
across access points and restarts. Current issuer/key, membership eligibility,
assignment scope, expiration and retained status are rechecked on use. Operator
activation action IDs prevent an identical later regrant from reviving an old root.
Legacy file-policy changes retain the existing observed-generation limitation and
must not be used to remove/re-add authority offline without a recorded transition.

The permission list retains correlated condition records. Path narrowing uses
signed path prefixes; opaque conditions stay exact. Receiver projection still
checks actual current graph ancestry before exposing objects or relationships.
Changing the URL cannot widen a scope, evade a revoked root or reuse a nonce.

## Transport budgets

Root claims reserve room for an equal-scope child:61,440 canonical bytes maximum, with nesting reserved for the full chain. Complete presentation envelopes fit130,000 bytes; the native command and opt-in read HTTP body are bounded at131,072 bytes. Legacy enrollment and non-opt-in read parsers keep their64KiB limit. The loopback Wallet interface exposes fixed public negotiation metadata as well as its existing protected read routes; it exposes no browser administration UI.

## Release gates

### Finite browser session candidate

The optional `holder_session_exchange_v1` provider capability and matching server
capability enable a separate browser session for a saved v2 grant. The Wallet
reviews the exact read scope and absolute expiry once. It sends the reusable grant
only to its approved issuer; the page receives the finite issuer credential and
keeps its own nonextractable proof key in memory. Reads then work while the Wallet
is locked, until the original deadline. Navigation within the document preserves
that deadline; reload, page departure, expiry or a rejected credential closes
local custody. There is no silent renewal or persistent browser credential store.

An uncertain exchange retains the original session key and request identifiers for
proof-authenticated status recovery. Status cannot create a second credential or
extend the deadline. A known unsupported v1 parent can retain its earlier path
before session consent; a failed new-session exchange never falls back to it.

This source candidate is not enabled in the installed Wallet or server. Its tests
include committed synthetic secS vectors, browser crypto and public/Tailnet UI
fixtures. These do not qualify a real installed Wallet-to-issuer exchange. Matching
receiver, extension and native client integration must pass before activation.

- Preserve issuer keys, policies, grants, request bytes and existing replay records.
- Review the canonical provider selection and any legacy assignment migration.
- Deploy matching verifier and clients with explicit rollback evidence.
- Verify real Tailscale save, lock/reopen, equal/narrower derivation, cross-endpoint
  reuse, expiry, denial outside scope, revocation and recovery.
- Qualify iPhone/iPad and Transfer UI in place; retain Wallets and settings.
- Do not label process isolation or generic write delegation complete.
