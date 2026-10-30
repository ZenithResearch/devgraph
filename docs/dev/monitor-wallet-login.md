# Monitor Wallet login

Implementation branch: `codex/monitor-wallet-login`, starting at public DevGraph
main `38d7c2af3d5a69600057d3eecd0d37ed9da4bacb`.

The user requested a dedicated login page, one **Log in with Wallet** action,
one visible Wallet review window, and a delegated browser session. This is an
OAuth-like user experience over the existing proof-bound secS session contract;
it does not implement OAuth 2.0. The sidebar is not a prerequisite.

## Ownership and compatibility

DevGraph owns `/login`, the browser temporary key, verified finite credential,
reload continuation, monitor/board session admission and logout. Castalia Wallet
owns an additive `session_login_v1` capability and a popup-lifetime signing host.
The reusable grant and long-term identity key never enter DevGraph. Existing
readAccess operations, native clients, grant bytes and permissions remain valid.

The initial browser action calls `open_session_login` with the configured
canonical service, public provider profile/access binding, temporary public key,
temporary installation ID and a requested lifetime bounded by the selected service
profile. The current
read-only profile selects the longest duration already permitted by the caller,
verified service and signed parent. Wallet prepares that duration directly and
shows the exact permissions and absolute expiry in its final sign-in review.
There is no mandatory duration setup screen. The browser sends the
verified service ceiling (at most 3600 in the current signed contract), not an
unlimited delegation. The user also requested support for eligible approved write authority and
human-selected lifetime; the required parent compiler and transactional write
receiver are documented in [the audited integration plan](service-session-delegation-plan.md).
This release does not advertise that unimplemented profile.
Wallet opens one visible review window immediately. Unlock, provider/origin
review and existing-grant checks happen inside that window. Missing access is
an explicit request/pending state; login does not silently provision or save a
different grant.

Optional `access_request` and `grant_acquisition` descriptors supply closed,
literal relative routes from the checked public configuration. They confer no
authority. DevGraph supplies its opaque access request schema and bounded exact
result-schema tags as service data. Wallet owns generic signing/consent and does
not contain DevGraph adapters or hierarchy rules. Request submission,
obtaining already-approved access, saving a
reusable parent and starting a temporary session each retain their explicit
Wallet review; they occur within the same window. The grant descriptor names
the exact supported result wrapper and v2 parent schema. The browser never
receives the reusable parent or private enrollment authorization.
The descriptor preserves the service's explicit membership requirement. Missing
disclosure defaults to required; it never implies proof-free enrollment. Current
first-use popup enrollment supports explicitly optional membership only, and
must explain an unsupported membership requirement rather than silently omit it.

Popup setup uses a separate, cookie-free extension transport on exactly four
existing POST routes: `/monitor/wallet-access/v1/challenges`,
`/monitor/wallet-access/v1/grants/v2`,
`/devgraph/access-requests/v2/challenges` and
`/devgraph/access-requests/v2/actions`. Preflights retain TLS/Host, literal-path,
header/body, expiry and abuse limits and never call the issuer. Responses reflect
only a validated extension origin, never credentials or cookies. CORS grants
transport, not authority: the original signed challenge, proof, canonical
service, membership, nonce and scope checks still run. Protected reads, operator
actions, public configuration and arbitrary paths gain no extension CORS.

Wallet returns `{schema: "castalia.session-login-prepared.v1", loginId,
challenge}`. The browser validates the challenge and proves possession using
its nonextractable temporary key. `complete_session_login` continues the same
window with that proof; it cannot create another approval window or manufacture
a fresh page gesture. The human reviews the actual permissions and fixed expiry.
The result is the existing `castalia.holder-session-opened.v1` credential result.

## Custody and lifetime

The visible Wallet window owns a dedicated worker running unchanged Wallet Core.
Only encrypted existing Wallet records are read; no password, decrypted root,
root unwrap key or private reusable grant is persisted or sent to the page.
Closing the window ends its signing custody. Background-worker suspension must
not destroy the foreground ceremony. Explicit lock/import/identity/trust/grant
changes invalidate a stale signer before consent, signing and dispatch. This
requires shared revision/lock-intent fencing, not a periodic keepalive.

Chrome-supplied tab/frame/document IDs, exact origin, public child key, challenge
digest, popup identity and absolute deadlines bind the handoff. The page cannot
supply those Chrome identities. Before possible dispatch, preserve only bounded
nonsecret recovery metadata; never a durable approval flag or plaintext parent.
After possible dispatch, use the original child key and original issuer status.
A generic status HTTP error, including 401, is not evidence that the original
session expired or was revoked. It pauses access and retains recovery custody;
it never silently clears that key and starts another approval. Only a validated
terminal status, locally proven expiry/invalid custody or explicit sign-out ends
that continuation.

The browser stores its nonextractable Ed25519 CryptoKey with the exact challenge,
verified token, public issuer binding and original expiry in origin-local
IndexedDB. Reload revalidates those values and current issuer status. It does not
reset the expiry. Storage loss, key mismatch, clock rollback, revocation or expiry
ends local access safely. Logout deletes local session material and fences slow
callbacks; it does not claim server-side revocation of the reusable grant.
IndexedDB is origin-wide, not cryptographically tab-isolated. Nonextractability
does not protect against same-origin XSS invoking the key. The selected profile's
permissions, fixed lifetime and receiver's current-parent checks remain authority
bounds. The existing read-only v1 contract must not silently become write-enabled.

## UI

The login page shows the current server, one login button, concise session
lifetime text and an accessible status. Missing/old Wallet, blocked popup,
cancelled review, missing access and uncertain dispatch have distinct messages.
No password, recovery file, Wallet website or manual endpoint field appears in
the DevGraph page. The authenticated monitor and board show a compact session
status, fixed expiry and sign-out action. Returning to a validated local monitor
or board path never puts credentials or keys in URLs.

The shell uses per-response script nonces, no eval/inline-script allowance, exact
same-origin application transport and extension script schemes so installed
Wallet providers can inject their interface. CSP is additional protection, not
a claim of isolation from every same-origin script or installed extension.
Cross-tab sign-out broadcasts immediate local invalidation; a storage epoch
also fences every read and late save. If deletion fails, the UI reports it and
offers retry rather than claiming sign-out succeeded. Pagehide only detaches
memory, preserving the finite IndexedDB continuation; BFCache restore checks
the issuer again. No Wallet call or automatic renewal occurs during restore.

## Recovery correction

An authenticated status for an unissued challenge must become terminal after its
review deadline. An already-issued credential remains recoverable after that
deadline until its original absolute expiry. Sample admission time after the
database write lock, check again before publishing a newly generated credential,
and retain terminality so clock rollback cannot reopen an expired preparation.
No new issuance, automatic renewal or fresh signing follows a status request.
The existing `expired` state with `credential: null` is compatible with retained
native decoders; signed formats and original `expires_at` do not change.

## Qualification required

- Producer/consumer closed-shape tests and existing legacy client tests.
- Exact issuer deadline, queued-lock, slow-issuance and terminal-clock tests.
- Browser key persistence, corrupted/evicted storage, expiry, logout races and
  reload with the same absolute expiry and current-parent status.
- Real packaged Wallet with the actual monitor login client, no sidebar or
  DevTools: root locked, background termination at each stage, duplicate clicks,
  wrong document/popup, cancellation before/after dispatch, lost committed reply,
  revocation and expiry. Personal approvals remain human-owned.
- Preserve original Wallet/graph data, permissions, endpoint policies and routes.

Source and disposable tests do not establish a successful personal login. Record
the exact deployed source/package and the human-approved live result separately.

## Local candidate evidence

- 395 frontend unit/contract tests pass, including retained legacy clients.
- The repository tests include native holder/read authority integration and
  transport denial cases. Record final run counts alongside the exact release;
  opt-in external integrations remain separate.
- Eight disposable real-browser walkthroughs exercise actual monitor/board/login
  shells, public and Tailnet origins, desktop and narrow layouts, ordinary and
  lost-reply completion, nonextractable IndexedDB custody, unchanged expiry,
  reload/navigation, stale-epoch saves and cross-tab sign-out. Wallet and issuer
  in this particular walkthrough are synthetic; it is not extension qualification.
- Ruff, source diff, generated ontology, Zenith tokens and backup-cost checks pass.

Before activation, qualify the real packaged Wallet against the actual consumer
and drain every old exchange writer. This includes pinned native Transfer helper
registrations that can launch a provider on demand, not just HTTP listeners.

## General resource request and connection recovery

Send access request in Wallet prepares and submits the existing null-intent
request in one explicit button action. The verified service binding supplies the
resource. No requester preset, resource picker, or requested permission inventory
is required. The owner chooses permissions during review. Legacy signed request
formats, retained request IDs and unknown-outcome recovery are unchanged; checking
a retained request uses status, never silently replaces it. The older connection
UI also prepares general requests instead of presenting a preset/scope form.

A temporary issuer-status failure leaves the monitor in a paused state with the
original browser key/checkpoint. It tries the original status at most three times
(2, 5 and 10 seconds), with Reconnect and online recovery available afterward. It
does not open Wallet, issue another credential, or extend expiry. Expiry and
authenticated revocation still end access, and Sign out still clears local
custody. Pagehide fences retries; BFCache restore resumes the original session.

First-use provider trust and receiving/saving an approved reusable grant retain
Wallet-owned consent. A simple request is not a grant or automatic owner approval.
