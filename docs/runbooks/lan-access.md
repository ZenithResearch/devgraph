# Private LAN standing-access listener (runtime v8)

Status: source candidate; deployment, grants and device qualification are separate.
The existing `lan_read_runtime` remains read-only v5. The public pilot also stays
unchanged. Do not replace either factory to expose administration implicitly.

Use `devgraph.lan_access_runtime:create_app` only with separately reviewed
`devgraph.signed-read-runtime.v8` configuration and a retained receiver data root.
It requires an exact RFC1918 IPv4 HTTPS origin matching both the enrollment and
read-authority policies. Bind explicitly to that address/port, disable proxy
headers, and use the reviewed certificate/key. Listener lifetime has no test
clock; grant, credential, verifier-key and TLS validity remain independent.

Its closed route boundary admits the existing signed read/connection endpoints,
published request catalog, signed access requests/operator actions, required
monitor assets, and guarded credential Work v2 preparation/execution/status.
The explicit Work/Todo v2 projection routes also use the same scoped receiver.
Legacy Work v1, Arena mutations, SDK/administration/query/database routes,
OpenAPI/docs and arbitrary Work GET routes remain closed. No bearer header,
cookie, forwarded header, same-LAN location or HTTP identity value grants access.
The application still verifies holder/issuer/installation, exact operator
selection, native policy binding, current path scope and replay on every use.

The boundary retains exact TLS Host/Origin checks, no proxy-derived identity,
header/query/path limits, global rate/concurrency limits and a ten-second bounded
request read. Access/read bodies remain capped at 64 KiB; named projections at
128 KiB; generic credential transport at 256 KiB to match its existing closed
parser. Wrong Host/Origin/bearer and oversized bodies are rejected before the
handler. No additional receiver, key, grant, trust or fixture is created by the
factory.

Before activation: inspect actual OS-identity-native metadata; retain the same
signer and replay state; advance policy revisions monotonically; install matching
public receiver pins; prepare empty member assignments under a reviewed ceiling;
verify database readiness separately from liveness; and preserve SQLite history
using consistent snapshots. Stop on a changed signer, native digest, certificate,
unknown dispatch or conflicting listener. A service owned by another OS identity
must not accidentally use the primary user's native authority.

Every member grant still requires its exact signed operator decision. Work
presets cannot grant operator administration, community membership, owner transfer
or child delegation. Missing atomic in-path creation/evidence is not worked around
by broad native grants. Owner bootstrap and persistent certificate lifecycle remain
separate requirements. Source fixture initialization must be labelled disposable
setup, never a successful Wallet-authorized write or fabricated EventReceipt.

Qualification: `tests/native_read/test_lan_access_runtime.py` plus unchanged
read-only/public boundary suites. Host, path/method, body-limit and downstream
401 behavior are tested. These checks do not attest installed browser/iPad use.
