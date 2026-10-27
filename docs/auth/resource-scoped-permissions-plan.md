# Resource-scoped credentials: editor change, coverage audit and completion plan

Status: implementation candidate, 2026-10-27. Transfer supports independent
action/scope rules and all 20 named mutations when offered by the current policy.
The receiver accepts the existing wire shapes; no installed policy, member grant
or default owner authority is changed. Broader read families, generic profile
SDK generation and deployment qualification remain separate work.

## Target experience

Choose the canonical service, recipient and optional preset; then configure
Scope, Read permissions, Write permissions and Validity. Arena records and Work
are resource types within Scope. Reading an Arena record never implies access
to all Work in that Arena. A Project can expose its containing Arena's record
without exposing sibling Work. Review displays the exact resulting rules.

Each editor rule has its own hierarchy or exact-ID selector. Arena metadata IDs
must lie within its verified Arena anchors; a standalone Initiative/Project does
not imply an unknown Arena. Selecting all permitted resources is explicit.
Arena list/get remain a bundled operation in the current assignment contract,
and member Arena IDs remain explicit snapshots. New Arena records do not become
readable automatically. Existing Work-path grants continue including matching
future descendants and lose moved-out Work at authorization time.

## What the audit actually found

Run `python3 scripts/audit-devgraph-operation-coverage.py --devgraph /path/to/canonical/devgraph --report /path/to/report.json`.
The script reads the canonical producer constants and the latest ontology without
loading a server, accessing Wallet data or changing policy. It rejects a legacy
origin or a changed producer construction requiring a new audit adapter. Its
`complete` flag refers to named mutations in the native recipient editor, not to
all routes, live admission or end-to-end deployment.

| Operation family | Current native recipient editor | Remaining work |
|---|---|---|
| Work reads: list, get, relationships, children, blockers | Supported when offered by the policy; presets normally select the first three | Per-rule read scope and relationship-type controls |
| Arena reads: list, get | Supported as a combined explicit-ID grant | Independently selectable operations need a coordinated contract |
| Work writes: create, patch, progress.set, dependency.add/remove, blocker.add/remove, parent.set, workflow.assign/transition, archive, restore | Supported in independent rules when admitted | Creation destinations need a new receiver contract; use exact IDs or explicit all-permitted coverage today |
| Work writes: accept, convert, proposal.reject, workflow.review | Supported with explicit resource review | Proposal/decision/provenance resources require exact IDs or explicit all-permitted coverage, separately from hierarchical Work |
| Arena writes: create, patch, archive, member.set | Supported as ordinary member rule actions when admitted | Selected hierarchy anchors become exact Arena record IDs; both endpoints and before/after state remain receiver-checked |
| Graph/topology, observations, material, documents, bounded query | Separate delegated-read scopes/routes exist | Reconcile each receiver's accepted scope with the native grant/holder contracts; do not infer these rights from Work read |
| Internal/redacted/public-safe exports | Ontology declares separate export scopes | Confirm receiver availability and filter/redaction boundary before making selectable |
| Kanban/workflow metadata and legacy aliases | Present in the ontology/private API | Map aliases to canonical semantic actions without minting broad private API capabilities |
| Credential administration, owner registration, deployment/network configuration | Separate administrative protocols | Keep explicit administrative resources/actions, distinct from ordinary Work access |
| Health/discovery and status/idempotency recovery | Public metadata or original authenticated action protocols | Do not present as blanket data-access privileges |

The canonical named mutation set has 16 Work v2 actions and four Arena v1 actions;
Transfer accepts all 20 in recipient rules, separately from the unchanged built-in recipes. The audit captures
all 42 entries in ontology v0.10.0 as well. Those entries mix current operations,
legacy aliases and metadata: their existence is not proof of exposed capability.
Legacy `status` remains a v1 alias; v2 progress and workflow transitions retain
separate semantics. Progress/review operations may also require ReviewPacket,
Handoff or ExternalLink resources, and proposal decisions touch Decision/Issue
resources. A checked Work action alone does not provide those permissions.

## Convention and vocabulary

Use resource-scoped, default-deny authorization expressed as action/resource
rules. ABAC supplies the general subject/resource/action/context model;
relationship-based hierarchy membership supplies the resource predicate.
Presets and future Seat types are recipes for rules, not runtime authority.
This applies the model described by [NIST SP 800-162](https://csrc.nist.gov/pubs/sp/800/162/upd2/final)
to the existing provider-signed, holder-bound grants; it is not a new bearer or
an RBAC-only replacement.

Canonical terms:

- **Service**: stable provider/service identity. URLs and local RPC sockets are access points.
- **Subject**: verified Wallet identity; installation/origin restrictions are additional conditions.
- **Resource selector**: resource type plus exact IDs or verified hierarchy roots and descendant semantics.
- **Action**: stable, versioned semantic operation, independent of HTTP method or transport.
- **Permission rule**: an action set applied only to its own resource selector and conditions.
- **Grant**: approved provider-signed collection of rules with validity/revocation identity.
- **Derived credential**: same-holder equal/narrower proof; third-party delegation stays disabled.
- **Authorization decision**: secS validation plus current provider resource-policy evaluation, enforced at the receiver.

Example target rule matrix:

| Scope | Allowed actions |
|---|---|
| Arena A record | List/read this Arena record |
| Work under Arena A | List/details/relationships |
| Work under Project B in Arena A | Edit/progress |
| Work under Project C in Arena A | Read only, inherited from Arena A; no matching write rule |

No matching permission means deny. Do not union all actions and all roots into a
Cartesian product. Basic positive rules can express the example without an
explicit deny. Unchecking a child under an allowed parent is not an exclusion.
True exceptions to inherited access need a versioned, visible exclusion/deny
model, receiver-wide precedence rules, and anti-amplification tests before UI.

## Implemented rule editor and verification

Transfer 52 can add up to 16 independent rules. Each pairs its own actions with
verified hierarchy branches, exact IDs, or explicit all-permitted coverage.
The compiler narrows each original parent rule independently, preserves opaque
conditions and exact recipients, deduplicates identical output rows, and enforces
16 read grants / 32 read permissions and 64 write grants. It rejects unsupported
selected operations instead of silently omitting them. One validity applies to
the whole reviewed assignment. Existing presets remain recipes for one rule;
built-in recipes are not expanded with new write actions.

All 20 Swift-produced mutation selections and a read-A/write-B selection pass
DevGraph's actual draft decoders. Synthetic signed receiver tests allow reads
under Arena A and writes under Project B while denying writes to A. Existing
move/edge/provenance and current-policy checks remain active. secS conformance
tests reject cross-rule action/scope substitution. Wallet Core 0.4.1 tests prove
that independent scopes survive encrypted grant backup/restore and same-holder
equal/narrower derivation. Signed formats and the native Core pin are unchanged;
no iOS/Chrome rebuild is needed for these shapes.

[Operation inventory](permission-operation-inventory.json) classifies every
current ontology entry; its tests fail if a named mutation or ontology entry is
added without a mapping. The inventory is documentation, never authority.

Installed policies may offer fewer than 20 mutations. The editor cannot expand
that ceiling. Deployment or owner-policy changes require a separately reviewed,
Wallet-signed decision. A selected action may touch several resources: authorizing
a Task alone does not authorize its new ReviewPacket, Decision or evidence link.
The current implementation supplies positive rules; no exclusion/deny editor or
implicit access outside the selected rules is introduced.

## Delivery sequence and remaining gates

1. **Immediate UI grouping (this change).** Put coverage and object types in Scope,
   read/write actions underneath, and derive Arena record coverage from selected
   anchors. Reject stale out-of-scope Arena IDs in the selection compiler, not
   only in the UI. Preserve signed originals, saved grants and current policies.
2. **Provider operation manifest and completeness gate.** Define the reusable
   authorization-profile contract in secS/Wallet Core; DevGraph is its first
   resource adapter rather than the owner of the generic authentication schema.
   DevGraph owns action
   IDs, resource types, applicable selectors, touched-resource rules and support
   versions. Expose a verified catalog through secS; Transfer renders it. Every
   operation must be mapped as selectable, administrative, public, legacy alias,
   or explicitly unsupported. CI fails on an unclassified new operation. Add
   the eight missing named mutations only after their complete resource and
   receiver semantics pass; do not silently expand built-in presets or owners.
3. **Independent scope per rule.** Replace the editor's flat choice with rule
   rows: choose resource scope, actions, then conditions for each row. Start with
   separate read/write coverage and allow further per-action rows. Existing
   drafts already contain arrays of individually scoped read/write grants, so
   compile representable rules into the existing wire schemas. Do not add a new
   schema merely for the visual layout. Reject combinations the current
   receiver cannot enforce; preserve each parent rule's own limits.
4. **Complete resource semantics.** Handle create against an authorized destination
   container and prevent unattached creation escaping a path. Check current and
   destination parents/Arenas for moves and Arena member.set. Check both ends of
   dependency/blocker links. Review and authorize every independently mutated
   provenance/decision resource; do not smuggle it into a Work-path wildcard.
   Current path-write grants admit only Initiative, Project, Issue and Task;
   Proposal, Todo and provenance records need explicit applicability rules rather
   than silently inheriting support from an object-kind checkbox.
   Model Arena record list/get separately, include-archived and relationship types
   explicitly. New data/action families need matched receiver contracts.
5. **Coordinated compatibility rollout.** Publish new shapes only where needed;
   update secS, DevGraph, public Wallet Core, Chrome, iOS and Transfer decoders and
   subset verification before issuance. Negotiate versions; older readers fail
   closed. Never relabel a signed origin, migrate old grants into broader rules,
   or rewrite uncertain request/replay journals. Production policy changes have
   their own exact review and Wallet signature.
6. **User qualification and security evidence.** Synthetic contract tests first,
   then actual local management and remote Tailscale reads/writes with a reviewed
   test recipient. Verify equal/narrower same-holder derivation, expiry,
   revocation, tampering, replay and same-service access-point equivalence. Check
   iPad/Chrome/Transfer read-A/write-B results and denial outside B; verify a
   parent grant cannot bypass a child exclusion when that feature is introduced.
   No existing grants are broadened as part of testing.

Required adversarial cases include Arena metadata without sibling Work, multiple
Arena selections, standalone Work, moved-out/new descendants, archived resources,
cross-boundary edges/moves, provenance writes, source-rule mixing, stale catalog
versions and an unsupported action on every transport. Add an effective-access
preview showing why each sample resource is allowed/denied. Audit records must
identify the grant/rule version and decision without recording secret keys or
whole private payloads.

## Canadian government and defence alignment

Use [ITSG-33](https://www.cyber.gc.ca/en/guidance/annex-3a-security-control-catalogue-itsg-33)
for risk-tailored system security controls. Use [ITSP.10.171](https://www.cyber.gc.ca/en/guidance/protecting-specified-information-non-government-canada-systems-and-organizations-itsp10171)
and its [assessment procedures](https://www.cyber.gc.ca/en/guidance/assessing-security-requirements-specified-information-itsp10171-01)
for the relevant non-GC supplier environment. Our proposed evidence mapping is:

| Control | Proposed evidence |
|---|---|
| 03.01.02 Access enforcement / ITSG-33 AC-3 | Deny tests at each receiver, action and resource boundary |
| 03.01.04 Separation of duties / AC-5 | Distinct data-edit, workflow-review and security-administration rules |
| 03.01.05 Least privilege / AC-6 | Exact selectors, independent action scopes and attenuation checks |
| 03.03.01–02 Audit / AU-2–3 | Grant/rule/decision records with versioned test evidence |

The [CPCSC program](https://www.canada.ca/en/public-services-procurement/services/industrial-security/security-requirements-contracting/cyber-security-certification-defence-suppliers-canada/program-overview.html)
is a relevant procurement target for defence suppliers. Its level and the
applicable controls depend on contract requirements. [PCTF](https://diacc.ca/trust-framework/)
is complementary identity/authentication assurance, not a substitute for system
access controls. This plan claims no conformance, assurance level, government
accreditation or military approval. Contract security screening and any applicable
controlled-goods requirements are separate procurement obligations.
