# Ordinary current-and-future Arena grants

Status: implemented and tested source candidate; deployment is separate.

An ordinary member credential may select the whole Arena collection explicitly:

```json
{"scope":"devgraph.arena.read","all_arenas":true}
```

This selector covers Arena records created before or after issuance. It is
resolved against the canonical graph at each read, excludes archived records
from listing, and does not enumerate IDs at issuance. Work below those Arenas
still needs its own Work read/write rules. Arena creation remains a separately
selected `devgraph.arena.create` operation; reading never grants creation.

## Version and compatibility boundary

- Ordinary member draft: `devgraph.access-draft.v8`.
- Operator read/write actions: `devgraph.access-operator-action.v12` / `.v13`.
- Capability disclosure: `devgraph.access-activation-capabilities.v6`, with
  `future_arena_reads: true`. A client must also verify the operator's ceiling.
- Enrollment policy: `devgraph.native-read-policy.v6`.
- Native read disclosure/result: `devgraph.native-read-scope.v6` /
  `devgraph.native-read-result.v6`.
- Existing registry schema, owner ceiling, signed secS grant/credential envelopes,
  Wallet Core pin and backup formats do not change.

The attached schema describes the new ordinary draft. Old v6 drafts continue to
accept only explicit `arena_ids`; no existing row is expanded, rewritten or
re-signed. Old clients reject the newer disclosure rather than treating it as
an unrestricted legacy scope. Existing owner v7 ceilings remain owner ceilings;
members use v8, not that owner-only format. The registry accepts new nested v8
assignments while old-only registry bytes and digests remain exact.

## Reusable authorization semantics

The existing secS `Scope::All` means all resources within the signed permission's
provider-owned conditions. Here those opaque conditions select Arena metadata
for this canonical service. No network address becomes an authority boundary.
Wallet Core can save/restore that grant and derive an equal same-Wallet
credential. `Scope::Paths` with single `Arena/id` roots narrows it to fixed IDs;
the adapter then removes `all_arenas` and emits those IDs. Opaque constraints,
subject, audience, operation and validity cannot widen. A fixed-ID parent cannot
become dynamic. Onward delegation remains disabled.

The ordinary operator review/approval, current-assignment lookup, revocation,
nonce consumption and exact unknown-outcome recovery paths are retained. No
special enable switch, new self-credential or automatic grant is introduced.

## Qualification

Tests cover ordinary signed operator approval; unchanged old assignment bytes;
real native secS enrollment and reusable credentials; Arena creation after
issuance; pagination beyond the old ID limit; separate list/get enforcement;
no implicit Work/member access; same-holder narrowing; encrypted backup/restore;
replay and revocation. Transfer and iOS share the strict native v6 reader.
