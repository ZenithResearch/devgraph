# Presets, dynamic paths and durable grant activation

The opt-in `devgraph.signed-read-runtime.v7` composes the resource-path enforcement
with a public catalog, Wallet-consented requests, an authenticated operator inbox,
and persistent activation. Older runtimes retain their original closed decoders.
No existing private configuration is upgraded by loading this source.

## Ownership and discovery

`devgraph.access-operator-policy.v3` contains the owner-configured authority
ceiling (`devgraph.access-draft.v4`), permission templates and `published_paths`.
Each publication has a typed `resource_path` and a `discover_descendants` flag,
which defaults to false. An empty publication list discloses no hierarchy.
Publishing a Project does not reveal its hidden ancestors. Authenticated operators
see the scope inventory available under their configured read ceiling. Discovery
is bounded and filters before pagination; failure never means unrestricted scope.

GET `/devgraph/access-requests/v2/catalog` requires the configured HTTPS origin,
accepts no bearer credential or query, and returns no-store public metadata.
The catalog digest covers the templates, revision, receiver, issuer, publications
and policy digest. Live scope rows are separate metadata. Browser and native
clients verify that digest and bind requests to this origin and catalog version.
`catalog.example.json` is a synthetic cross-language Unicode fixture, not host
configuration or a deployable grant.

## User and operator flow

1. Connect Wallet, choose Contributor, Project Manager or No preset, then choose
   published Arena → Initiative → Project paths or leave scope to the operator.
2. Wallet signs `devgraph.access-request-action.v2`, containing an
   `access-intent.v2` preference. A request grants nothing and creates no group.
3. The operator authenticates to the inbox, chooses the preset and paths, and can
   narrow operations, kinds, explicit objects, Arena filters and expiry in Advanced.
   Requested preferences are not trusted authority. A stale catalog requires review
   against current templates.
4. Wallet reviews the exact selection and signs the final decision. Read action v4
   activates an installation-bound read grant. Write action v5 activates a
   holder-bound write grant. Mixed v5 binds both previous assignment digests and
   activates both in one database transaction, or neither. Native secS admission and
   pinned receiver policy must already permit the write; the UI cannot create it.
5. Wallet obtains read credentials from the active assignment and reuses them while
   valid. Writes still require the applicable Wallet signing consent. New operator
   approval is needed for new authority, not each use. Combined revocation removes
   both current components atomically.

Every path anchor is checked at access time, against canonical containment. New
matching descendants are included; moved-out objects are excluded even when the
credential is reused. Several selected paths form a union; each path's anchors,
object filters, operations, expiry, current grant and revocation all intersect.

Contributor and Project Manager are editable templates bounded by owner policy.
They do not imply access administration, delegation, proposal acceptance, independent
workflow review, receipt forgery or bypassing completion/workflow gates. Presets
may be narrowed. No-expiry grants remain a separate protocol change: this runtime
requires an explicit finite policy/assignment deadline.

Unattached Work creation cannot prove containment in a selected path. The picker
therefore omits it from path-scoped writes and says so. Separate provenance records
also need explicit scopes. Atomic creation directly inside a permitted container
and provenance allocation are not implemented by this contract; it never widens
a path grant to make those operations succeed.

Original request/action bytes and IDs survive restart and unknown outcomes. Recovery
checks the original result before another mutation. A new access request is offered
only after a known terminal/current result, preserving the previous public request
in history. Matrix/Nostr notifications, groups, owner bootstrap, synchronization
and child delegation remain separate work.

## Compatibility and qualification

The new policy/grant/action models have separate schema files in this directory.
Old member assignments and pending actions decode through their original models;
the old signed bytes are not rewritten. Installing runtime v7 requires reviewing
private policy versions, preserving the inbox/enrollment databases, matching native
admission and issuer/receiver bindings, and qualifying a separate candidate before
changing installed services. The bounded public pilot still accepts only its
explicit read-only v5 composition; this source does not expose v7 publicly.

Tests: `tests/native_read/test_path_access_flow.py`, `tests/frontend/scope-picker.test.mjs`,
`tests/frontend/browser/preset_scope_smoke.mjs`, and ZenithKit's `AccessCatalogTests`
and `ReadContractTests`. The browser walkthrough uses synthetic responses; the
Python suite separately signs with generated identities and issues through a matched
secS fixture binary. These checks do not claim installed iPad-to-server qualification.
