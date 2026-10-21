# Local resource discovery v1

DevGraph publishes advisory, public service metadata for local Zenith apps under
`~/.zenith/resources/v1/<resource_id>.json`. Transfer reads this directory on
launch, app activation and explicit refresh. It does not scan the network, probe
ports, read `.zenith/config.json` or inspect `.zenith` Wallet databases.

## Register an instance

```sh
devgraph local resource register --name "My DevGraph"
# Optional: publish the instance's reviewed HTTPS Wallet connection settings.
devgraph local resource register --connection-file /absolute/public/connection.json --replace
# For an explicitly selected instance configuration:
devgraph local resource register --config /absolute/instance/local.json --name "Another DevGraph"
```

The configured instance must already exist. Registration writes a separate
`resource-registration.json` beside its existing local configuration; it does not
rewrite the configuration, move data, start listeners or provision authority.
A UUID is persisted before publication and reused on retries and endpoint changes.
Changing advertised settings requires `--replace`; the ID remains the same.

After registration, successful `devgraph local start` and `restart` republish the
same record. A failed start does not publish. A discovery-write failure is
reported separately and does not claim that a healthy server failed to start.
The record persists across shutdown/reboot and is **configured-service metadata**,
not a live heartbeat. Direct launchd boot does not refresh its timestamp; apps
must not infer current health from that timestamp. This is additive and opt-in:
existing installations and CLI starts without registration remain compatible.

## Shared public record

The closed `zenith.local-resource.v1` object has exactly `schema`, `resource_id`,
`kind`, `name`, `endpoint`, `connection` and `updated_at`. `resource_id` is a
lowercase canonical UUID, independent of Wallet identity, service signing keys
and member installations. `kind` is `devgraph` for the first adapter. `name` is
nonblank, at most 80 Unicode scalars with no control characters. `updated_at` is
an unsigned JSON integer in UTC Unix seconds, at most 9007199254740991.

`endpoint` is an origin with no userinfo, query, fragment or trailing slash.
HTTPS is supported; HTTP is permitted only for `127.0.0.1`, `::1` or `localhost`.
A basic local host advertises its existing `http://127.0.0.1:8080` monitor with
`connection: null`. This does **not** make its bearer-based API Wallet-ready.
Transfer shows that connection setup is needed. Wallet management requires an
independently configured HTTPS receiver and public connection profile.

When present, `connection` uses the existing closed native Work connection
shape: name, HTTPS receiver, issuer, read authority pin, optional LAN/Tailscale
network and separately reviewed LAN TLS fingerprint. Its name and receiver
must equal the record's name and endpoint. It contains no credential, private
key, local bearer, executable, arbitrary file reference or secret environment.

The directory chain is owned by the current user with 0700 permissions, and
records are regular 0600 files, limited to 32 KiB and 100 records per directory.
Writers preserve unrelated records and existing `.zenith` configuration. Readers
hold directory descriptors, reject links and unsafe ownership/permissions,
strictly parse duplicate/float/unknown-field input, match UUID filenames and skip
invalid/unsupported records while retaining valid ones. Unsupported newer
versions/types do not silently become DevGraph configurations.

The same 22 acceptance/rejection vectors are shared with Transfer's Swift tests
in `tests/fixtures/zenith-local-resource-v1.json`. The public example is
`tests/fixtures/zenith-local-resource-example.json`.

## Trust and compatibility

Local publication is discovery only, not signed ownership, issuer approval,
operator admission, a grant or evidence that the process is running. Transfer
shows discovered services separately from saved connections and requires review
before saving a connection. It never silently replaces saved issuer/TLS pins.
The receiver still verifies current Wallet proofs and operator policy.

The existing public HTTPS connection-file contract, Transfer's saved connections,
local host config, encrypted Wallet snapshot and all grant/credential bytes are
unchanged. Owner-signed resource descriptors and Hub/secS adapters are future
contracts; arbitrary registered ports are not a management interface.
