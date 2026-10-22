# Transfer owner setup v2 and permission recipes

`zenith.devgraph-owner-setup.v2` retains v1's identity, fingerprint, HTTPS,
validity and dynamic path fields. It requires explicit ordered `read_operations`
(list, get, relationships, or an empty list for a write-only ceiling). An empty
read/write selection is invalid. `preset_snapshot` optionally embeds one exact
`zenith.credential-preset.v1` revision. It must be unarchived and its operations
must exactly match the explicit setup selection; `preset` is then `custom`.
Unknown fields and duplicate JSON keys are rejected. Whole-plan canonical SHA256
includes the complete recipe and descriptive Seat association.

The v1 contract stays closed and unchanged: no v2 fields are accepted, its three
default read operations remain, and its historical canonical digests are stable.
A v2 consumer accepts v1 and v2. A v1-only consumer rejects v2 rather than silently
dropping the selected read scope or recipe. Swift and Python run the same
accepted/rejected fixture cases and compare canonical digests.

A recipe contains schema, preset_id, revision, name, description,
read_operations, write_operations, archived, and optional seat. Read/write choices
are ordered subsets of the supported DevGraph operations. Holder, resource paths,
validity and server identity are deliberately outside the reusable recipe.

The optional seat association contains seat_class_iri, seat_type_iri and label.
The class is exactly https://zenith-research.ca/ontology/terms/Seat. The seat type
is an absolute HTTPS or URN reference. Transfer can create a local type-reference
URN or reuse a saved one; that creates descriptive recipe metadata, not an
organization, appointment, directory service or verified external type record.
SeatType suggests CredentialPreset in the shared source ontology v0.10.0. A
Person or Agent can occupy a Seat through SeatAssignment. None of these directory
facts issue authority, and the policy compiler never makes them principals.

Transfer stores custom recipes separately under its private app support folder,
one coordinated 0600 history file per ID. Revisions append consecutively and a
stale writer cannot overwrite a newer revision. Built-ins are duplicated before
editing. Archive/restore saves another revision; older snapshots remain intact.
Limits: 128 local recipes, 32 revisions or 64 KiB per history file, 16 owner paths,
and the existing supported Initiative/Project/Issue/Task permission surface.

The compiler produces a proposed server policy, not active credentials. It does
not publish custom recipes to clients, install a production owner, register a
listener, create grants or add Seat graph labels. The installed server's operator
ceiling and the signed exact member decision remain the authority boundary.
Pending production activation must independently recheck local fingerprints,
identity custody, current native rules, paths, certificate and final approval.
