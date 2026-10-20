# Standing grant activation — runtime v8 source candidate

Status: source candidate, not installed qualification. Existing runtime v1..v7
and finite draft/policy/action decoders keep their contracts and signed bytes.
No grant, issuer trust, signing key, receiver pin or network listener is changed
by accepting this source.

## Version boundaries

| Surface | New explicit schema |
| --- | --- |
| Application composition | `devgraph.signed-read-runtime.v8` |
| Grant draft | `devgraph.access-draft.v5` |
| Member registry policy | `devgraph.member-access-policy.v4` |
| Operator ceiling/catalog | `devgraph.access-operator-policy.v4` |
| Operator read / write+mixed action | `devgraph.access-operator-action.v6` / `.v7` |
| Native ceiling | `secs-devgraph-work-policy.v2` (version 2) |
| Receiver scoped assignments | `devgraph.receiver-write-policy.v3` |
| Activation capability | `devgraph.access-activation-capabilities.v4` |

Drafts and native/receiver rules use closed `castalia.grant-validity.v1` values.
`dated` has explicit start/end; `until_revoked` has a start and no end field.
Old finite fields cannot be mixed into the new wire. Existing finite assignment
records remain readable in the new registry without changing their bytes.
Replacement is an exact operator decision with previous digests and monotonic
policy revisions; this is not a migration that widens expired grants.

## Authority and repeated use

The member signs an access request, which grants nothing. The operator reviews
preset, operations, holder/installation, resource paths and validity; the final
signed decision atomically stores the assignment, audit and result. Mixed
read/write decisions activate both parts or neither. Persist/reload, exact
status recovery, current policy, narrowing and revocation remain enforced.

Reads remain installation-bound; writes remain holder-wide across separately
admitted callers. A finite native ceiling cannot admit an until-revoked write
assignment. secS policy v2 and its matching receiver digest are required for
that path. The native verifier key has a separately reviewed finite deadline;
its expiry closes issuance even when the member grant remains until-revoked.
The key deadline is not the standing grant lifetime.

Read credentials still last 60–3600 seconds (or less at the dated grant bound).
The same active assignment can redeem another after credential expiry without
another operator decision. Current scope/digest and revocation are checked on
use, so old credentials cannot retain removed rights. Wallet unlock/consent,
issuer trust and the two-minute approval challenge are separate from validity.
No automatic Wallet unlock, keepalive or consent bypass is included.

## Resource-specific membership

Runtime v8 requires explicit `membership_requirement: "required" | "optional"`.
Optional means a Wallet may provide exactly an empty membership proof. It must
still sign the exact server/installation-bound request and possess the holder
key. Any nonempty membership proof must validate with real issuer and revocation
checks. Required mode retains community membership enforcement. Missing trust
or revocation files fail setup; they are not treated as empty registries.

Native clients opt into `castalia.graph-access.v2`; historical membership-gated
native methods keep their behavior. The protocol marker is client metadata and
must not be forwarded into the server's closed identity request. Membership
setup remains a separate Wallet feature, not a prerequisite invented by the
monitor.

## Browser and scope semantics

The operator must choose a finite date or Until revoked explicitly, within the
current ceiling. There is no implicit expiry or infinity timestamp. Exact
summary and current policy show the selected mode. Unknown decisions retain
original action bytes and recover status only; no second approval is dispatched
automatically. New action versions bind the exact read, write or mixed assignment
digest. Old action results cannot establish a new unbounded assignment.

Paths are evaluated against canonical membership at access time: current/future
descendants enter scope and moved-out objects leave it. Every related endpoint
and operation/resource correlation remains checked. Preset intent is not
authority. Creation of unattached objects is not advertised under path grants;
atomic in-scope creation and associated evidence remain separate open work.

## Qualification

Source tests cover old closed decoders, mixed activation and revocation,
persistence/reload, short credential re-redemption under one assignment,
optional membership with real holder proof, matching Python/Rust native policy
digest, native CLI exact credential approval and replay, and Swift/native ABI.
Mocks/composition tests are identified as such; real secS read tests use the
matched standalone binary. Browser UI, two distinct Wallets and physical iPad
still require installed final-artifact qualification. No sync, delegation,
owner bootstrap or production deployment is claimed.

The [private LAN v8 listener](../runbooks/lan-access.md) is a separate factory
with a closed route/method boundary. Existing LAN read-only/public factories
stay unchanged. Native admission, listener activation and exact member decisions
remain distinct reviewed actions.
