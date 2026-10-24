# Credential requests across HTTPS endpoints — roundtable plan

Date: 2026-10-23. Status: reviewed implementation plan, not an installed release.

The requirement is the same Wallet credential-request journey through every
explicitly enabled DevGraph endpoint: private Tailnet, LAN or public HTTPS.
Transport controls reachability. DevGraph policy, Wallet consent and secS still
control authority. Transfer remains the Mac owner client; DevGraph remains the
service and canonical graph. iOS implementation and installation belong to the
separate client lane and are outside this work.

## Repository review and corrections

Development belongs in `ZenithResearch/devgraph`. The remote repository review
found [PR18](https://github.com/ZenithResearch/devgraph/pull/18), the active Wallet
requests/standing grants/shared HTTPS candidate. Default-branch documentation is
older; neither `main` nor an installed artifact alone represents the combined work.

The endpoint working tree starts at `a69b9456b3993de2f5276e1a9e72c9e8dc015ac7`.
Its existing endpoint candidate is useful but **not installable against the current
owner service**:

- `endpoint_read_runtime.review_endpoint` lists runtime v9, while this checkout's
  `ReadRuntimeConfiguration` accepts only v1–v8. Owner-v9 and later local RPC
  sources exist in separately retained, hash-addressed installation artifacts.
- Factory tests substitute the signed runtime with a v8 stub. Passing route and
  signed-request tests does not prove real owner-v9 composition or issuance.
- The installed owner checks the original signed registration, exact operator
  policy digest and original receiver. Changing an origin or copying policy files
  cannot authorize a published endpoint.
- `AccessRequestInbox` binds a database to one `(issuer, receiver)`. Different
  origins cannot share that database by changing its configured receiver.

The earlier 3,036 backend and 291 frontend passes are source regression evidence,
not live acceptance. Reconcile the actual owner-v9 and RPC sources, tests and
Wallet/secS source pins into a reviewed immutable source set before packaging.
Do not build by copying a few new files over an older installed runtime.

## Roundtable decision

Architecture, credential-contract and rollout reviewers independently inspected
the source, then challenged a consolidated proposal in a second round. They
converged on **one graph/service owner with explicit endpoint bindings and
receiver-scoped authority** for the first release.

| Situation | First-release behavior |
| --- | --- |
| Same canonical HTTPS scheme, host and port reached through different transports | Same receiver, inbox, assignments and credential contract. No new grant follows from changing transport. |
| Different HTTPS origin, including a different port | Separate owner-authorized endpoint binding and receiver-scoped request/enrollment/grant state. |
| One owner managing several endpoints | One service-selected Transfer view aggregates endpoint-scoped inboxes and routes exact decisions to the selected partition. |
| A public request without a current grant | Request can become pending; protected work remains denied. |
| A credential issued for another origin | Reject it. No automatic portability, grant replication or implicit service-wide revocation promise. |

A single public HTTPS origin usable by Tailnet-connected users is simpler and
uses one partition, but it does not provide a private-only address. Support the
explicit multiple-endpoint case rather than silently replacing the private route
with a public one. Cross-origin credentials/service-wide grants are separate
future contracts; they are unnecessary to meet this requirement.

## Ownership and endpoint configuration

Preserve the graph resource UUID, original service instance, signed owner
registration, graph, original loopback interface and existing grants. Give each
published interface a stable endpoint ID under that service. Never reassign an
endpoint to a different graph, origin or authority partition; an origin change
creates a new binding.

Add a separately versioned, narrowly scoped endpoint-configuration authorization
and Wallet consent. The historical owner grant's four `access.*` operations do
not authorize deployment. The first configuration capability must be established
through the protected installation-owner channel plus explicit owner consent;
recognizing the same public key is insufficient. Keep this distinct from member
grant decisions and from first-owner registration.

The signed review must bind, at minimum:

- Resource/service/endpoint identity, active parent owner proof digest and
  applicable owner/policy revision.
- Exact HTTPS origin, issuer/key/audience pins, endpoint policy digest and
  unchanged-or-narrower permission ceiling. Receiver substitution is explicit;
  it cannot widen resource paths, operations, validity or native admission.
- Member route allowlist, catalogue/hierarchy visibility, requested private or
  public publication, upstream target and TLS/Host/termination configuration.
- Expected service and endpoint-registry configuration revisions, observed network
  configuration digest, durable action ID and exact before/after configuration.

Do not weaken the original owner validator to accept arbitrary origins. Add an
endpoint-policy validator that verifies the active original owner and new signed
binding, while preserving the original installation review byte-for-byte. Pin
the new endpoint's ceiling; a later parent-policy expansion cannot silently widen
existing assignments. Endpoint configuration creates no member grant.

Wire names and runtime version are to be allocated after reconciling the actual
owner/RPC source baseline. Use additive versioned records; preserve old readers,
signed bytes, registrations and stored requests. Do not reuse a closed schema
while changing its meaning.

## Inbox, requests and credentials

Retain the existing signed request → review → decision → activation → enrollment
→ Wallet acceptance flow and the existing receiver checks. Each distinct origin
uses its own receiver-bound inbox/enrollment/replay partition. This reuses current
authorization implementations rather than inventing a second grant model.

Transfer aggregation is a projection and routing layer. Its composite identity
must include endpoint ID, binding revision, request ID and request digest. It
obtains a fresh challenge from the selected partition, displays the full origin,
issuer, scope and validity, then submits the exact signed decision to that same
partition. Equal request/action IDs at different endpoints must not collide.
Status recovery retains its original endpoint and receiver.

Version protected local RPC to enumerate and select only owner-registered
endpoints under the already selected graph and service. Verify each partition's
operator admission independently; report partial/unavailable partitions accurately.
Never fan out to arbitrary discovered URLs, rewrite signed receivers or replace
exact instance/profile checks with a hostname/local-IP guess. The existing
loopback inbox does not automatically see requests received elsewhere.

Keep catalogue visibility owner-controlled. An unpublished hierarchy can still
offer a general request for operator selection. Restricted visibility and service
failure need distinct messages; a fallback request is never whole-graph authority.

Keep challenge expiry, pending-request retention, dated/until-revoked grant
validity, issued credential expiry, TLS validity, signer-key deadlines and listener
lifetime separate. Endpoint disabling must close challenge, submit, enrollment
and presentation paths, not merely hide discovery. Keep grants, audit, receipts
and revocation/replay records. Re-enabling requires explicit review and a defined
binding generation/issuance rule; do not accidentally revive credentials from a
disabled generation. Local legacy credentials keep their existing semantics.

## Network boundary and recovery

Use one bounded member-facing application per configured origin. Keep operator
administration and native service control on their separately authorized local
paths. Public/Tailnet headers, source IP and `.ts.net` names convey no Wallet
permission. Optional/required membership remains the resource policy's choice.

Settle and test the actual TLS termination/upstream Host contract before routing.
The present boundary requires HTTPS and an exact Host and ignores arbitrary
forwarded headers. An HTTP upstream or rewritten Host does not work unchanged.
Any trusted proxy mode needs an explicit narrowly scoped configuration and tests;
do not add global forwarded-header trust or certificate-warning bypasses.

Publication has prepare/review/commit/status phases. Immediately before dispatch,
compare the owner proof, policy, endpoint registry, service configuration and
network revisions. A changed owner, revocation or target invalidates unsigned or
undispatched plans. Reserve actions atomically across windows/processes and save
the exact original before dispatch. After timeout, reconcile that original action;
never create a new listener/action to try again. Already-dispatched work needs
original-status recovery even if configuration subsequently changes.

Readiness and exact route readback precede discovery publication. Partial routing
or failed readiness is a partial/failed result, never success. Roll back code or
the exact owned routing change without restoring old authority databases over
new decisions, replay records or revocation tombstones.

## Implementation order and acceptance gates

| Milestone | Responsible lane | Exit evidence |
| --- | --- | --- |
| 1. Reconcile source | DevGraph with Wallet/secS source owners | PR18 work plus actual owner-v9/RPC sources and tests in canonical source; immutable commit/tree, companion pins, retained contract snapshots, hashes and updated current-state evidence. Candidate review rejects unsupported runtime versions. |
| 2. Close relevant hardening findings | DevGraph, secS RPC and Mac Transfer | Reproduced operator capacity/status recovery, unsigned challenge admission, denial-audit exhaustion, process-tree deadline and cross-window journal/CAS regressions repaired or proven unreachable on final paths. Public aggregate quotas include shared signer/audit/graph resources and retained member-request capacity. |
| 3. Endpoint configuration contract | DevGraph host, secS and Wallet consent; Mac Transfer control | New narrow configuration consent, parent-owner and ceiling validation, stable endpoint registry, CAS and durable original-action status; original owner registration and loopback operation preserved. |
| 4. Endpoint-aware administration and member routes | DevGraph and Mac Transfer | Protected endpoint enumeration/routing, composite inbox identities, per-origin stores, generic HTTPS discovery and request boundary. Browser contracts retained; no iOS release is implied. |
| 5. Matched integration and staging | DevGraph/companion qualification | Real owner runtime, exact native read authority and Wallet fixtures; complete request/approve/enroll/accept/read/revoke cycle through two origins, including failures and restart recovery. Critical factory tests use the real composition. |
| 6. Reviewed live cutover | Mac host and human owner/requester | Exact origin/target/TLS/route diff, owner configuration signature, health/readback, then separate narrow member approval and actual Wallet acceptance. Repeat through the selected public HTTPS route from outside the Tailnet. |

The recorded security findings are DG-01/02/03, RPC-001 and SWIFT-001 in the
2026-10-22 security roundtable. Existing shared Wallet state alone does not prove
atomic action-journal safety. Hiding public operator routes may remove remote
reachability for a particular finding, but does not repair owner-side capacity
or recovery. Record final-source repair evidence rather than claiming blanket
closure from passing happy-path tests.

The test matrix must include: original owner proof unchanged; wrong owner/resource,
stale binding/policy and tampered origin/pin rejection; public and private pending
requests; no-grant and excluded-scope denial; same-origin route parity; different-
origin challenge/proof/status/credential substitution rejection; equal IDs across
partitions; native partial admission and lost-response recovery; concurrent
configuration/decision/revoke; unexpired credential rejection after grant or
endpoint revocation; legacy local use; unpublished-catalog fallback; TLS/Host/
Origin/proxy spoofing; and sustained capacity/recovery across endpoints.

## Scope and handoffs

The Mac lane owns receiver binding, bounded member routes, existing-listener
cutover and DevGraph deployment evidence. Transfer and native Wallet/secS lanes
receive versioned consent/RPC contracts, fixtures and exact source/artifact pins.
The iOS lane receives only the public connection/request compatibility contract
and acceptance expectations; this plan makes no iOS source, install or URL changes.
Do not edit another lane's in-progress files or message its chat without separate
user authorization.

An existing private URL is not public. Source qualification can use synthetic
public origins, but live public acceptance requires an identified approved public
DevGraph origin. Preserve unrelated Serve/Funnel listeners and their other paths.
Do not create a new Funnel or convert an occupied listener's visibility as part
of credential-request enablement. If a public route is unavailable, report that
specific deployment gap separately from protocol implementation.

The first-release completion claim is: users can request and redeem access through
each enabled endpoint, and the existing owner manages endpoint-scoped decisions.
It requires exact installed-artifact and live requester evidence; source test
totals and an exported publication review do not establish it.

## Source references

- [Owner/access release integration](coordinated-wallet-release-20261021.md) and
  [exact source-set metadata](generic-wallet-source-set.json).
- [Inbox receiver binding](../../src/devgraph/auth/access_requests.py),
  [operator challenge/decision](../../src/devgraph/auth/access_operator.py),
  [standing grants](../../src/devgraph/auth/standing_access.py).
- [Current runtime parser](../../src/devgraph/signed_read_runtime.py),
  [candidate endpoint factory](../../src/devgraph/endpoint_read_runtime.py),
  [candidate tests](../../tests/native_read/test_endpoint_read_runtime.py).
- [Browser request/recovery](../../src/devgraph/frontend/static/credential-v2/access-request.mjs),
  [credential acceptance](../../src/devgraph/frontend/static/credential-v2/read-access.mjs),
  [HTTPS boundary](../../src/devgraph/public_read_runtime.py).

Private installation/audit inventories, exact deployment addresses and the
roundtable record are retained in the task workspace, not copied into public
source documentation.
