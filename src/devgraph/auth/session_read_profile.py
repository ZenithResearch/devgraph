"""Lossless mapping to existing typed provider assignments, not a new grant wire.

Shared secS permissions remain the reviewed contract. This DevGraph adapter uses
the configured provider's decoder/compiler and rejects any mapping that changes
roles, selectors, operation identity or legacy constraints. No runtime registers
this adapter automatically; writes and owner-native admission are out of scope.
"""

import json

from devgraph.auth.access_operator import GrantSelection
from devgraph.auth.access_policy import require_subset
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.grant_validity import GrantValidity
from devgraph.auth.selector_grants import permission_v2
from devgraph.auth.signed_read import SecsReadAuthority

SUPPORTED = {"devgraph.access-draft.v" + str(v) for v in (2, 4, 5, 6, 7, 8)}


def read_session_permissions(draft, compile_read, issuer_pin):
    compiled = compile_read(draft)
    authority = SecsReadAuthority.policy_for(
        compiled, key_id=issuer_pin["key_id"], public_key=issuer_pin["public_key"]
    )
    return [permission_v2(p, issuer_pin["service"]) for p in authority["permissions"]]


def build_read_session_selection(
    *,
    ceiling,
    provisioning,
    compile_read,
    issuer_pin,
    identity,
    permissions,
    not_before,
    expires_at,
):
    if ceiling.schema_ not in SUPPORTED:
        raise ValueError("unsupported_session_policy_profile")
    validity = GrantValidity.dated(not_before, expires_at)
    if not isinstance(permissions, list) or not 1 <= len(permissions) <= 32:
        raise ValueError("invalid_session_permissions")
    grants = []
    for permission in permissions:
        if (
            not isinstance(permission, dict)
            or set(permission) != {"operation", "resource", "roles", "constraints"}
            or not isinstance(permission["constraints"], dict)
        ):
            raise ValueError("unsupported_session_permission")
        # Identical constraint rows often represent separate get/list permissions.
        # Do not merge different rows, infer ancestry or synthesize operation sets.
        constraints = permission["constraints"]
        if constraints not in grants:
            grants.append(constraints)
    raw = json.loads(canonical_json(ceiling))
    # v7 is an owner-only ceiling. Its explicit All projects into the ordinary
    # member v8 draft; neither earlier fixed-ID parser acquires All semantics.
    if raw["schema"] == "devgraph.access-draft.v7":
        raw["schema"] = "devgraph.access-draft.v8"
    raw.update(
        subject_public_key=identity["public_key"],
        installation_id=identity["installation_id"],
        lifetime_seconds=60,
        read_grants=grants,
        write_grants=[],
        previous_digest=ceiling.digest,
    )
    if "preset" in raw:
        # A ceiling's preset reference is not a requester selection/provenance.
        raw["preset"] = None
    if "validity" in raw:
        raw["validity"] = json.loads(validity.to_json())
    else:
        raw.update(not_before=not_before, not_after=expires_at)
    draft = provisioning.decode_draft(canonical_json(raw))
    require_subset(draft, ceiling)
    if canonical_json(read_session_permissions(draft, compile_read, issuer_pin)) != canonical_json(
        permissions
    ):
        raise ValueError("unsupported_session_selector_mapping")
    selection_type = getattr(provisioning, "selection_type", GrantSelection)
    selection = selection_type.model_validate(
        dict(read=json.loads(canonical_json(draft)), write=None)
    )
    if (
        selection.write is not None
        or selection.read.write_grants
        or canonical_json(selection.read) != canonical_json(draft)
    ):
        raise ValueError("unsupported_session_assignment_mapping")
    return selection
