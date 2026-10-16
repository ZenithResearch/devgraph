# @devgraph/web — 0.2 breaking developer preview

Devgraph owns HTTP reads and mutation transport. Rust/WASM constructs and validates
requests and canonical results. Castalia Wallet supplies generic credential-bound
approval; it does not supply a Devgraph connection or read credential.

```js
import {initialize} from '@devgraph/web';

const runtime = await initialize();
const client = runtime.connectDevgraph({
  origin: location.origin,
  readCredential: enteredReadCredential, // explicitly supplied, memory only
  provider: window.castaliaWallet,       // optional for reads
});
const todo = await client.getTodo('Todo', 'example');
// For writes, preload setup outside a click. No consent or authority is issued.
const setup = await client.prepareWalletSetup();
// Bind these to separate user-click handlers; do not chain the two ceremonies.
approveProviderButton.onclick = () => setup.approve();
connectWalletButton.onclick = () => setup.connect();

// After setup, create the operation and execute it from a user action:
const idempotencyKey = crypto.randomUUID();
const prepared = client.prepare({
  schema: 'devgraph.work-request.v2', operation: 'create',
  kind: 'Todo', id: 'new-example', expected_version: null,
  payload: {id: 'new-example', title: 'Check today’s priorities'},
}, {idempotency_key: idempotencyKey});
saveButton.onclick = async () => {
const outcome = await prepared.execute(); // fresh generic Wallet confirmation
if (outcome.kind === 'committed') {
  // The WASM decoder has validated the canonical result before this point.
  console.log(outcome.work);
} else if (outcome.kind === 'outcome_unknown') {
  // Retain prepared.canonical_bytes and the original idempotency key.
  // On an explicit user recovery action:
  recoverButton.onclick = () => prepared.reconcile(); // new approval, STATUS ONLY
}
};
// On teardown:
window.addEventListener('pagehide', () => {
  prepared.dispose();
  client.dispose(); // aborts activity and drops the read credential reference
  runtime.dispose();
}, {once: true});
```

The browser client requires the current page's canonical origin (HTTPS, or HTTP
loopback). It omits cookies, refuses redirects and bounds raw response bytes. The
current local receiver runs on `http://127.0.0.1:8080`; its operator must explicitly
configure the allowed origin and issuer trust. Nothing installs trust or expands
a grant automatically. Unsupported operations are rejected before consent.

## Migration from 0.1

Replace `connectCastalia()` → `runtime.createClient({connection})` with
`runtime.connectDevgraph(...)`. Remove `requestReadAccess()` and `read_context`;
supply a read credential obtained through Devgraph's read-access setup. Do not put
it in local storage, session storage, URLs or Wallet grants. Reads work without
Wallet; the credential cannot authorize mutations. Disposal/navigation drops the
client's credential reference (JavaScript does not guarantee memory zeroization).

Replace `prepared.authorize()` → `attempt.execute()` with `prepared.execute()`.
A generic `credential_presentation_v2` Wallet and matched Devgraph/secS candidate
are required for writes. Missing capability, trust or version support fails closed;
there is no fallback to `provider.devgraph` or a read credential. Compatibility
exports remain temporarily for the separate, qualification-gated removal change.
They are not used by `connectDevgraph`.

Never repeat `execute()` after an uncertain outcome. Keep the exact prepared
canonical bytes and idempotency key, and invoke `reconcile()` after fresh approval.
It calls only the status endpoint; absence of a matching receipt remains unknown.
Recovery returns the original receipt with `duplicate: true` and `work: null`.
Read again for current state. The receipt is Devgraph's mutation record, not a
cryptographic external-code-quality attestation. `pending` means the graph commit
succeeded and its outbox work is pending.

## Contracts and reads

`devgraph.work-request.v2` covers base Todo and the five subtypes with
`not_started`, `in_progress`, `done`, and independent archival. Use `getTodo` for the
canonical read shape and `devgraph.work-result.v2` for canonical mutation results.
`getWork` and Work/Arena v1 request/envelope compatibility remain unchanged. Work
request version and credential transport version are separate: canonical writes
use `/todo-operations/v2`; existing Work/Arena requests use their v2 authority
endpoints. Both are reached through Devgraph's `/credential-work/v2` browser API.

`prepareBytes(Uint8Array, {idempotency_key})` accepts request bytes directly to the
Rust parser. Preserve its returned canonical bytes for recovery. Integer inputs
accept number or bigint within ±(2^53−1); wider values, fractions, negative zero,
duplicate keys, unpaired surrogates and unknown fields are rejected. Read-domain
64-bit values remain bigint. `exportJson` is for application export, not signing.

`iterateWork({kind, include_archived?, descending?, limit?, after_id?})` and
`iterateRelations({kind,id}, relationship, {limit?, after_resource?, signal?})`
fetch bounded pages, detecting non-advancing cursors. Relationships are `children`,
`parent`, `dependencies`, `dependents`, `blockers`, `blocked`. Pagination is not a
snapshot. `cypher(request_bytes, {columns,types,limit}, {signal?})` validates scalar
results; the receiver's compiler decides which queries may execute. Reads accept
`AbortSignal`. A canceled or disposed mutation cannot roll back a prior commit.

The package imports safely during SSR. `initialize({wasm})` accepts a URL, bytes or
compiled WebAssembly.Module; `@devgraph/web/wasm` supports bundler asset URLs.
Installed consumers need no Rust, Git or lifecycle download. Build with pinned
toolchains, then pack explicitly with `npm pack --ignore-scripts`.

`sdk-build.json` binds every shipped runtime module and WASM to its evidence.
Source/fixture passes do not establish installed approval. Release and full legacy
removal require exact-artifact browser **and** terminal/native acceptance in a
fresh disposable macOS VM, including a rerun after packaged source changes. That
qualification remains blocked until the VM is available; live activation is
outside this delivery.

## Merged Wallet provider setup (0.2.0-preview.2)

Wallet `ff8de6ddc04ac2fb6b39408274276bc4ce984d42` starts with no trusted
providers. `prepareWalletSetup()` preloads the operator's public provider profile
from `/credential-work/v2/provider-profile` and verifies the required capabilities.
Its `profile` getter returns a display copy. Invoke `approve()` and `connect()`
from **separate clicks**: provider approval and connection are distinct ceremonies,
and Wallet requires fresh browser user activation for both. Loading setup, reading,
and constructing a request never install trust or issue a grant. If either action
is declined, keep reading mode and show the error. Disposal invalidates these
controls. Do not persist read credentials with ordinary UI preferences.

This does not automatically establish membership. The profile is restricted to
request-credential trust for the exact page origin. The receiver still requires
explicit operation/resource grants. Terminal SDK configuration keeps its separate
owner-private issuer trust and interactive signing ceremony. Historical Work/Arena
request bytes, signatures and response envelopes are unchanged.
