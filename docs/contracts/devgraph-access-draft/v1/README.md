# Issuer-controlled member access

An issuer starts with an **approved permission ceiling**, narrows an unsigned
selection repeatedly, and issues only the final selection through existing secS
contracts. The ceiling is configuration, never a globally privileged credential
created for editing. Membership establishes eligibility; it does not grant access.

Source belongs to public `ZenithResearch/devgraph`:

- `src/devgraph/auth/access_policy.py`: strict grant vocabulary, subset checks,
  draft digests and existing-policy projections.
- `src/devgraph/ops/access_plans.py`: offline operator CLI. No network, custody,
  signing, policy installation or trust changes.
- `src/devgraph/auth/member_read.py`: approved member/device assignment selection,
  live revocation and narrowing checks, shared secS issuer/verifier integration.
- `src/devgraph/signed_read_runtime.py`: explicitly opt-in runtime v2 composition.

Wallet and secS keep their existing shared read and generic request contracts.
Wallet remains application-neutral. Devgraph owns the interpretation of Work and
Arena selectors and enforces reads in storage before pagination. Signed mutations
still pass the receiver's current-version, resource, workflow, evidence, approval,
idempotency and atomic-receipt checks. An exported grant cannot satisfy those gates.

## Scope and narrowing

`devgraph.access-draft.v1` binds an issuer, HTTPS receiver, the existing named-write
audience, an absolute validity interval, maximum read-credential lifetime, optional
member/device bindings, and separate read/write grant arrays. Unknown fields fail.
Omitted arrays mean no permissions. At least one permission is required; revoke by
removing an approved assignment rather than representing an empty grant as global.

Each `attenuate` step adds the previous draft's SHA-256 digest and rejects:

- adding operations, item types, items, Arenas or archive visibility;
- changing a bound member, device, issuer, receiver or audience;
- extending the validity interval or credential lifetime;
- combining distinct grants into a broader operation/resource cross product;
- unknown operations, arbitrary policy constraints or child-delegation authority.

Read selectors are intersected: an item restriction **and** an Arena restriction
must both hold. Omitted selectors mean unrestricted; explicitly empty selectors
are rejected. Existing inherited Arena membership rules apply. No project subtree
selector is invented. Each child read grant must fit one parent grant; the checker
conservatively rejects rearrangements requiring more complex union reasoning.

Write grants name exact canonical operations and resources. `{ "kind": "Task" }`
permits that whole resource kind; adding `"ids": ["one"]` restricts it to
`Task/one`. Empty IDs are rejected. All resources required by a multi-resource
mutation need permission, including its evidence or parent records.

The current write policy decoder fixes its audience to `devgraph://receiver-local`.
It does **not** implement Arena predicates or installation restrictions. These
restrictions fail validation/export rather than being discarded. Read grants can
be Arena-scoped today. Writes can be limited to exact item resources or kinds.
Read and write credentials remain separate: from a member draft, make a read-only
device-bound branch and a write-only holder-bound branch.

The historical read projection supports Proposal, Initiative, Project, Issue and
Task, with list/get. Base Todo reads need a deliberate future shared read-contract
extension. Canonical writes include base Todo, progress, workflows and supporting
records. This release does not silently widen a historical decoder.

## Operator flow

Store private JSON files in an owner-private directory (0700; files 0600). Start
with an explicitly reviewed ceiling; there is no automatic all-access default.
`example-ceiling.json` and `example-selection.json` are fictional configuration
examples, not credentials or usable authority. Adjust the ceiling and validity
before using them. Keep real member/server settings outside the repository.

```sh
devgraph auth access inspect --draft-file /private/operator/ceiling.json
devgraph auth access attenuate \
  --parent-file /private/operator/ceiling.json \
  --selection-file /private/operator/selection.json \
  --output-file /private/operator/member-step-1.json
```

Repeat `attenuate` with the previous output as parent and a full narrower
selection as input. An existing `previous_digest` must match, or it may be omitted
and the tool binds it. The output file must be new: these commands never replace
a reviewed file. The CLI returns `authority_changed: false` and a reviewable draft.

```sh
devgraph auth access registry --revision 2 \
  --ceiling-file /private/operator/ceiling.json \
  --assignment-file /private/operator/alex-phone.json \
  --assignment-file /private/operator/jess-phone.json \
  --output-file /private/operator/member-policy-next.json

devgraph auth access export \
  --ceiling-file /private/operator/ceiling.json \
  --draft-file /private/operator/jess-write.json --format write \
  --policy-id devgraph-member-work --policy-version 2 \
  --output-file /private/operator/write-plan.json
```

Read export uses `--format read`. Exports are **review plans**, not installable
policies: they retain the draft's member/device/deadline/server bindings alongside
the projected policy and ceiling digest. Do not extract a read projection and
activate it as global runtime-v1 policy: that would omit those extra restrictions.
A write plan describes selected rules, not a patch to a multi-member installation.
A separately reviewed administration change must preserve other members, deny
rules, policy pins and replay state, and advance the expected policy version.
Renewal or migration must not add permissions. Explicit expansion is a new
operator approval, never an attenuation step.

## Opt-in member read runtime

Use the existing signed-read configuration paths and checksum-pinned native binary,
but select `"schema": "devgraph.signed-read-runtime.v2"` and add the absolute
`member_policy_file` path. V1 rejects that field; v2 requires it. The member policy
contains a ceiling and approved assignments, each with an exact Member Key and
installation ID. Duplicate bindings, widening, and mismatched root read policy
are rejected. The existing enrollment and secS policy files must project the same
ceiling. A missing, malformed or untrusted configuration fails closed.

The receiver reloads the owner-private assignment on every challenge, enrollment
and read. The phone still sends only its public member/device identifiers; callers
cannot submit permissions, install trust, or choose an issuer policy. The normal
membership and holder-signature checks remain mandatory. Credentials expire at the
earlier of their lifetime or assignment deadline. Missing/revoked assignments and
changed assignment digests deny older credentials. Increase the registry revision
for every edit. The database retains the highest revision and per-member grant
generations: rolling a file back or re-adding the same assignment cannot revive
old credentials or pending challenges, including after a process restart. Other
members retain access when their assignments are unchanged. The revision is
rechecked inside the enrollment/read transaction to reject a concurrently
superseded snapshot. A narrowed assignment requires
a fresh, human-approved enrollment; renewal cannot expand the original enrollment
ceiling. Unknown write outcomes still require original-request status recovery.

Runtime v2 rejects previously issued global-v1 credentials because they lack the
registered assignment revision; it does not upgrade their authority. Existing v1
configurations retain their original behavior until an operator deliberately opts
in. Only additive policy-revision, grant-generation and challenge-generation
tables are created. No migration rewrites historic credentials, request bytes or receipts.

SecS receives a temporary owner-private policy projected from the approved
selection. Devgraph validates its semantics against the separately configured
root first. The temporary file contains public permission metadata only, and is
removed after invocation. Issuer keys never leave secS's existing custody path.
Generic Rust verification still compares exact approved permission records.

## Future delegation and current qualification

The subset function can be reused when a future child-credential protocol is
specified. Draft ancestry is **not** signed delegation, not transferable authority,
and not proof that a holder may issue credentials. Parent signatures, revocation
chains, depth/budget limits and holder delegation rights remain separate work.

Tests exercise repeated narrowing, incompatible projections, two real member
signatures, filtering before pagination, expiry, membership/replay safeguards,
revocation and compatibility with the pinned secS executable. This is source
integration with disposable keys. Installed iOS/Desktop UX, member administration
UI, dynamic Arena-scoped writes and production activation are not qualified here.
The earlier installed single-create smoke test is not evidence for this new feature.
