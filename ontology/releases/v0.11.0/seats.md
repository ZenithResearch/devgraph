# Organizational seats and credential presets

`Seat` is Zenith's shared primitive for a position in an organization, inspired
by the user's EOS seat model. Its canonical IRI is
`https://zenith-research.ca/ontology/terms/Seat`. It is independent of the Person
or Agent who occupies it. A vacant seat is valid and does not need a fake actor.

`SeatType` is a reusable classification/template for seats, such as a project
management seat or contributor seat. It is not a Wallet principal. A `Seat` has a
`SeatType`; a dated `SeatAssignment` places one `Person` or `Agent` in one seat.
An Organization can contain seats but cannot occupy one. Capacity, overlapping
appointments, reporting structures and assignment approval remain organizational
policy, not new receiver authorization rules. Reports-to is not permission
inheritance. These are shared Zenith terms, not DevGraph-specific copies.

`CredentialPreset` identifies a reusable permission recipe.
`CredentialPresetRevision` identifies its exact immutable permissions and optional
SeatType association. Seat types may suggest multiple presets and a preset may
be suggested by multiple seat types. Transfer's initial local recipe UI stores
one optional seat-type reference per recipe revision. It does not claim the
organization's seat catalog or assignment runtime already exists.

Grant selection combines an exact preset revision, holder Wallet, service,
resource predicates, validity and the operator's signed decision. The receiver
checks that selection against its own policy ceiling. Selecting a parent path
covers current resources below that path at access time; future descendants enter
and moved-out resources leave the scope. This is independent of seat assignment.
Editing a recipe never mutates existing grants. Replacing or revoking a grant
requires its own authorized server decision. Seat assignment, membership,
reporting lines and ontology statements alone confer no permissions.

All five classes have `runtimeLabel: false`. This release adds no Work kinds,
appointment route, graph labels, Neo4j migrations, identity creation or authority
provisioning. The existing review-only `directory.ScopedPost` and `PostAssignment`
drafts are candidates for alignment with `Seat` and `SeatAssignment`. They are
not asserted owl:equivalentClass and their stored records are not rewritten.

Transfer uses the shared Seat class IRI and an explicit SeatType instance IRI in
`zenith.credential-preset.v1`. Links remain descriptive; external references are
not fetched or trusted automatically. The recipe is snapshotted into a reviewed
`zenith.devgraph-owner-setup.v2` plan, which is still prepared_not_applied. A plan
must pass the separate owner installation, identity and policy review boundary.
