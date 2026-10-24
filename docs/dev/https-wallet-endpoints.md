# Wallet credential requests at HTTPS endpoints

Status: the source now includes the installed owner-v9 composition and the
owner-published v10 secondary endpoint. `devgraph.owner_https_runtime:create_app`
serves the shared Wallet UI and signed member requests at a separately configured
HTTPS origin. Owner registration, issuer custody and existing grants are retained;
a secondary receiver starts with its own empty member/request/replay stores.
Live deployment evidence is recorded separately in the private workspace. Tests
alone do not establish extension consent or credential receipt.

`devgraph.endpoint_read_runtime:create_app` composes the existing Wallet monitor,
signed member request inbox and holder-bound credential enrollment at an explicitly
configured HTTPS origin. It supports a Tailnet address, LAN address or public
HTTPS address using the same application flow. No hostname is bundled into a client.

The browser connects its Wallet, reviews the server, requests its chosen scope,
and checks the original request until the operator decides. An active grant lets
the user review read access and accept the returned credential into Wallet.
Submitting a request does not grant access. Unknown responses retain the original
request ID for status recovery. Native clients discover the same public settings
at `/.well-known/devgraph-connection.json` and use the existing signed request API.

The application routes and stored credential/request formats are unchanged. The
factory requires request-enabled runtime v5 or later supported by the installed
signed-read composition. Owner-bound v9 configurations require the original signed owner registration.
Use the owner HTTPS factory for a v10 secondary origin, retaining v9 as its
parent. Neither factory creates or replaces an owner registration.

Configure `DEVGRAPH_SIGNED_READ_CONFIG`, `DEVGRAPH_ENDPOINT_ORIGIN` and optionally
`DEVGRAPH_ENDPOINT_NAME`. The selected origin must already match the enrollment,
read authority and operator policy. Before starting, run the read-only review:

```sh
python -m devgraph.endpoint_read_runtime \
  --config /absolute/private/runtime.json \
  --origin https://your-devgraph.example
```

A successful review reports only binding readiness, not grant validity, network
reachability, a startup result or successful credential issuance. It never reads
issuer private-key bytes, creates a database, grants access or changes network
publication. A mismatched origin requires a separately owner-authorized endpoint
configuration; changing the URL in a connection file cannot substitute for that.

Serve over TLS with the exact external Host. Arbitrary forwarded headers do not
authorize HTTP. Keep the database and private API on their existing loopback
listeners. The public member boundary admits only the monitor/assets, public
discovery/catalog, signed request and read-enrollment/presentation routes.
Operator administration, native authority, direct graph routes, and Work mutation
routes remain closed even if present in the composed application. Existing LAN
administration and bounded public-pilot factories retain their original behavior.

Network configuration is separate: a Mac may use an existing Tailscale Serve or
Funnel listener for this origin after the receiver has been configured. Tailnet
clients need their own Tailscale connection. Public HTTPS clients use their normal
HTTPS transport. The server's discovery profile does not force either route.

## Browser access controls

The shared browser panel shows the current page origin and three steps: connect
Wallet, request or check access, and receive the credential. It uses the same
signed request flow on a public HTTPS origin and a Tailnet HTTPS origin. Visitors
configure no service endpoint or network in this panel; endpoint configuration
belongs to the service owner through the service-control interface or Zenith
Transfer.

An unavailable setup can be retried without submitting an access request. Pending,
unknown, declined and activated requests have distinct guidance. Unknown outcomes
keep the original request ID for recovery. The panel reports a connected read
session only after Wallet accepts the credential; historical request updates do
not replace the current connection state. Existing markup without the new heading,
progress indicators or retry button remains supported.

`node tests/frontend/browser/read_handoff_smoke.mjs` checks the shared controls
at synthetic public and Tailnet origins at 375 and 1280 pixels, including retry,
request recovery, expired reviews and accepted credentials. It intercepts network
requests and substitutes a synthetic Wallet, so it does not qualify installed
Wallet approval, owner binding or live network reachability.

## Deploying the browser entry before endpoint activation

`scripts/build_wallet_monitor_shell.py --output /absolute/new/public-site` exports
only the Wallet monitor, board and public assets. Serve this immutable directory
with `devgraph.wallet_monitor_shell_runtime:create_app`, setting
`DEVGRAPH_WALLET_MONITOR_SHELL` to the directory and
`DEVGRAPH_WALLET_MONITOR_ORIGIN` to the exact HTTPS origin. Bind the listener to
loopback and proxy that existing origin to it. Keep the manifest, server source,
logs and private deployment configuration outside the served directory.

The Wallet controls replace the password field inside **Connect to your graph
with Wallet** on the actual graph page. This setup-only composition has no graph
connection or credential authority: configuration returns 503, signed request
and enrollment POSTs remain closed, and the panel reports that setup is required.
Publishing this shell is UI deployment, not successful credential integration.
After the exact owner-bound HTTPS endpoint is authorized and qualified, replace
the setup shell with the signed endpoint composition. Do not use localhost
credentials or another origin's owner registration as a shortcut.

`tests/frontend/browser/graph_connection_smoke.mjs` checks the actual exported
page and assets, the placement under Connect to your graph, missing-setup failure,
request-pending behavior against a synthetic configured service, and the board.
The existing synthetic handoff checks qualify credential acceptance separately.


The same browser page accepts extension or native app providers through the
[shared browser Wallet provider interface](browser-wallet-provider.md). Modern
graph providers do not need to expose legacy community membership data. Native
webview bridge implementation and testing remain a separate client responsibility.

## Owner-published secondary interface

Runtime v10 adds `owner_parent_runtime_file` and
`owner_endpoint_profile_file`. The parent must be v9 with a current signed owner
registration for the exact graph data root. The endpoint policy must equal the
parent policy except for its receiver. Enrollment and read-authority policies
have the same restriction; issuer key and native-policy paths remain unchanged.
The endpoint profile must match those public issuer pins. Linked, overlapping
and cross-store aliases of member, request, enrollment and native-admission state
are rejected. Existing v1–v9 files keep their contracts.

This factory supports explicit LAN, Tailnet and public HTTPS profiles. It never
changes network publication itself. Protected installation-owner configuration
is a host action, distinct from the four Wallet access-administration operations.
Those operations do not authorize service configuration. The host operator must
authorize publication explicitly; a visitor cannot register a new receiver.
The coordinated Transfer service-configuration interface remains separate work.

For a dedicated local TLS terminator, v10 optionally accepts
`endpoint_tls_proxy_port`. Bind Uvicorn to the exact `127.0.0.1` port with
`--no-proxy-headers`. The wrapper accepts only HTTP from a loopback peer on that
exact local socket, sets the transport scheme from this protected configuration,
and removes forwarded headers. The inner boundary still requires the exact
external Host and Origin and verifies every Wallet proof and grant. With this
field omitted, the boundary requires real upstream HTTPS as before. This setting
conveys no client authority; processes running as the installation owner already
control the runtime and key files.

The HTTPS member boundary exposes no operator actions, browser operator UI,
bootstrap, raw graph API or Work mutation routes. Owner review needs a separately
qualified native RPC client/configuration for that exact endpoint partition.
The older native RPC versions support only loopback/private IPv4; DNS endpoints
need an explicit compatible consumer update, not a relaxed v1/v2 decoder. A
pending member request is not an approval or credential receipt.

Qualification includes original owner preservation, policy widening rejection,
state alias rejection, loopback termination/Host checks, anonymous enrollment
rejection, real holder-signed pending requests with replay reconciliation, and
public/Tailnet source compositions with synthetic storage/native verifier pins.
The live installed composition must additionally validate the actual owner and
native verifier; synthetic keys are never used for production requests or grants.
