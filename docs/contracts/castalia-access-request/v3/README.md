# Standing write assignments and native admission

Runtime `devgraph.signed-read-runtime.v6` explicitly enables this source path.
V1–v5 retain their existing behavior. Loading this source does not install keys,
trust, native grants, or change the live service. Installed qualification remains
required with the exact packaged artifacts.

## Flow and authority

A member requests access using the unchanged Wallet access-request contract. The
operator selects write operations, exact objects or kinds, optional Arena
restrictions, and an expiry. Object and Arena selectors intersect. Review checks
that selection against both operator/member ceilings and an independently
installed native secS policy. Missing native admission fails **before consent**.
The operator then confirms the exact selection in Wallet.

Approval records the standing assignment, decision, result and audit in one
SQLite transaction. It does not issue a credential. Browser preparation checks
the active assignment before asking the existing native issuer for an exact
operation credential. Wallet still confirms each mutation. The receiver checks
current native authority, every resource, current scope, and the canonical Work
completion/version rules before committing a graph transaction. Status recovery
uses original bytes and the original idempotency key and never repeats a mutation.

A standing write assignment is **holder-wide**, across callers already admitted
by the native policy; the requesting installation is provenance, not a device
restriction. Its disclosure and active-grant UI say this explicitly. Read grants
remain installation-bound and are preserved by write activation and revocation.
Mixed read/write activation is unavailable: grant them separately. The API
rejects mixed activation atomically before consent.

## Compatibility and storage

`devgraph.access-draft.v3` adds Arena-aware write grants. Read selections retain
v2. `devgraph.access-operator-policy.v2` selects the v3 ceiling, and write review,
approval and revocation use `devgraph.access-operator-action.v3`, binding the
previous write assignment digest. V1/v2 actions cannot activate v3 semantics.
Read actions remain v2. Result v2 and requester result v3 are reused.

Generic Wallet challenge/signing bytes, Work/Arena requests, secS policy digest
and projection/signature domains are unchanged. Published v1/v2 schemas and
historical vectors remain retained. The compatible Wallet Rust/WASM fixture
signs these application-owned actions without any new custody capability.

The existing owner-private inbox gains write assignments and native/operator
policy high-water tables. It starts empty; existing native grants do not become
operator assignments. An observed native/operator rollback or same-version
changed digest fails closed. Narrowing permanently disables incompatible
assignments. Widening later cannot revive them. Original decisions remain.
Admission holds the shared SQLite read lock through native preparation or graph
commit; a revocation cannot slip between checking scope and using it.

## Operator setup boundary

Runtime v6 requires `write_native_policy_file`, an absolute owner-private copy of
the already installed `secs-devgraph-work-policy.v1`. Its digest must match the
receiver's managed native policy binding. Native policy files allow 256 KiB;
other private configuration remains limited to 64 KiB. No configuration can be
supplied through the operator HTTP API. An owner must provision compatible
native admission separately, with explicit approval for the exact holder and
ceiling. This API cannot expand or install it.

For a v5 upgrade, retain the existing inbox and enrollment databases, and
increase the operator-policy revision when changing its schema or ceiling.
A same-revision changed digest is a rollback error, not an automatic migration.
Existing read assignments remain valid subject to their unchanged policy rules;
write tables start empty and require new explicit operator approval.

V6 composes only guarded generic native authority routes; retained v1 authority
is disabled in that composition. **Do not expose another legacy or unguarded
receiver using the same native ceiling**: it would bypass the standing overlay.
The deployment gate must check the complete ingress topology and all pins.

## Verification boundary

Source tests cover Wallet WASM operator consent, durable activation/restart,
revocation, read preservation, stale/mixed decisions, rollback, native ceiling
rejection, scope enforcement, idempotent receipts, missing receipts and lock
ordering. Native projections in write tests are cryptographically signed fixture
projections; they do not qualify an installed native issuer. Synthetic monitor
checks cover write review/activation/revocation, four themes, narrow screens and
lost-response recovery. Full installed browser and terminal/native tests, actual
native credential issuance, member Web/iOS adapters, and live deployment remain
separate gates. No sync or holder-created delegation is added.
