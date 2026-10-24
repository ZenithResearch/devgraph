"""Explicit host-owner publication of a second interface of a claimed graph.

This is an owner-private host configuration, never an HTTP bootstrap route.
The original signed owner registration stays authoritative. Publication may
change only the receiver address, not the operator identity or permission ceiling.
No existing member grant is copied to the new origin.
"""

import copy
from pathlib import Path

from devgraph.lan_read_runtime import lan_origin
from devgraph.local_resources import LocalResourceError, validate_connection
from devgraph.public_read_runtime import public_origin
from devgraph.signed_read_runtime import ReadRuntimeConfiguration, private_json


def endpoint_operator_policy(config, *, data_root):
    from devgraph.signed_read_runtime import owner_operator_policy

    if config.schema_ != "devgraph.signed-read-runtime.v10" or data_root is None:
        raise ValueError("explicit published endpoint required")
    parent = ReadRuntimeConfiguration.model_validate(private_json(config.owner_parent_runtime_file))
    if (
        parent.schema_ != "devgraph.signed-read-runtime.v9"
        or parent.owner_bootstrap_database_file is None
        or parent.owner_install_review_file is None
    ):
        raise ValueError("original signed owner runtime required")
    for field in (
        "owner_bootstrap_database_file",
        "owner_install_review_file",
        "issuer_key_file",
        "secs_binary",
        "secs_binary_sha256",
        "write_native_policy_file",
        "membership_requirement",
        "membership_trust_file",
        "revoked_memberships_file",
    ):
        if getattr(config, field) != getattr(parent, field):
            raise ValueError("published owner or issuer changed")
    # This validates the original signature-backed owner registry and unchanged
    # original policy on every administrative action and member enrollment.
    expected = copy.deepcopy(owner_operator_policy(parent, data_root=data_root))
    try:
        profile = validate_connection(private_json(config.owner_endpoint_profile_file))
    except LocalResourceError as error:
        raise ValueError("invalid published profile") from error
    origin = public_origin(profile["receiver"])
    if profile.get("network") == "lan":
        lan_origin(origin)
    elif profile.get("network") not in (None, "tailscale"):
        raise ValueError("explicit HTTPS member endpoint required")
    if origin == private_json(parent.enrollment_policy_file)["receiver"]:
        raise ValueError("a secondary endpoint requires a distinct receiver")
    expected["ceiling"]["receiver"] = origin
    actual = private_json(config.access_operator_policy_file)
    if actual != expected:
        raise ValueError("publication may not widen owner authority")
    for field in ("enrollment_policy_file", "authority_policy_file"):
        original = private_json(getattr(parent, field))
        published = private_json(getattr(config, field))
        original["receiver"] = origin
        if original != published:
            raise ValueError("publication may not widen enrollment authority")
    authority = private_json(config.authority_policy_file)
    pin = {k: authority[k] for k in ("issuer", "key_id", "public_key", "receiver", "audience")}
    if profile["issuer"] != authority["issuer"] or profile["authority"] != pin:
        raise ValueError("published issuer changed")
    # Origins must retain separate request/replay/member stores. A new address
    # is not authority to replay old grants or consume an old site's challenge.
    state = (
        "enrollment_database_file",
        "access_inbox_database_file",
        "member_policy_file",
        "owner_native_admission_directory",
    )
    originals = [Path(getattr(parent, field)).resolve() for field in state]
    selected = []
    for field in state:
        value = getattr(config, field)
        if value is None:
            raise ValueError("published origin requires separate state")
        path = Path(value)
        # Resolve aliases even before the SQLite file exists. Hard links cannot
        # share a replay/member store either. Never copy grants between origins.
        resolved = path.resolve()
        if path.is_symlink() or (path.is_file() and path.stat().st_nlink != 1):
            raise ValueError("published state must not be linked")
        for other in originals + selected:
            if resolved == other or resolved in other.parents or other in resolved.parents:
                raise ValueError("published origin requires separate state")
            if path.exists() and other.exists() and path.samefile(other):
                raise ValueError("published state must not be aliased")
        selected.append(resolved)
    return actual
