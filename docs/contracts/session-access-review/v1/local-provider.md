# Local provider registration review

`scripts/prepare_local_session_registration.py` prepares NEW owner-private review
files; it neither installs a registration nor starts a process, opens graph stores,
reads issuer-key bytes, signs, issues a grant or changes authority. Required flags:
`--host-config`, `--runtime-config`, `--session-binary`, `--interpreter`,
`--directory`; optional `--name`. Paths are application configuration, never MCP
arguments or Wallet backup records. Preserve the invoking venv interpreter path.

The selected runtime must be an explicitly configured v11 canonical provider with
optional community membership. Effective provider selection follows the existing
explicit holder-provider reference before publication-parent provenance. A route
alias pointing to a different provider registry is refused. This slice does not invent a
membership proof or override required eligibility for an ephemeral identity.

The output `profile.json` uses `devgraph.local-session-provider.v1`: production
mode, explicit session grant schema v2, public canonical provider/receiver pins, bounded session lifetime, hashed
host/runtime config references and hashed native session helper. `provider.json`
matches `zenith.session-provider-registration.v1`: public pins, canonical profile
digest and exact interpreter/script/profile/application-source file hashes.
`profile_digest_sha256` hashes canonical profile content; launch file hashes bind
exact file bytes. No secret/token/private key belongs in either file.

Only a separately reviewed installation into Transfer's owner-private
`SessionReviews/provider.json` enables it. Missing/changed files fail closed.
The native consumer validates an exact HTTPS origin, advertised mode and trusted
provider/profile pins. Application source pins and the qualified dependency lock
are drift fences, not proof of immutable third-party site-packages or OS custody.

The pinned entry script compiles its bootstrap directly from source before any
DevGraph import. First-party imports then compile regular, non-writable-by-others
Python source, refusing cached bytecode, native aliases and sourceless modules.
A fresh private cache prefix prevents inherited caches from being selected by
later imports; bytecode writes are disabled. The registration pins all first-party
resource files as well as source and the invoking venv's `pyvenv.cfg` when present.
This does not make third-party dependencies immutable or establish OS isolation.

On explicit launch the private process validates pins again and reuses the
existing signed-read composition without listening on HTTP. It uses the canonical
operator, assignment inbox, root registry, issuer-native tools, receiver, current
policy/revocation checks, audit log and transaction enforcement. No separate
authorization database, verifier fallback, trusted-localhost exception, native
policy edit or new provider identity is introduced. Small session review/challenge
tables live alongside the canonical inbox. Existing signed formats and records
are retained. Normal typed graph projections remain behind signed scoped calls.

The ordinary host's v1/v2 default issuance setting is retained. The private
process explicitly uses v2 for its new session assignments, with the exact same
issuer, binary, provider/route registry and service; existing roots are never
converted or rewritten. A synthetic native selector probe fails startup if the
configured native tool predates scoped verification. Every access point must use
a qualified dual-version verifier before live activation. Updating old native
binary references and restarting existing Wallet services is a distinct reviewed
prerequisite; installing only a descriptor is insufficient.

Local host storage settings are derived inside that server composition. Neither
MCP nor the app receives database credentials. Public HTTPS, Tailnet and local
routes continue to refer to the same canonical provider. No discovery URL can
select a registration, interpreter, source tree or issuer key.

Approval is still personal in native Wallet UI and binds exact scope/expiry and
current policy. Session keys stay in the finite Rust custody child; the owner
Wallet may then lock while that session operates. This is inherited-pipe custody,
not active XPC isolation. Closing the adapter destroys its keys and releases its
slot, preserves the original outcome and does not claim server-side revocation.
Unknown outcomes are reconciled, never silently reissued after lost custody.

Current execution is read-only. Typed Work calls use the existing get/list
handler; path-based Work/Arena views use the existing Wallet monitor handler.
Neither handler accepts arbitrary queries or caller-authored ancestry. Exact
permission round-trip rules can reject a request whose operation subset is not
representable by an existing assignment; it is never broadened automatically.
