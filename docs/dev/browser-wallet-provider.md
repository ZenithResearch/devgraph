# Browser Wallet provider interface

The graph connection panel uses a capability-based provider interface. An extension,
a Wallet website or an app's native Wallet can implement the interface. An unrelated
Wallet needs an adapter implementing this contract before it can work with DevGraph.
No browser name, Wallet implementation class or user-agent string establishes support.

This document specifies the DevGraph page boundary. It does not implement an iOS
`WKWebView` bridge or qualify an installed native Wallet. The native implementation
remains a separate client change. Publishing the browser UI also does not activate
credential requests for a service origin that lacks its owner-approved binding.

## Supply a provider

A host browser may inject `window.devgraphWalletProvider` into the main page before
it loads. DevGraph prefers this provider over the retained `window.castaliaWallet`
extension hook. Existing consumers of the extension hook continue to work. A
custom page can also pass `provider: () => providerObject` to
`DevgraphWalletAccess.mount(element, options)`; the standard graph page needs no
custom renderer or fork.

For a provider injected after the page's initial discovery window, the host sets
the property and then dispatches `devgraph:wallet-provider-ready` on the page
window. This triggers discovery and a public capability check. It does not approve
trust, connect an identity or authorize a request. A provider appearing later
cannot take over a Wallet website or another Wallet the user has already selected.
After disconnection, the user can choose **Use Wallet** to select the current host
provider again.

```js
// Host-owned JavaScript wrapper; native implementation lives in the host app.
window.devgraphWalletProvider = nativeWalletProvider;
window.dispatchEvent(new CustomEvent('devgraph:wallet-provider-ready'));
```

## Methods and capabilities

Every method returns a Promise of a JSON-compatible object or array, and rejects
with a bounded Wallet error code. A native wrapper must parse native JSON bytes
into objects before resolving; raw JSON strings are not provider responses.

| Method | Meaning and result |
| --- | --- |
| `getCapabilities()` | Public list of implemented capability names; no identity, trust or signing consent. |
| `proposeProviderProfile(profile)` | Review the exact server origin and issuer pins in Wallet; resolve `{state: 'approved'}` only after explicit approval. |
| `requestConnection()` | Ask to connect the current page to Wallet; resolve `{state: 'connected'}` only after approval. |
| `readAccess(input)` | Perform an operation listed below, keeping custody and consent inside Wallet. |

Required capabilities are `provider_profiles_v1`, `connection_v1`, and either
`graph_access_v2` or the retained `read_access_v1`. Sending or recovering credential
requests additionally requires `access_request_v1`. Advertise only capabilities
actually implemented. `graph_access_v2` does not imply generic mutation signing
or `credential_presentation_v2`.

The existing trust profile remains `castalia.provider-profile.v1`; the historical
schema name is a wire identifier, not a restriction to a particular browser or
Wallet implementation. The page supplies exactly its current HTTPS origin and
reviewed issuer pins. Native Wallet must independently validate and present them.

For a provider advertising `graph_access_v2`, `readAccess` supports:

| `input.operation` | Input and response |
| --- | --- |
| `identity` | Return `{subject_public_key, installation_id}`. Public key is 64 lowercase hexadecimal characters; installation ID is 32. Membership fields are unnecessary and are not retained in the graph-v2 connection. |
| `request` | Receive the exact access/status challenge; return `{approval: {challenge, proof, signature}}` after explicit native consent. The approval must preserve the challenge exactly. |
| `approve_graph_v2` | Receive `{challenge}`; return `{accessId, enrollment: {challenge, proof, signature}}` after the user approves the exact scope. |
| `accept_graph_v2` | Receive `{accessId, credential}`; verify it and store it in Wallet, then return `{accessId, receiver, expires_at}`. Receiver must equal the reviewed challenge receiver; expiry is Unix seconds. |
| `present_graph_v2` | Receive `{accessId, call}`; return `{credential, call, proof}` bound to the exact read operation and path. |
| `disconnect` | Receive `{accessId}` for the current connection when available; end that read session without deleting the user's identity. |

The retained `read_access_v1` path uses `approve`, `accept` and `present`, and its
identity response additionally includes a `memberships` array. It may send the
selected membership's `profileId`. Advertising graph v2 selects the modern path;
the page never silently downgrades it to the legacy operations.

### Reusing approved server setup

The optional `read_setup_status_v1` capability adds
`readAccess({operation: 'setup_status', profile})`. Wallet returns exactly
`{schema: 'castalia.read-setup-status.v1', connection_approved, profile_approved}`
with boolean status values. This performs no signing, trust mutation, enrollment,
or access request. The supplied profile must match the actual main-frame origin.
A locked Wallet or an unconnected origin returns false for both values and learns no
installed trust or identity. For a connected origin, `profile_approved` requires
an exact match with an active Wallet-approved profile. A changed key, audience,
caller, profile, or removed approval requires review again.

With this capability the page connects the site first when necessary, then skips
the server review only when Wallet confirms the exact profile is already approved.
An already connected Wallet proceeds directly to checking existing read access.
Document, epoch and navigation checks still apply to the discovery result. Older
providers retain separate explicit setup steps. Discovery never approves a read
credential, substitutes an owner grant, persists a credential, or permits renewal.
The owner grant remains independent of browser login; a returning authorized user
does not submit another access request or repeat owner approval.

The page does not announce a read connection until Wallet accepts the returned
credential. A pending or approved access request alone never enables graph reads.
The existing request recovery keeps the original request ID after uncertain
outcomes. Provider selection changes neither the server's issuer/receiver binding
nor any credential, signed request, approval or durable journal schema.

## Native browser boundary and lifecycle

A native app may render the ordinary HTTPS graph page in a webview that supports
its guarded JavaScript bridge. The wrapper supplies the provider above and routes
calls to its Wallet's native custody and consent APIs. This is not the separate
Transfer operator renderer or its privileged local RPC bridge; do not expose those
owner operations to a remote graph page.

The native host must bind each call to its actual main-frame security origin and
current page/navigation, using native frame information rather than a caller-
supplied URL. Inject the bridge into the approved main frame only; do not expose
it to third-party Wallet iframes or unrelated navigation. Preserve exact issuer,
receiver and signed challenge checks. There is no arbitrary signing, key export,
recovery-file or password method on this page interface.

On Wallet lock, account/installation change, bridge loss or navigation invalidation,
the host rejects pending calls and dispatches `devgraph:wallet:invalidated` on the
old page window. DevGraph disconnects its injected provider session, clears loaded
graph data and rejects stale in-flight results. The historical
`castalia:wallet:read-access-invalidated` event remains accepted. These events
carry no key, identity, credential or authority and do not unlock or reconnect a
Wallet automatically.

The graph connection panel uses the injected provider directly and contains no
Wallet website URL, embedded Wallet or website fallback button. If discovery
finds no provider, **Check for Wallet** remains available. It checks the current
provider again without requiring a reload or weakening capability checks; a
missing provider produces guidance to enable the extension for the current site.

## Verification

`tests/frontend/monitor_wallet.test.mjs` exercises the modern two-field identity,
modern operations, acceptance and scoped reads without legacy membership data.
`tests/frontend/provider_setup.test.mjs` verifies that graph read support does not
confer mutation support. The browser handoff and actual graph-page walkthroughs
cover the legacy extension hook, host hook, late host discovery and invalidation
at phone and desktop widths. These use synthetic providers and prove browser
contract compatibility, not actual WebKit/native custody or service activation.


### Missing provider and missing server setup are separate

An extension or native host provider connects directly through its provider
object. The graph never asks for a Wallet website. Provider presence is reported
separately from service setup. Missing provider guidance and manual detection
remain available after the initial discovery timer expires.

The exact 503 `endpoint_wallet_setup_required` response means the service owner
has not enabled Wallet access for this address. Connection actions stay disabled
until setup is available. A website URL cannot resolve that server-side state.
Installed extensions must expose their provider in the actual browser viewing
this page; another Chrome profile or browser does not establish a provider here.
