# Existing-owner Arena administration

Owner registration is retained unchanged. A protected local secS RPC command
prepares `devgraph.access-operator-action.v10`, `upgrade_arena_policy`. The
registered owner signs the existing Wallet `access.decide` challenge. The action
binds the resource, original owner-registration digest, exact old/new policies,
selected canonical receiver, affected access points, and a private proposal digest.

Two explicit modes exist: `selected` Arena IDs, and `all_current_and_future`.
The latter is an owner ceiling, represented by `devgraph.access-draft.v7` with
`{"scope":"devgraph.arena.read","all_arenas":true}`. Operator/member policies
use v6 and the root enrollment policy uses v5. These forms are **not** accepted
as recipient drafts or issued read credentials. A member still receives v6
with explicit Arena IDs, then the existing v5 native scope/result and reusable
holder grant. The owner ceiling alone never widens a stored member assignment.

`create_arenas:true` is independently signed and requires the all-Arena mode.
This permits the owner-only local `devgraph.access-operator-action.v11` command
`create_arena`, binding the current policy digest, original action ID, and the
complete canonical `devgraph.arena-request.v1` create request. The fixed secS RPC
verifies its Wallet signature; DevGraph rechecks the active owner and explicit
creation role before and inside its canonical mutation transaction. Creation
uses the regular audit, outbox, receipt and principal-scoped idempotency machinery.
`arena_create_status` checks the original receipt without another mutation.
This is not an ordinary member Work grant or onward delegation.

Upgrade preparation and endorsement are distinct from local application. The
executor compares every input digest before writing, retains original bytes,
and atomically replaces individual files with runtime manifests last. It
restarts only the existing Wallet jobs whose exact plist digests are in the
signed proposal. Private API, database, issuer keys, certificates, original
owner registration and member assignments are preserved. A partial transaction
denies normal admission until original-action recovery completes. The public
route-binding inspection and owner-signed `arena_policy_status` recovery remain
available without starting a partially configured application. Recovery admits
only the exact previously signed targets. A terminal unendorsed status closes
that original action so a late execution cannot apply afterward.

The same canonical provider registry is used through every admitted access
point. Network reachability never changes secS authority. HTTP exposes neither
owner-policy upgrades nor owner Arena creation.
