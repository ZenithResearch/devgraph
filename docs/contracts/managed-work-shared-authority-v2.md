# Managed agents in shared standing native authority

The owner-local `devgraph auth work` administrator continues to accept its exact
finite single-agent policy and `devgraph.work-grant-plan.v1` bytes. Once a
separately reviewed native installation uses `secs-devgraph-work-policy.v2`,
that bundle can also admit other Wallet holders through explicit standing rules.
This compatibility support does not migrate, admit or activate any holder.

## Status and identity

`auth work status` selects the saved Wallet identity, reconstructs only its
exact dated managed Work/Arena profile, and checks that profile's own validity.
An active standing rule for a different holder cannot keep an expired agent
active. Readiness requires current service-key metadata and matching v1 plus
any existing v2 receiver pins. A nonstandard/standing saved-agent profile fails
closed; it is not silently converted into a finite managed profile.

## Reviewed renewals and key rotation

For shared native policy, planning emits `devgraph.work-grant-plan.v2`. It keeps
the v1 plan fields and adds `preserved_rules_sha256` (the ordered canonical
array of all other identities' native rules) and `verifier_not_after` (the
finite service-key deadline). The new policy remains schema v2. Every other
holder's allow, deny and revoked rules, resources and validity remain unchanged.
The saved agent's exact reviewed profile is replaced with a dated profile.

At apply, current native digest and policy version prevent stale updates. The
other-holder hash is checked against actual current native state, not merely
against the proposed plan. The key deadline must equal the maximum of the
previous finite key deadline and this agent's reviewed new expiry: it cannot
be shortened or extended arbitrarily. The existing service key is retained
unless the explicit plan requests verifier rotation. There is no automatic
renewal, new Wallet identity, scope migration or grant activation.

After provision, both the private v1 receiver binding and an existing v2 binding
are updated to the same current native policy/key registry. During a partial
cutover mismatched bindings deny access. Preserve the original plan and inspect
the native generation and pins before reconciliation; never redispatch provision
with a new plan to hide an uncertain/partial result. This administrator does not
automatically repair a mismatched v2 pin.

## Revocation

`auth work revoke` retains its emergency receiver-first, whole-native-authority
meaning. It removes managed receiver admission first, then marks all native
rules revoked while preserving the current finite verifier-key deadline. This
can stop every admitted Wallet's native Work access. It is distinct from
revoking one member's standing assignment inside Transfer.

## Verification and deployment

Tests cover unchanged legacy plans, another holder appearing first, expired
agents versus active standing holders, preservation of ordered foreign
allow/deny/revoked rules, both receiver pins, exact key deadlines, stale and
tampered plans, rotation, emergency revocation, and interrupted pin replacement
without a second provision dispatch. Producer wire versions and digest domains
remain the already-defined native Work policy v1/v2 contracts.

Source tests do not activate a production policy. Upgrade the installed
administrator and matched native binaries before a separately reviewed
multi-holder production policy cutover. Preserve the existing actor key,
service key and native replay ledger, and record installed compatibility proof.
