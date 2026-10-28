# Session access review: candidate application interface

Status: source-pinned, synthetic producer/consumer fixtures and a pure binding
checker in `src/devgraph/session_review_contract.py` for a native review adapter.
No live route, session-key custody helper, issuer activation or execution adapter
is installed or implemented by this contract. These unsigned records are
not credentials, signatures, proofs of possession, or permission to execute.

Transfer owns native review and the thin MCP/local IPC adapter. DevGraph owns
provider semantics/execution; secS owns authority verification and shared resource
contracts; Wallet Core owns holder verification/derivation/presentation. The local
session agent owns its key and attaches proofs. A model receives opaque handles
and sanitized outcomes, never a seed, bearer, root credential or presentation.

## Reuse and compatibility

`fixtures.json` pins the inspected source revisions. Permission records reuse
`secs.holder-grant.v2`'s `Permission`, `RoleSelection` and
`secs.resource-selector.v1`; those formats are not redefined here. The example is
an exact-object read selection. Provider-native validation of the complete
permission/effect contract remains required; these fixtures do not implement a
second selector evaluator. Keep constraints and hierarchy guards when narrowing.

Existing holder v1/v2 grants, fixed-ID semantics, signatures, journals and
per-operation human approval retain their contracts. New-session issuance is a
fresh issuer decision to that holder, never same-holder attenuation of the
owner's grant. Onward delegation remains disabled. Session write issuance is
unavailable until the provider/native chain is implemented and qualified.

## Capabilities and trust

The `unsupported` fixture represents the current absence of this session
integration, not live discovery output. It must render unavailable, never trigger
the old signer or reuse the owner Wallet automatically. The separately named
`simulated_read_qualified` fixture is for UI development only. Every capability
defaults false; unknown/missing versions or features fail closed. A provider
profile is loaded from local reviewed configuration and matched to trusted issuer
pins; a page or model cannot nominate its own trusted issuer/key.

The capabilities' expiry default of 3600 seconds is an implementation candidate,
not a selected user policy or Wallet lock timeout. The operator may choose a
different expiry within the actual provider ceiling. Capability metadata alone
never authorizes a request.

Expiry limits bound the finite session grant interval, distinct from shorter
derived credentials and per-call proofs. The provider clips a refreshed review
deadline to `min(now + 120, expires_at)`. A 60-second selection therefore has a
60-second deadline, which may equal its expiry; `now >= review_deadline` always
rejects approval. `minimum_expiry_review` and the just-outside-ceiling tests cover
both boundaries. Provider policy binding is explicit (`policy_id`,
`policy_version`, `policy_digest_sha256`) and separate from the trusted profile's
digest. A policy change invalidates a pending approval even if the profile file
did not change.

## Prepare, review and result

1. A local custody helper creates a session key only after an explicit identity
   creation request. It returns an opaque handle and public identity. The helper
   proves key possession directly to the provider/native adapter; a chat label is
   display context, not authentication. The shared saved signer is not silently
   repurposed as a session identity.
2. Authenticated native IPC prepares a pending request under an immutable request
   ID/idempotency binding. The server resolves canonical service, issuer pins,
   permissions, expiry ceiling and provider policy revision. Routes/TLS consent
   remain separate transport configuration. Local and Tailnet aliases reference
   the same service; endpoint switching cannot mint another scope or evade replay.
3. The `manifest` is the entire unsigned review snapshot. Its
   `review_digest_sha256` is SHA-256 of canonical UTF-8 JSON (sorted keys, compact
   separators, no floats), not a signature. Native authenticated IPC and the
   independently verified issuer decision remain mandatory. Changing expiry,
   holder, policy/profile pin or permissions requires a new review revision,
   challenge and digest. A stale approval must return `review_changed`.
4. Human approval binds the final exact manifest. The issuer rechecks authority,
   caller/key possession, current policy, chosen expiry and original request ID.
   An API to request review or retrieve outcome is not an API to approve/sign.
5. `issued` returns only a credential handle and finite expiry to the model. The
   local custody helper holds the real credential and attaches fresh proofs to
   operations. The receiver still checks current grants/revocation, replay,
   actual object placement, paired effects and atomic pre/postconditions.

The review challenge deadline is separate from the approved grant lifetime.
Expiry adjustment before submission invalidates the old snapshot; after possible
submission the original bytes/ID must be reconciled, never edited or resubmitted
automatically. `outcome_unknown` has no usable credential handle. Only retrieving
the original issued outcome may populate one. Denied/cancelled/revoked/expired
results confer no credential. Client cancellation after dispatch does not prove
that issuer activation was cancelled. Persist the original result and idempotency
binding so relaunch does not issue twice.

`check_expiry_update` verifies an expiry-only response keeps the request handle,
ID, holder, provider/policy, permissions, original interval start and display
context; revision increases, challenge changes, and the exact requested expiry is
returned under a new digest. A changed policy requires a newly prepared review,
not a silent expiry edit. The caller must not invoke this path after possible
submission. `check_result` rejects wrong request/holder/service/review digest,
different issued expiry, stale issued handles, unknown fields, or a credential
handle on any non-issued state. The fixtures include these negative results.
Closing a window does not manufacture `cancelled` after dispatch: only a
definitive pre-dispatch cancellation or provider-confirmed cancellation may do so.

Locking the owner Wallet does not revoke a previously issued credential. Quitting
a session does not claim server revocation unless that operation succeeds; dated
expiry remains mandatory and renewal requires a new explicit review. Production
grants, installation, trust changes and app replacement require their own review.
