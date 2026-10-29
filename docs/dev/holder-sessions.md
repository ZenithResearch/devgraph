# Optional finite holder sessions

This source implements the separately versioned secS holder-session v1 profile.
It does not change existing signed v1/v2 grants, public grant issuance defaults,
installed applications, or production policies. A signed v2 parent is required;
v1 parents remain usable through the existing flow and are not silently converted.

The issuer's canonical service and registry own authorization. LAN, HTTPS and
Tailnet access points share the parent revision, revocation and replay state.
Observed browser origin or an explicitly reviewed native application context is
checked separately; a token's own origin claim is not trusted caller context.

## Authority and custody

Wallet Core keeps the reusable parent and identity key. It privately constructs
`secs.holder-session-prepare.v1`; the receiver returns an immutable retained
challenge. The Wallet reviews the exact scope, approving Wallet installation,
separate application installation/key, origin and hard expiry. The application
proves possession of its temporary key; Core signs a private authorization.

Exchange verifies both signatures and the current canonical parent, then issues
`secs.holder-session-credential.v1`. The application receives only that finite
issuer token. Each call/status uses a fresh temporary-key proof, including after
the root Wallet locks. No parent or root key is exported to application clients.
The native helper owns the application key; the local DevGraph broker does not.

Only `devgraph.work.get`, `devgraph.work.list`, `devgraph.arena.get` and
`devgraph.arena.list` are admitted in this profile. Null/omitted permissions mean
the trusted read catalogue intersected with the parent, including mixed parents;
an explicit array must remain exact or fail. It cannot authorize a write,
membership, owner command, renewal or onward delegation. Scope selectors retain
their existing exact-object/hierarchy and paired-effect semantics.

Expiry is fixed at preparation, bounded by parent, requested and policy limits.
Changing an undispatched draft requires a fresh temporary key and inner request
ID/review. Reusing an inner request ID with changed bytes fails. After possible
dispatch, preserve the original key and status proof: identical issued retries
return the original token without extending it, including after review deadline.
A late new exchange is rejected. Status never issues a token. Losing the key or
reaching expiry requires a new human-reviewed session, not silent fallback.

Each read checks native cryptography, current parent/revision/membership, shared
replay and the current graph scope. A canonical DELETE-journal read fence spans
disclosure so a concurrent committed revocation cannot be ignored.

## Explicit configuration and transports

The existing v11 read runtime adds optional `holder_session_policy_file`, absent
by default. A separate owner-private policy has this closed shape:

```json
{"schema":"devgraph.holder-session-policy.v1","enabled":true,"allowed_origins":["app://zenith-transfer"],"audience":"devgraph","max_ttl_seconds":300}
```

There is no automatic profile installation or policy promotion. All aliases must
select the same canonical provider/policy. Policy drift fails closed.

HTTP exposes GET `/holder-sessions/v1/capabilities` and POST `challenges`,
`exchange`, `status` beneath that base (also the retained `/devgraph` aliases).
Canonical access bindings retain the `/service/v1` prefix in returned paths.
Only these paths permit cookie-free extension transport CORS. Existing read,
operator and write transport boundaries are unchanged; exact TLS/Host and abuse
limits still apply. Unsupported configuration advertises `supported:false`.

Native registration is a separate
`zenith.holder-session-provider-registration.v1`, prepared through
`devgraph.local_holder_session_provider.prepare_registration`. It creates a new
private review directory, pinned source/interpreter/profile inventory and a
separate `castalia.holder-session-agent-profile.v1`. It opens no store and issues
no grant. It is application configuration, outside Wallet backup.

Launch the reviewed broker as `python -I scripts/holder_session_provider.py
--profile-file <absolute private profile>`. Its bounded private NDJSON command is:

```json
{"schema":"devgraph.holder-session-provider-command.v1","request_id":"11111111111111111111111111111111","client_id":"22222222222222222222222222222222","operation":"services","arguments":{}}
```

The only operations are `services:{}`, `prepare:{body}`, `exchange:{authorization}`,
`status:{proof}`, `execute:{presentation}`. Responses use
`devgraph.holder-session-provider-result.v1`, correlated request ID and
`ok/value` or a fixed `error`. An exchange error can mean an unknown committed
outcome; recover with original-key status. No raw errors/private payloads are
returned. `client_id` is correlation, not authentication. Transfer must bind its
client/opaque handles/helper process and fsync its dispatch journal. The broker
has no key custody, owner fallback, expiry-edit command, arbitrary target URL or
HTTP listener. EOF closes the composition, not server-side grant authority.

## Qualification and remaining client work

`holder-session-source-set.json` pins the canonical secS source and public vector.
The Actions `holder-sessions` lane builds both native authorities and runs the
receiver suites instead of silently skipping cryptographic tests. Source tests
cover native signatures, exact retry/status, alias revocation/replay, expiry,
scope filtering, configuration drift, cookie-free HTTP admission and registered
write rejection. The ordinary CI suite continues testing old grants unchanged.

For isolated client qualification only, run
`python -I tests/native_read/holder_session_fixture.py --directory <NEW private
directory> --holder-public-key <disposable Wallet public key> --holder-binary
<native secS binary> --read-binary <read binary> --origin <reviewed context>`.
It creates a random fixture issuer, public-holder v2 parent, canonical SQLite
registry and in-memory allowed/hidden Tasks. It never loads local host config,
Neo4j or a personal Wallet. Its JSON setup output names protected profile/parent
files. Reopen using `--profile-file <profile>` for actual private NDJSON, or add
`--https` for an ephemeral loopback HTTPS server and generated test certificate.
Trust only the reported certificate in the isolated test. A fixture-only `REVOKE`
file tests canonical revocation; no production route contains that control.

Native iOS temporary-key custody and full installed-app/Tailscale qualification
are separate work; macOS helper availability does not imply an iOS implementation.
Passing these tests is not production activation or evidence of live access.
