# Compatibility v0.9.0

This release adds descriptive classes and predicates for Zenith's local
coordination space and public service registrations. All new classes have
runtimeLabel false. Runtime Work/Arena operation catalogs and constraints remain
byte-equivalent to v0.8.0 except ontology-version metadata. Existing ontology
release files, signing domains, grants, snapshots, graph storage and app data
remain unchanged; no consumer migration is required.

The filesystem descriptor is the separately versioned zenith.local-resource.v1
contract. Older clients can ignore the new vocabulary and retain their pinned
ontology bundle. Registry readers skip unsupported record versions/types and
never infer ownership or access. Public web publication, installed runtime
upgrade and owner bootstrap remain separate deployment actions.
