# Wallet connections for the monitor

The Wallet monitor uses a holder-bound read connection instead of a pasted key.
The Chrome extension advertises optional `read_access_v1`; other browsers open a
separate Castalia Web `/wallet-connect` window. Login and encrypted recovery-file
selection happen in Wallet. Files chosen from cloud-backed Files/Downloads are
processed locally by Wallet, never uploaded to the monitor.

The flow is: choose Wallet → review server verification settings → approve the
site → approve the exact read scope and expiry. The issuer chooses the scope from
its operator-approved member/device assignment. Membership alone creates no
access. Rust validates the issuer result before the monitor can read anything.

Recovery files, passphrases and private keys never enter Devgraph. Credentials and
read handles are not persisted by the monitor. Filter preferences use only an
origin/public-identity-derived key. Disconnect, lock, expiry and document changes
invalidate the connection and clear prior work. Reconnecting requires fresh
approval; an unsupported Wallet produces an upgrade message, never a pasted-key
or write-authority fallback.

## Operator composition

This is explicitly enabled by `devgraph.signed-read-runtime.v3` in the existing
`DEVGRAPH_SIGNED_READ_CONFIG` receiver composition. Review and pin:

- `devgraph.native-read-policy.v2` as the issuer ceiling;
- `devgraph.member-access-policy.v2`, with matching `devgraph.access-draft.v2`
  assignments for each exact Member Key and installation;
- the matching generic secS read policy, binary hash and private issuer key;
- independently approved membership trust and revocation files;
- retained audit/data paths and the current enrollment database;
- optionally `web_wallet_url`, an operator-selected HTTPS Wallet website.

`devgraph auth access inspect/attenuate/registry/export` accepts explicitly
versioned v2 drafts. Narrow against a v2 ceiling; mixing v1 and v2 is rejected.
Export still produces an offline review plan, not an installed grant. Member
assignment revision/epoch checks prevent renewal from restoring removed rights.
No existing policy, member, device or grant is upgraded automatically.

The public configuration endpoint `/monitor/wallet-access/v1` proposes issuer
verification settings; it installs neither trust nor authority. Its challenge,
enrollment and request endpoints use the existing shared read-signing domains.
Only the allowlisted exact path is bound into each read proof. The issuer and
receiver must agree on the reviewed root policy. TLS/reverse-proxy routing is an
operator concern; use the server's actual browser origin in provider approval.

## Scope and compatibility

The v2 application policy admits all six Todo types. Historical five-kind v1
policies, runtime v1/v2, Work request bytes, generic mutation signing and legacy
native read endpoints remain unchanged. This is a separate application constraint
version, not a rewrite of the shared credential format. Existing native clients
must not be pointed at the v3 composition until their result decoder is qualified.

The daily list, reader, topology and read-only board operate on a storage-filtered
inventory. Work/Arena predicates and archive restrictions apply before counts or
pagination; relationship permissions remain tied to the authorized anchor, and
both endpoints must be visible. A read exceeding the bounded 10,000-item/edge
projection fails rather than returning misleading complete counts.

Observations, receipts, supporting document contents, Arena metadata and project
selection are not implicitly granted by Work read access. Unsupported sections
say they are not shared; navigation requiring additional scopes is absent. The
read-only board does not acquire signing permission from this connection.

The explicit local developer preview retains its already selected HttpOnly
session adapter. It is not selected as a Wallet failure fallback. General SDK
clients may still supply their existing explicit in-memory read credential;
this monitor feature does not force Wallet onto every SDK read.

## Qualification

Run the Python suite with a pinned disposable secS test binary, frontend tests,
Wallet native/WASM tests and provider tests, and Web tests/typechecks. The optional
synthetic browser fixture checks real rendering and cache clearing, not installed
signing or real membership enrollment. Qualify the final packaged extension and
Web Wallet with a disposable identity and actual human consent before activation.
Production Web builds additionally require independently reviewed issuer public
settings. No source test installs trust, changes live grants or deploys services.

For optional UI regression, start the deliberately mock receiver with
`uv run uvicorn tests.browser.wallet_fixture:app --host 127.0.0.1 --port 18883`,
then run `node tests/browser/monitor-wallet.mjs`. Set
`DEVGRAPH_PLAYWRIGHT_PACKAGE` to an existing package.json that has Playwright
installed. The runner checks the fixture marker before interacting, uses a fresh
headless profile, and writes synthetic screenshots under `DEVGRAPH_QA_OUTPUT`.
Never expose the mock receiver outside loopback or describe it as qualification
of installed signing, real identities or real membership.
