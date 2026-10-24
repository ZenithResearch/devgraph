"""Protected owner-local Wallet administration upgrade for one selected resource.

No HTTP bootstrap route; the fixed per-user helper calls this module. Setup
tokens remain in a separate short-lived owner-private local handle, never in
arguments, public metadata, logs, credentials or the server authorization DB.
"""

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import socket
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from devgraph.auth.owner_bootstrap import (
    BootstrapBinding,
    BootstrapError,
    BootstrapStore,
    active_owner,
)
from devgraph.local_resources import validate_record
from devgraph.ops import owner_install as install
from devgraph.ops.owner_setup import SHORT_OPERATIONS

OWNER_OPERATIONS = SHORT_OPERATIONS


def registry_path(home, resource_id):
    if str(UUID(resource_id)) != resource_id:
        raise BootstrapError("invalid_resource_id")
    return (
        home
        / "Library/Application Support/Zenith/Devgraph/owners"
        / resource_id
        / "registry.sqlite"
    )


def token_path(home, resource_id):
    return registry_path(home, resource_id).with_name("pending-setup.json")


@contextmanager
def _locked(home, resource_id):
    path = registry_path(home, resource_id).parent
    install.directory(path)
    BootstrapStore._private(path, directory=True)
    fd = os.open(path / ".setup.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise BootstrapError("unsafe_setup_lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _review(plan_file, digest, home, *, staged=True):
    if staged:
        plan, root, review = install.checked_review(plan_file, digest, home=home)
    else:
        # Read-only owner recovery remains valid after legitimate native holder
        # admission changes the current native-policy file. Never use this path
        # to prepare or activate authority.
        plan = install.decode_plan(install.read(plan_file, maximum=65536))
        root = (
            home
            / "Library/Application Support/Zenith/Devgraph/wallet-endpoints"
            / plan.resource_id
            / install.plan_digest(plan)
        )
        review = install.object_file(root / "review.json", maximum=1048576)
        install.require(
            digest == install.sha(install.encoded(review))
            and review["plan_digest"] == install.plan_digest(plan)
            and review["root"] == str(root),
            "review_changed",
        )
    install.require(
        plan.schema_ == "zenith.devgraph-owner-setup.v3"
        and review.get("owner_claim_required") is True
        and review.get("local_only") is True
        and review["source_endpoint"] == plan.local_endpoint,
        "owner_upgrade_review_required",
    )
    return plan, root, review


def _binding(home, plan, endpoint=None):
    config = home / "Library/Application Support/Zenith/Devgraph/local.json"
    registration = config.with_name("resource-registration.json")
    raw = install.read(registration)
    record = validate_record(json.loads(raw))
    return BootstrapBinding(
        resource_id=record["resource_id"],
        endpoint=endpoint or record["endpoint"],
        configuration_sha256=install.sha(install.read(config)),
        registration_sha256=install.sha(raw),
    )


def expected_binding(plan, endpoint=None):
    return BootstrapBinding(
        resource_id=plan.resource_id,
        endpoint=endpoint or plan.local_endpoint,
        configuration_sha256=plan.configuration_sha256,
        registration_sha256=plan.registration_sha256,
    )


def _handle(path, review_digest, public_key):
    value = install.object_file(path, maximum=16384)
    install.require(
        set(value) == {"schema", "review_digest", "public_key", "secret", "claim_id"}
        and value["schema"] == "zenith.local-owner-setup-handle.v1"
        and value["review_digest"] == review_digest
        and value["public_key"] == public_key,
        "setup_handle_changed",
    )
    return value


def prepare_claim(plan_file, review_digest, *, home=None, now=None, refresh=False):
    home = home or Path.home()
    now = int(time.time()) if now is None else now
    plan, _root, _review_value = _review(plan_file, review_digest, home)
    with _locked(home, plan.resource_id):
        store = BootstrapStore(
            registry_path(home, plan.resource_id),
            expected_binding(plan, _review_value.get("existing_service", {}).get("endpoint")),
            current_binding=lambda: _binding(
                home, plan, _review_value.get("existing_service", {}).get("endpoint")
            ),
        )
        state = store.status()["state"]
        if state == "active":
            raise BootstrapError("already_initialized")
        path = token_path(home, plan.resource_id)
        if state == "uninitialized":
            install.require(
                not path.exists() and not path.is_symlink(), "inspect_retained_setup_handle"
            )
            handle = dict(
                schema="zenith.local-owner-setup-handle.v1",
                review_digest=review_digest,
                public_key=plan.owner_public_key,
                secret=secrets.token_hex(32),
                claim_id=secrets.token_hex(16),
            )
            install.save(path, install.encoded(handle))
            store.arm(handle["secret"], now=now, expires_at=now + 900)
        else:
            handle = _handle(path, review_digest, plan.owner_public_key)
        if refresh:
            return store.refresh_claim(
                handle["secret"], plan.owner_public_key, handle["claim_id"], now=now
            )
        return store.prepare_claim(
            handle["secret"],
            plan.owner_public_key,
            handle["claim_id"],
            now=now,
            grant_id=review_digest,
        )


def require_owner(plan_file, review_digest, *, home=None):
    home = home or Path.home()
    plan, _root, _review_value = _review(plan_file, review_digest, home)
    owner = active_owner(
        registry_path(home, plan.resource_id),
        resource_id=plan.resource_id,
        grant_id=review_digest,
        public_key=plan.owner_public_key,
    )
    install.require(
        owner["binding"]
        == expected_binding(
            plan, _review_value.get("existing_service", {}).get("endpoint")
        ).__dict__,
        "owner_binding_changed",
    )
    return owner["result"]["admin_grant"]


def complete_claim(plan_file, review_digest, proof_file, *, home=None, now=None):
    home = home or Path.home()
    now = int(time.time()) if now is None else now
    plan, _root, _review_value = _review(plan_file, review_digest, home)
    proof = install.object_file(proof_file, maximum=16384)
    path = registry_path(home, plan.resource_id)
    with _locked(home, plan.resource_id):
        try:
            retained = active_owner(
                path,
                resource_id=plan.resource_id,
                grant_id=review_digest,
                public_key=plan.owner_public_key,
            )
        except BootstrapError:
            retained = None
        if retained is not None:
            install.require(
                retained["proof_digest"] == hashlib.sha256(install.encoded(proof)).hexdigest(),
                "original_owner_proof_required",
            )
            result = dict(retained["result"], duplicate=True)
        else:
            handle = _handle(
                token_path(home, plan.resource_id), review_digest, plan.owner_public_key
            )
            store = BootstrapStore(
                path,
                expected_binding(plan, _review_value.get("existing_service", {}).get("endpoint")),
                current_binding=lambda: _binding(
                    home, plan, _review_value.get("existing_service", {}).get("endpoint")
                ),
            )
            install.require(
                proof.get("challenge", {}).get("admin_grant", {}).get("grant_id") == review_digest,
                "owner_review_changed",
            )
            result = store.redeem(handle["secret"], proof, now=now)
        token = token_path(home, plan.resource_id)
        if token.exists():
            _handle(token, review_digest, plan.owner_public_key)
            token.unlink()
        return result


def inspect_claim(plan_file, review_digest, proof_file, *, home=None):
    home = home or Path.home()
    plan, _root, _review_value = _review(plan_file, review_digest, home, staged=False)
    proof = install.object_file(proof_file, maximum=16384)
    retained = active_owner(
        registry_path(home, plan.resource_id),
        resource_id=plan.resource_id,
        grant_id=review_digest,
        public_key=plan.owner_public_key,
    )
    install.require(
        retained["proof_digest"] == hashlib.sha256(install.encoded(proof)).hexdigest(),
        "original_owner_proof_required",
    )
    return dict(retained["result"], duplicate=True)


def propose(resource_id, source_endpoint, *, home=None):
    home = home or Path.home()
    config = home / "Library/Application Support/Zenith/Devgraph/local.json"
    registration = validate_record(
        install.object_file(config.with_name("resource-registration.json"))
    )
    install.require(
        registration["resource_id"] == resource_id
        and registration["endpoint"] == source_endpoint
        and registration["connection"] is None,
        "selected_connection_changed",
    )
    status = install.cli_read(home, ["local", "status"])
    install.require(
        status.get("healthy") is True
        and status.get("api", {}).get("api", {}).get("base_url") == source_endpoint,
        "selected_graph_not_ready",
    )
    install.require(
        not registry_path(home, resource_id).exists(), "owner_state_requires_inspection"
    )
    with socket.socket() as port:
        port.bind(("127.0.0.1", 0))
        number = port.getsockname()[1]
    return dict(
        schema="devgraph.wallet-admin-upgrade-proposal.v1",
        resource_id=resource_id,
        source_endpoint=source_endpoint,
        wallet_endpoint=f"https://127.0.0.1:{number}",
        local_only=True,
        authority_changed=False,
    )


def refresh_service(resource_id, source_endpoint, *, home=None):
    from devgraph.local_services import publish_service

    home = home or Path.home()
    config = home / "Library/Application Support/Zenith/Devgraph/local.json"
    registration = config.with_name("resource-registration.json")
    raw = install.read(registration)
    record = validate_record(json.loads(raw))
    install.require(
        record["resource_id"] == resource_id
        and record["endpoint"] == source_endpoint
        and record["connection"] is None,
        "selected_connection_changed",
    )
    status = install.cli_read(home, ["local", "status"])
    install.require(
        status.get("healthy") is True
        and status.get("api", {}).get("api", {}).get("base_url") == source_endpoint,
        "selected_graph_not_ready",
    )
    return publish_service(
        config,
        interface="private-api",
        endpoint=source_endpoint,
        home=home,
        now=int(time.time()),
        expected_registration_sha256=install.sha(raw),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "propose",
            "service-refresh",
            "prepare",
            "claim-prepare",
            "claim-refresh",
            "claim-complete",
            "claim-status",
            "activate",
            "status",
        ),
    )
    parser.add_argument("--plan-file", type=Path)
    parser.add_argument("--review-digest")
    parser.add_argument("--proof-file", type=Path)
    parser.add_argument("--resource-id")
    parser.add_argument("--source-endpoint")
    args = parser.parse_args()
    try:
        if args.command == "service-refresh":
            result = refresh_service(args.resource_id, args.source_endpoint)
        elif args.command == "propose":
            result = propose(args.resource_id, args.source_endpoint)
        else:
            install.require(args.plan_file is not None, "plan_required")
            if args.command == "prepare":
                result = install.prepare(args.plan_file)
            elif args.command == "status":
                result = install.active_status(args.plan_file)
            else:
                install.require(args.review_digest is not None, "review_required")
                if args.command in ("claim-prepare", "claim-refresh"):
                    result = prepare_claim(
                        args.plan_file, args.review_digest, refresh=args.command == "claim-refresh"
                    )
                elif args.command == "claim-status":
                    install.require(args.proof_file is not None, "proof_required")
                    result = inspect_claim(args.plan_file, args.review_digest, args.proof_file)
                elif args.command == "claim-complete":
                    install.require(args.proof_file is not None, "proof_required")
                    result = complete_claim(args.plan_file, args.review_digest, args.proof_file)
                else:
                    require_owner(args.plan_file, args.review_digest)
                    result = install.activate(args.plan_file, args.review_digest)
        print(install.encoded(result).decode())
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        print(
            json.dumps(
                dict(
                    error="owner_upgrade_step_failed",
                    instruction="Inspect retained state; do not retry activation.",
                )
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
