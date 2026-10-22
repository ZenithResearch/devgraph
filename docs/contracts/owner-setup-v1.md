# Owner setup plans in Transfer

`zenith.devgraph-owner-setup.v1` is a reviewed **configuration proposal**, with
`status: prepared_not_applied`. It is not an owner credential, a signed owner
bootstrap, a member grant, or an instruction to start a service.

Transfer exposes this flow for the actual configured local DevGraph on 8080:

1. Discover the stable public resource ID, independently match it to the private
   configured instance registration, and verify the real service is healthy.
2. Unlock the existing Transfer Wallet to select its public identity as the
   proposed owner/operator. No other Wallet is created or imported implicitly.
3. Set the permissions that owner may grant: read, Contributor, Project Manager,
   or individual custom Work operations. Owner grant administration and the
   owner's own Work execution permission remain separate.
4. Explicitly select global scope or one or more real Arena/Initiative/Project
   paths. Selecting a parent includes current and future descendants at access;
   moving an object outside the selected path removes that path's authority.
5. Select Until revoked or a dated maximum, a separate local/private-LAN HTTPS
   Wallet endpoint, and whether to publish hierarchy names for request pickers.
6. Review the exact Wallet, resource identity, operations, paths, duration and
   setup digest, then save the plan privately with compare-and-swap. A locked or
   changed Wallet/local configuration requires renewed review.

## Wire and storage

The closed object contains `schema`, `status`, `resource_id`, `resource_name`,
`local_endpoint`, `registration_sha256`, `configuration_sha256`,
`owner_public_key`, `wallet_endpoint`, `preset`, `write_operations`, `scope_mode`,
`paths`, `not_before`, optional `not_after`, and `publish_hierarchy`.

Each path node contains only `kind`, `resource_id`, `title`, `path`. The path
contains the real typed Arena → Initiative → Project anchors up to the selected
container. Scope mode is explicitly `global` with no paths or `paths` with a
nonempty normalized union. Duplicate/redundant branches, duplicate decoded JSON
fields, integral float/bool timestamps, unknown fields and oversized data are
rejected. A missing or empty path selection never becomes global implicitly.
The document is at most 64 KiB; at most 16 selected branches and 12 Work write
operations are accepted. The shared fixture pins canonical bytes/digests across
Swift and Python. Names and ancestry remain data, not executable instructions.

Transfer retains these plans under its private application-support `OwnerSetups`
directory. It does not put a credential or claimed owner into the public
`~/.zenith/resources/v1` discovery record. The existing resource UUID and v1
record remain unchanged. No Wallet seed, read bearer, database password, issued
credential or signing token is stored in a setup plan.

## Production boundary

The new editor reads only the installed CLI's fixed local status and bounded
hierarchy queries. It cannot select an executable, a remote private API, a data
root, a shell command or an environment-provided credential. The configured
instance and public resource must agree; a test saved connection on another
port cannot enter local owner setup.

`devgraph.ops.owner_setup.decode_plan` validates this contract.
`compile_operator_policy` builds the proposed standing operator ceiling without
IO or active member assignments. The server issuer derives from stable resource
identity. Publishing a global hierarchy additionally requires an explicit,
current Arena-root inventory from the installer; it cannot silently invent or
publish test paths. The currently supported Work scope is Initiative, Project,
Issue and Task; proposal lifecycle, separate evidence permissions and access
administration are outside the Work presets.

## Remaining installation gate

Saving the plan is deliberately reported as **not installed**. Production
activation still needs a supported local installer: independently re-read the
instance/configuration fingerprints and selected current paths, verify native
compatibility, preserve existing rules/key/replay, prepare the actual TLS and
issuer material, show the exact installation, and obtain final approval. The
installer must not trust a review file or imported public Wallet key as proof
of ownership. Initial local setup requires the actual OS owner's authority;
subsequent policy edits must use admitted owner authority and exact current
policy preconditions. Neither discovery nor a portable review file can replace
that authorization. Portable admin credentials and owner-signed discovery are
separate protocols, not implied by this editor.
