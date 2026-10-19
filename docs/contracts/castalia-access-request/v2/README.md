# Approval → standing read grant → credential

This source candidate finishes **read-grant activation**, not general write
provisioning. It requires explicit `devgraph.signed-read-runtime.v5`. Existing
runtime v1–v4 installations retain their behavior. No installation, policy file,
issuer key, trust store or native secS administration is changed by loading this
source. Deployment and exact-artifact qualification remain separate.

## User flow

1. The member requests access using Wallet consent.
2. The operator loads the inbox, selects read scope and expiry, and reviews it.
3. The operator approves the exact selection in Wallet. Devgraph atomically
   records the decision, active assignment, revision, receipt and audit.
4. The member checks access, reviews the issuer's read challenge in Wallet, and
   obtains a holder-bound credential from the existing native secS issuer.
5. The same credential serves repeated reads with fresh holder presentations.
   The operator is not involved in those reads. A replacement credential needs
   the existing explicit Wallet enrollment flow; there is no automatic renewal.
6. An operator may revoke the exact grant with a reason. Further credential use
   and new enrollment are denied. Expiry, policy narrowing and membership
   revocation also retain their existing enforcement.

`active` means that a standing read assignment is active. It does **not** mean a
credential has already been issued or delivered. Operator results keep
`authority_issued: false`; the member's enrollment endpoint alone returns the
actual credential. Operator status recovery returns the original decision and
cannot activate a second grant or mint a credential.

The configured grant interval and credential lifetime remain finite. Neither
non-expiring credentials nor self-issued child delegation are introduced here.
No synchronization permission is inferred from ordinary read access.

## Versioning and custody

Wallet's generic `castalia.access-request-challenge.v1`, its signing domain,
provider contract and Rust/WASM implementation remain unchanged. The new
application-owned `devgraph.access-operator-action.v2` carries an explicit
`previous_assignment_digest` for review/approval (null means no existing grant).
Revocation additionally binds the exact member, installation, request/version,
assignment digest and reason. V1 rejects these fields, including explicit nulls.
V5 refuses legacy review/approval and rejects write or mixed selections **before
Wallet consent** rather than silently activating only part of a selection.

The reviewer receives an activation plan disclosing replacement. The same
operator, policy revision, request version/digest, selection, prior-assignment
digest and review ID must still match at approval. Changing an existing static
or active assignment therefore needs a fresh exact review. Each new request
remains distinct; previous requests become superseded when their binding is
replaced. Credentials cannot gain scope through reissuance.

`devgraph.access-operator-result.v2` returns active/revoked decisions with an
assignment digest and `credential_issued: false`. Read-only inbox responses
advertise an explicit read/write activation capability and bounded current-grant
summaries; an operator outside a grant's ceiling cannot inspect or revoke it.
Requester `devgraph.access-request-result.v3` reports `grant_active` separately
from credential issuance and distinguishes active, expired, revoked and
superseded requests. Original v1/v2 envelopes and bytes remain readable.

## Atomicity and policy boundaries

`auth/access_provisioning.py` maintains an owner-private SQLite overlay in the
existing access inbox. The reviewed member policy file supplies the issuer
ceiling and historical assignments. No HTTP caller can replace that file or
supply the authority configuration. The overlay never edits it. Replacement and
revocation mask the old binding; revocation cannot fall back to an older static
grant. Historical decisions and credentials remain retained.

Approval, assignment replacement, revision advance, result and audit share one
transaction. Storage failure leaves all of them unchanged. Duplicate action IDs
return their original result, and conflicting bytes are rejected. Status is a
freshly signed lookup only. An unavailable result remains unknown, without
resubmission. Browser recovery persists only unsigned proposals and identifiers.

The highest observed base-policy revision/digest is retained separately, so a
rejected call cannot roll revocation back. An observed narrowing permanently
deactivates incompatible overlays. Expanding the ceiling later does not revive
them. Member assignment epochs preserve unchanged members' credentials and
invalidate superseded ones across restart.

Enrollment and credential verification attach the inbox to their existing SQLite
transaction and check its assignment revision while holding a read lock. This
prevents a concurrent activation/revocation from committing between the current
grant check and read authorization. A transaction already authorized before
revocation may finish; subsequent calls observe the revocation. Shared scope
filtering still occurs before results, counts and pagination.

## Evidence and limits

Source tests cover actual Wallet WASM consent → Devgraph approval/activation →
native secS credential issuance → repeated holder-bound reads → revocation,
using disposable identities and public custody fixtures. Other tests cover
replacement preconditions, rollback, transaction interruption, explicit v5 opt-in,
requester states and denial of partial write activation. Synthetic browser tests
exercise both the old pending flow and the activation/revocation flow, including
lost review recovery, four themes and narrow screens.

These are source/synthetic checks, not approval in an installed extension or on
a physical phone. Remaining integration includes scoped write activation and
Arena write enforcement, a hierarchical scope browser, Web/iOS request adapters,
packaging and installed qualification. Existing granted reads on supported clients
continue using the unchanged read credential/enrollment contracts.

The next source foundation is the separate [receiver write policy](../../receiver-write-policy/v1/README.md). It adds transactional resource enforcement without extending historical drafts, flattening Arena restrictions into native prefixes, or enabling write activation.

The subsequent opt-in [v3 write activation path](../v3/README.md) composes this
foundation with persistent assignments and independently installed native
admission. The historical v2 contracts and runtime v5 behavior remain unchanged.
