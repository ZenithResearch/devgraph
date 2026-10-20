# Private LAN read listener

`devgraph.lan_read_runtime:create_app` is an explicit read-only runtime v5
listener for an RFC1918 IPv4 HTTPS origin. Its process lifetime is independent
of grant validity. It creates no keys, assignments, trust, or credentials, and
does not revive expired policy. The public pilot retains its separate bounded
factory and shutdown timer.

Use only owner-reviewed `DEVGRAPH_SIGNED_READ_CONFIG` and `DEVGRAPH_DATA_ROOT`,
with the existing isolated database configured through `NEO4J_*`. Set
`DEVGRAPH_LAN_ORIGIN` to the exact HTTPS origin and optionally set
`DEVGRAPH_LAN_NAME`. Bind uvicorn explicitly to that same LAN address and port,
use the reviewed TLS certificate/key, and disable proxy-header interpretation.
Do not bind all interfaces or put database listeners on the LAN.

The closed boundary admits connection discovery, static monitor assets, signed
read enrollment/use, and member access requests. Exact TLS Host/Origin checks,
body limits, deadlines for reading requests, rate and concurrency limits remain
enforced. Bearer headers, operator administration, Work mutations, query/database
routes and API documentation remain closed. Downstream grant expiry, revocation,
Wallet consent and proof checks are unchanged.

`/live` reports process liveness only. It does not prove database readiness, a
member grant, or successful device reading. This mode is not the hierarchical
write/runtime-v7 deployment. Provisioning that deployment remains a separate
owner-reviewed operation.

Record the source revision, process IDs, verified certificate fingerprint and
probe results. Stop the recorded receiver gracefully when requested; stop a
dedicated database only after verifying ownership and absence of other users.
No launch agent or restart-on-login service is created by this factory.

The separately reviewed [LAN standing-access v8 factory](lan-access.md) admits
its own closed, guarded inbox/write surface. This read-only factory does not
select or enable it automatically.
