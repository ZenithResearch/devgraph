# Standing grant revision: implementation boundary

Status: implementation plan; no new action is accepted or activated by this document.

## Current behavior

The standing operator contract advertises read action v6 and write/mixed action
v7. Review/approval requires an undecided member request at version 1; activation
records its version 2 decision. Revocation binds that exact active decision,
read/write assignment digests and recipient. Replacements already work when a
member submits a fresh request. The active-grants UI currently offers revocation,
not an operator edit flow. Changing those validators in place would let old
signed actions acquire new semantics, so a UI-only edit button is insufficient.

The next slice is operator-initiated revision of an existing standing grant,
without requiring the member to submit another request. Membership and transport
notifications do not provide this authority. Native admission remains an
independent maximum; this flow cannot provision a signer or widen admission.

## Reviewable product flow

1. Inspect an active grant: member, read installation, holder-wide write binding,
   exact operations, selected paths, validity, current revision and policy ceiling.
2. Choose Change permissions. Prefill the existing grant, with separate operation
   presets and a multi-select resource tree. The last selected container includes
   current/future descendants at access time; a moved-out object loses that path.
3. Show the current and proposed grant together. Classify added authority,
   removed authority, device/binding changes and any duration extension. A
   narrowing must be a subset of the existing grant as well as the current
   operator ceiling. Broadening is a separately explicit operator decision.
4. Review in Wallet, then confirm the exact revision in Wallet. The confirmation
   must name grant revision, old/new assignment digests, holder/installation,
   validity and current policy/native admission. A preset name is not sufficient.
5. Commit the assignment revision, both read/write halves if selected, decision,
   durable result and audit atomically. Show which credentials must be redeemed
   again. No new member request and no automatic grant renewal are needed.

Editing a mixed grant must never silently discard or preserve the other half.
The UI must explicitly choose both halves or describe a write-only/read-only
revision that preserves the other current half. Creation, completion evidence,
access administration and unsupported operations cannot be inferred from a
Project Manager label. Their independent receiver contracts remain required.

## Versioned contract and persistence

Introduce and advertise a new, closed action version for grant maintenance;
do not reinterpret historical v1-v7 review/approve/revoke actions. The final
schema number and names are assigned with the producer/consumer change, not by
this plan. Capability absence leaves the current UI read-only except for the
existing supported revocation flow.

The new signed operation binds: unique action ID, stable target/recipient,
current grant revision, exact prior read/write assignment digests, proposed
selection and validity, review ID, current policy revision/digest and reason.
Reject missing/null prior components when they are required by the selected mode.
Unknown fields, changed same-version policy, stale grant revisions, a different
reviewer or changed selection must fail closed before activation.

Keep the original member request and all historical decisions immutable. A
separate monotonically increasing grant revision must not reuse the original
request's one-shot version 1/2 lifecycle. Migrate existing assignments by an
explicit reviewed rule; reading old state alone never grants new authority.
Persist revision/audit/results with the assignment transaction. Status recovery
uses the exact original action ID and returns the original result without
reapplying it. Existing revocation tombstones and high-water marks survive
restart, upgrade and rollback attempts. An old revoke cannot delete a newer
assignment and an old approval cannot resurrect a revoked one.

Current policy must be checked again under the lock used for receiver admission
and mutation. Previously admitted transactions may finish according to the
existing lock boundary; a later operation cannot use an earlier scope after the
revision commits. A removed read credential must be denied at the receiver, not
merely removed from the Wallet's local cache. Current credentials whose scope
no longer matches cannot be silently broadened; the Wallet redeems fresh access
under the remaining grant with its normal consent.

## Consumers and required evidence

Update together: Python action/selection parsing and persistent grant registry;
operator API capability/result schemas and examples; browser operator client,
preset/tree editor, exact summary and recovery; generic Wallet verified
challenge handling where required; any native/Swift administrative consumers.
Retain old schemas, stored requests and old positive/negative vectors. Future
harnesses consume this same contract instead of editing authority files.

Required tests:

- Exact current-grant prefill and explicit global/multi-path selection. Advanced
  operation edits survive tree navigation. Cancel submits no decision.
- Read-only, write-only and atomic mixed changes; every half-failure rolls back
  decision, assignment, result and audit together.
- Wrong recipient/device/revision/digest/policy/reviewer/selection, expired
  approval, unavailable native admission and legacy-action semantic escalation.
- Retain an actually issued read credential and signed pending write projection
  before narrowing; prove receiver denial afterward while a narrower fresh
  credential succeeds. Distinguish local lock/expiry from receiver rejection.
- Subtree moves/future descendants, correlated rules and no Cartesian-product
  expansion when multiple roots/operations are selected.
- Concurrent revision/revoke, stale revoke after replacement, restart and policy
  rollback, missing receipt and lost response with exactly one revision/audit.
- Source-to-source native/WASM/Python vectors and browser walkthroughs, followed
  by installed Wallet/iPad qualification at the final candidate hashes.

## Adjacent findings

The Work SDK's original-status proof currently displays the original mutation
wording. A verified recovery purpose must be represented by a compatible signed
contract before Wallet can truthfully label it status-only; an unsigned UI hint
must not redefine a mutation proof. Keep this separate from grant maintenance.

A committed Work response currently serializes an unknown parent as null while
scoped readback retains the parent. Resolve response completeness and authorized
relationship disclosure before changing serialization; do not fetch and expose
an out-of-scope parent merely to populate the field.
