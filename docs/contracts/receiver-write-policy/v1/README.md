# Receiver write scopes v1 — source foundation

This is an **owner-private receiver policy**, additional to native secS signature,
operation, caller, expiry, resource and replay checks. It is not a credential or
an application-supplied authorization field. `ScopedSecSWorkAdapter` is an
explicit composition; no production runtime or existing grant is switched to it
by this change. Operator write activation remains unavailable until its registry
and native admission are composed and qualified together.

A policy binds one stable issuer, audience and exact native policy ID, version
and digest. Assignments bind an authenticated secS actor to a finite interval and
explicit operation/resource grants. Each grant may additionally restrict Arenas.
IDs and Arena selectors intersect; grants remain paired with their operation.
Attenuation cannot widen either selector, rebind the actor, extend validity, or
combine independent operation/Arena pairs into a Cartesian product.

The accompanying `schema.json` is generated from the closed Python contract;
`example.json` is a public synthetic policy, not usable authority. Missing,
invalid, expired or wrong-native-policy assignments deny access. Empty
assignments revoke all assigned actors; an empty Arena list is invalid, never
unrestricted. `arena_ids` is omitted for an unrestricted resource grant.

## Transaction boundary

The adapter verifies native authority before consulting scope. After acquiring
the existing serialized Work mutation transaction it verifies authority again,
loads current scope, and checks **every signed resource**. Existing version,
workflow, evidence, parentage, receipt and audit rules remain authoritative.
The trusted policy loader must fail closed and provide current registry state;
its persistence, monotonic revisions, revocation locking and activation are the
next integration boundary, not supplied by this parser.

Work inherits Arena membership through canonical parentage. All six Todo types
are understood; base Todo and Proposal currently have no permitted containment
or direct Arena membership and therefore require an unfiltered explicit resource
grant. Unknown, absent, cyclic or ambiguous membership never matches an Arena.
Dependencies do not confer membership. Traversal uses bounded incoming-edge
witnesses for the referenced parent chains, not a full graph scan.

After mutation, each resource must remain covered by **one of the same grants
that covered its prior state**. A failure rolls back Work, relationships, parent
and Arena versions, evidence, receipt and success audit. Separate grants for
Arena A and B do not themselves authorize moving an item between them; an
explicit grant including both is required. Removing a parent cannot escape an
Arena restriction by making work unassigned. All named endpoints are checked,
including both relationship endpoints and prior/new parents and Arenas.

Creates start parentless, outside any Arena. An Arena-only grant cannot authorize
creation of a new root; the operator must explicitly grant creation for that
resource (and separately permit its later attachment). New Decisions,
ReviewPackets, Handoffs and ExternalLinks likewise need separate resource grants:
no authority is inferred from an associated Task or Project. Review requirements
and guarded completion still apply after authorization.

Idempotent retries require current scope before receipt lookup and cannot apply
another mutation. A resource moved beyond the current scope can deny recovery;
a missing or denied result must remain unknown. No automatic resubmission or
change of request bytes/idempotency key is permitted. The scoped adapter also applies the same guard to status-only recovery and audits
its committed/unknown result. This foundation does not replace existing production
status routes.

## Compatibility and remaining integration

Historical Work/Arena requests, generic Wallet disclosures, secS projections,
credential transport, access-draft v1/v2 and signing domains are unchanged. Old
drafts still reject Arena-bearing writes. The existing native policy compiler
explicitly refuses to flatten an Arena-bearing grant. Shared Rust/WASM request
parsers and Wallet remain unchanged; this receiver-only policy is not given to
those parsers and cannot manufacture authority.

Before activation: bind reviewed write selections to a versioned persistent
assignment registry and exact native admission; enforce monotonic policy and
revocation under the mutation lock; compose this receiver on every admitted
write and status route with no legacy bypass; advertise only those capabilities;
then run matched native/Wallet, disposable persistence and installed tests.
Read grants cannot authorize writes. No live policy, trust, grant or deployment
is changed by the source implementation or synthetic tests.
