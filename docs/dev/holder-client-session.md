# Finite browser credentials

Status: client custody implementation under qualification. The new exchange is
not advertised or activated by this frontend change. The issuer, shared secS
verifier, Wallet approval bridge and receiver must be qualified together before
the new capability is enabled. Existing holder v1/v2 bytes remain unchanged.

The public holder-grant discovery may add `v2_endpoint` and
`supported_schemas` while retaining its existing `schema` and `endpoint`.
The browser accepts only the fixed `/monitor/wallet-access/v1/grants/v2` path
and the explicit v1/v2 schema pair. A capable Wallet can then prepare a finite
session from an already saved v2 parent without changing the server's default.
An existing v1 saved grant keeps its original flow. **Set up browser session**
starts a fresh, Wallet-reviewed enrollment under the current assignment; it
does not convert stored grants or request different permissions. Saving that
new reusable grant is a separate one-time choice from approving its finite
session. Recovery retains the original signed enrollment and selected endpoint,
and rejects a result for the other grant protocol.

Signed service discovery uses the browser fetch API as a standalone function,
just like ordinary read requests. Calling a stored fetch function as a method of
the client fails in Chromium before Wallet can connect. Discovery still verifies
the exact issuer signature, site origin, service, route prefix and validity;
loading public settings never installs trust or grants access.

`tests/frontend/service_route.test.mjs` checks the fetch receiver as well as the
signed route. `tests/frontend/browser/holder_session_smoke.mjs` exercises native
browser fetch, crypto and the monitor UI across legacy and signed canonical
routes, public and Tailnet-style origins, and narrow/wide viewports. The canonical
case uses the deployed additive v1/v2 grant discovery shape and checks one
synthetic Wallet approval, prefixed reads, Wallet lock, fragment navigation and
fixed expiry without renewal. These fixtures use no personal Wallet or grants.

Wallet keeps the reusable grant and long-term signing key. For the new session
profile, the browser creates a fresh nonextractable Ed25519 key and a distinct
random client installation ID. Wallet approves one typed challenge binding that
client, the actual site origin, canonical service, parent grant, exact read
permissions and fixed expiry. Wallet sends its authorization and parent grant
directly to the issuer. The page receives only the finite issuer credential.

The issuer clamps expiry to the requested duration, its configured cap, the
parent validity end and the parent's existing maximum credential lifetime. It
cannot silently extend a short parent lifetime into a longer session. A proof
lasts at most 60 seconds and never beyond that fixed credential expiry. Reading,
changing views, reconnecting and locking Wallet cannot renew the credential.
A fresh session requires another explicit Wallet approval.

The receiver must check the original parent is still current and active on each
read, alongside exact scope, issuer/service/origin binding, client proof, time
and replay checks. A saved signature or cached principal is insufficient.
Neither the session credential nor its temporary key can create another session
or authorize an administrative decision. Existing operator approval flows keep
their own consent requirements.

| Event | Client behavior |
| --- | --- |
| Fragment-only view change in the same document | Keep the same key and original expiry. |
| Expiry or a backward local clock change | Close custody and require a new approved session. The server checks time independently. |
| Application Disconnect or document close/reload | Drop key and credential. No persistence or key recovery is provided. |
| Root Wallet locks after session issuance | The new independent finite client may continue until expiry or parent revocation. Existing Wallet-bound read handles retain their lock behavior. |
| Lost exchange response | Retain the original request/challenge and client key for status reconciliation; never silently issue again. |

`holder-session.mjs` keeps the key in a private field and uses WebCrypto with
`extractable: false`. It checks the issuer signature, exact reviewed challenge,
canonical digests and finite bounds before installation. It generates a fresh
nonce and exact call proof for every read. No Wallet or recovery material enters
browser storage, source logs or Castaway data through this module.

Nonextractability prevents raw private-key export through WebCrypto. Compromised
same-origin code could still use an active key; it does not make an XSS-compromised
page safe. Scope limits, short fixed expiry, receiver checks and ordinary web
security remain necessary. See [Web Cryptography](https://www.w3.org/TR/webcrypto/)
for the browser primitive and [RFC 9449](https://www.rfc-editor.org/info/rfc9449/)
for the general sender-constrained token pattern. The protocol here is the shared
secS session profile, not an OAuth or DPoP wire implementation.
