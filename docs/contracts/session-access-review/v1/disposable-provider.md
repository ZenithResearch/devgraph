# Private session qualification provider

This is an opt-in developer qualification host, not an installed DevGraph mode.
It creates a new synthetic issuer, an ordinary issuer-approved read assignment
and two in-memory Tasks. It never reads installed configuration, real Wallets or
Neo4j. No HTTP route, public listener, production policy or grant is changed.

`scripts/create_disposable_session_profile.py --help` creates a NEW private
directory. Supply a disposable owner's public key and explicitly pinned native
session, holder-authority and read-authority binaries. The private profile records
binary hashes. Do not reuse a real owner or move fixture state into production.
Launch `python -I scripts/session_agent_service.py --profile-file <profile>`.

The private inherited NDJSON wire is `devgraph.session-provider-command.v1`:
`request_id`, `client_id`, `operation`, `arguments`, plus `schema`. Identifiers
are 32 lowercase hexadecimal characters. Frames are bounded to 262144 bytes.
The configured broker authenticates its caller; client IDs alone are not auth.

Operations are `services`, `prepare`, `update_expiry`, `owner_challenge`,
`submit`, `reconcile` and `execute`. No model-facing approve/sign endpoint exists.
`prepare` accepts a configured canonical service, original request ID, exact
secS v2 permissions, display label/purpose and exactly one of `expires_at` or
`lifetime_seconds`. Relative lifetime is 60–3600 seconds, anchored after native
identity preparation; retries retain the original intent and review. Expiry
changes increment the review revision and invalidate previous owner approvals.

The owner challenge contains the complete manifest and digest at
`details.session_review`. Existing Core challenge TTL remains exactly 120 seconds;
the native UI and provider also enforce the earlier manifest review deadline.
`submit` requires a genuine owner proof; this activates an ordinary assignment,
then the fresh native session identity proves possession and receives its own
issuer-signed secS v2 root. Existing member roots cannot be converted to it.

`reconcile` recovers the exact original root from the canonical registry, never
creates a new identity or retries ambiguous issuance. A lost helper fails closed.
`execute` accepts an opaque credential handle and exact call; internal native
proofs stay inside the broker/provider. Only bounded business results return.
Current revocation, replay, selector and transaction checks use the existing
signed-read handler. No public credential, issuer-key or proof is returned by MCP.

The private `close_client` operation takes empty arguments and only the broker's
bound client ID. On adapter EOF/finally it terminates that client's helper,
destroys its ephemeral keys and releases its slot. Repeating it succeeds with
`{schema:"zenith.session-client-closed.v1",closed:true,server_revocation_confirmed:false}`.
Old handles become unusable; recovering them never generates replacement keys.
Other clients, root/assignment records and retained review history stay intact.
It is not a public MCP tool, a signing action or an issuer-side revocation.

This first adapter supports finite fixed-ID Work `get`/`list` only. It refuses
modern Arena/path/standing profiles rather than flattening their selectors or
guards. Writes are unavailable. Production profile integration requires its own
lossless mapping, tests and review; successful fixture tests do not qualify it.

## Modern profile mapping qualification

`auth/session_read_profile.py` is the source-only mapping primitive for the next
adapter slice. It uses the trusted configured provisioning decoder, selection
type and read compiler. The complete shared secS permission list must round-trip
exactly, including roles, selectors and legacy constraints; no ancestry is inferred
from a client, no different grants are combined and no extra operations are added.
Identical constraint rows for get/list may be deduplicated into one typed grant.
The owner-only v7 All ceiling projects only to ordinary explicit v8 member All;
earlier fixed-ID parsers retain their closed formats. Every session assignment is
dated, independently holder/installation-bound and read-only. A ceiling's preset
reference is not copied as requester provenance.

Pure mapping tests cover fixed/dynamic Arena reads, original ceiling byte
preservation, retained hierarchy ancestry and rejection of changed service,
broader selectors, extra roles, writes and fixed-to-dynamic amplification. A
single Arena get permission is currently unrepresentable by the existing draft's
get/list pair and is rejected rather than receiving extra list authority. These
tests do not enable the modern adapter in the private process or qualify actual
production issuance/execution. That integration remains a distinct next step.

Custody is an ephemeral Rust child using inherited private pipes, not XPC or a
same-user security boundary. Native exchanges time out after ten seconds and
close the child on corrupt, oversized or stalled responses. There are at most
eight clients, eight session identities per helper, 128 retained reviews and
256 retained native outcomes/challenges. Capacity exhaustion fails closed.
These prototype limits and in-memory custody are not durable session recovery.
Closing custody stops future signing but does not claim issuer-side revocation.

Compatibility: existing signed formats, roots, fixed-ID grants and legacy HTTP
handlers remain unchanged. Session review metadata uses separate tables in the
explicitly selected private inbox; it is not a membership or Seat definition.
