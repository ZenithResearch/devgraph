"""Owner-local production endpoint preparation. Never accepts an HTTP caller.

Preparation only stages a private installation and public review. Activation is
an explicit digest-bound second operation. Wallet ownership is confirmed by the
real endpoint's action-bound native inbox proof; it is not inferred from a plan.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import stat
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID

from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.path_access import PathEnrollmentPolicy
from devgraph.auth.signed_read import SecsReadAuthority
from devgraph.auth.standing_access import StandingMemberPolicy, StandingNativePolicy
from devgraph.auth.write_provisioning import NativeWritePolicy
from devgraph.local_host import load_local_config
from devgraph.local_resources import validate_record
from devgraph.ops.owner_setup import compile_operator_policy, decode_plan, plan_digest

MAXIMUM = 32 * 1024 * 1024


class OwnerInstallError(ValueError):
    """Closed owner-facing errors; never return subprocess stderr or secret data."""


def require(ok, reason):
    if not ok:
        raise OwnerInstallError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return canonical_json(value)


def read(path: Path, *, private=True, maximum=MAXIMUM):
    require(path.is_absolute() and path.resolve(strict=True) == path, "unsafe_path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and info.st_uid == os.geteuid()
            and info.st_size <= maximum
            and info.st_mode & (0o077 if private else 0o022) == 0,
            "unsafe_file",
        )
        raw = file.read(maximum + 1)
        require(len(raw) <= maximum, "file_too_large")
        return raw


def object_file(path, *, maximum=262144):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_field")
            result[key] = value
        return result

    value = json.loads(read(path, maximum=maximum), object_pairs_hook=unique)
    require(isinstance(value, dict), "invalid_object")
    return value


def directory(path):
    if not path.exists() and not path.is_symlink():
        directory(path.parent)
        path.mkdir(mode=0o700)
    info = path.lstat()
    require(
        path.resolve(strict=True) == path
        and stat.S_ISDIR(info.st_mode)
        and info.st_uid in (0, os.geteuid())
        and info.st_mode & 0o022 == 0,
        "unsafe_directory",
    )


def save(path, raw, *, executable=False):
    directory(path.parent)
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o700 if executable else 0o600,
    )
    with os.fdopen(fd, "wb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())
    handle = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def replace(path, raw, expected, *, private=True):
    require(sha(read(path, private=private)) == expected, "concurrent_file_change")
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(12))
    save(temporary, raw)
    require(sha(read(path, private=private)) == expected, "concurrent_file_change")
    os.replace(temporary, path)


def invoke(args, *, timeout=45, environment=None):
    p = subprocess.run(
        [str(a) for a in args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        env=environment if environment is not None else {},
    )
    require(p.returncode == 0 and len(p.stdout) <= 1048576, "native_command_failed")
    value = json.loads(p.stdout)
    require(isinstance(value, dict), "invalid_native_result")
    return value


def converted_native(policy, holders, operations, validity):
    """Preserve every finite historical rule, status and deny before new rules."""
    old = (
        StandingNativePolicy if policy["schema"].endswith(".v2") else NativeWritePolicy
    ).model_validate(policy)
    raw = old.model_dump(mode="json", by_alias=True)
    raw.update(
        schema="secs-devgraph-work-policy.v2",
        schema_version=2,
        policy_version=old.policy_version + 1,
    )
    for rule in raw["rules"]:
        if "validity" not in rule:
            rule["validity"] = dict(
                schema="castalia.grant-validity.v1",
                mode="dated",
                not_before=rule.pop("not_before"),
                not_after=rule.pop("not_after"),
            )
    for holder in holders:
        require(
            isinstance(holder, str) and len(holder) == 64 and bytes.fromhex(holder).hex() == holder,
            "invalid_native_holder",
        )
        actor = "pubkey:sha256:" + sha(bytes.fromhex(holder))
        for operation in operations:
            for kind in ("Initiative", "Project", "Issue", "Task"):
                raw["rules"].append(
                    dict(
                        actor_id=actor,
                        operation=operation,
                        resource=kind + "/",
                        resource_match="prefix",
                        effect="allow",
                        status="active",
                        validity=validity,
                    )
                )
    return StandingNativePolicy.model_validate(raw)


def tls_material(host, now):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "DevGraph Wallet connection")])
    at = datetime.fromtimestamp(now, timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(at - timedelta(minutes=5))
        .not_valid_after(at + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))]),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        cert.public_bytes(serialization.Encoding.PEM),
        cert.fingerprint(hashes.SHA256()).hex(),
    )


def copy_package(source, destination):
    count = total = 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if "__pycache__" in relative.parts or path.suffix in (".pyc", ".pyo"):
            continue
        require(not path.is_symlink(), "linked_source")
        if path.is_dir():
            continue
        raw = read(path, private=False)
        total += len(raw)
        count += 1
        require(total <= 128 * 1024 * 1024 and count <= 5000, "source_package_too_large")
        save(destination / relative, raw)
    require(count > 20, "incomplete_runtime_package")


def inventory(root):
    return {str(p.relative_to(root)): sha(read(p)) for p in sorted(root.rglob("*")) if p.is_file()}


def cli_read(home, arguments):
    executable = home / ".local/bin/devgraph"
    read(executable, private=False)
    value = invoke([executable, *arguments], timeout=45)
    return value


def verify_live_graph(home, plan):
    from devgraph.resource_paths import ResourcePath

    status = cli_read(home, ["local", "status"])
    require(status.get("healthy") is True, "configured_graph_not_ready")

    def pages(arguments, cursor_name, typed=False):
        rows = []
        after = None
        for _ in range(20):
            args = arguments + ["--limit", "100"]
            if after:
                args += [cursor_name, after]
            page = cli_read(home, args).get("items")
            require(isinstance(page, list) and len(page) <= 100, "invalid_hierarchy_page")
            rows.extend(page)
            if len(page) < 100:
                return rows
            last = page[-1]
            next_id = (last["kind"] + "/" if typed else "") + last["id"]
            require(next_id != after, "repeating_hierarchy_cursor")
            after = next_id
        raise OwnerInstallError("hierarchy_capacity")

    arenas = pages(["query", "arena"], "--after-id")
    arena_ids = {
        row["id"] for row in arenas if row.get("kind") == "Arena" and not row.get("archived", False)
    }
    for selected in plan.paths:
        require(selected.path[0].split("/", 1)[1] in arena_ids, "selected_arena_changed")
        for index in range(1, len(selected.path)):
            parent_kind, parent_id = selected.path[index - 1].split("/", 1)
            child_kind, child_id = selected.path[index].split("/", 1)
            args = (
                ["query", "arena-members", parent_id]
                if parent_kind == "Arena"
                else ["query", "children", parent_kind, parent_id]
            )
            children = pages(args, "--after-resource", typed=True)
            require(
                any(
                    row.get("kind") == child_kind
                    and row.get("id") == child_id
                    and not row.get("archived", False)
                    for row in children
                ),
                "selected_path_changed",
            )
    return tuple(
        ResourcePath.model_validate(dict(schema="devgraph.resource-path.v1", path=["Arena/" + key]))
        for key in sorted(arena_ids)
    )


def prepare(
    plan_file, *, home=None, artifacts_file=None, holders=(), now=None, existing_service=None
):
    """Stage only. Native status reads cannot provision, sign, or start a service."""
    now = int(time.time()) if now is None else now
    home = home or Path.home()
    config_path = home / "Library/Application Support/Zenith/Devgraph/local.json"
    config = load_local_config(config_path)
    registration_path = config_path.with_name("resource-registration.json")
    config_raw, registration_raw = read(config_path), read(registration_path)
    registration = validate_record(json.loads(registration_raw))
    plan = decode_plan(read(plan_file, maximum=65536))
    require(
        registration["resource_id"] == plan.resource_id
        and registration["connection"] is None
        and registration["endpoint"] == plan.local_endpoint
        and sha(registration_raw) == plan.registration_sha256
        and sha(config_raw) == plan.configuration_sha256,
        "instance_or_plan_changed",
    )
    if existing_service is not None:
        from devgraph.ops.service_owner import selected_service

        current = selected_service(
            home, plan.resource_id, existing_service["instance_id"], plan.wallet_endpoint
        )
        require(current == existing_service, "selected_service_changed")
    host = urlsplit(plan.wallet_endpoint).hostname
    local_upgrade = plan.schema_ == "zenith.devgraph-owner-setup.v3"
    require(
        (
            host == "127.0.0.1"
            if local_upgrade
            else ipaddress.ip_address(host).is_private
            and not ipaddress.ip_address(host).is_loopback
        ),
        "use_private_lan_address_for_current_runtime",
    )
    artifacts_file = artifacts_file or config_path.parent / "owner-runtime/artifacts.json"
    artifacts = object_file(artifacts_file)
    require(
        set(artifacts) == {"schema", "binaries"}
        and artifacts["schema"] == "devgraph.owner-runtime-artifacts.v1"
        and set(artifacts["binaries"])
        == {"secs-devgraph-work-v1", "secs-devgraph-work-v2", "secs-read-authority-v1"},
        "invalid_artifact_manifest",
    )
    binaries = {}
    for name, item in artifacts["binaries"].items():
        require(set(item) == {"path", "sha256"}, "invalid_artifact_manifest")
        raw = read(Path(item["path"]), private=False)
        require(sha(raw) == item["sha256"], "artifact_changed")
        binaries[name] = raw
    native = home / "Library/Application Support/Zenith/secS"
    bundle = native / "authority/devgraph.work.v1"
    native_policy = object_file(bundle / "receiver-policy.json")
    old = (
        StandingNativePolicy if native_policy["schema"].endswith(".v2") else NativeWritePolicy
    ).model_validate(native_policy)
    status = invoke([artifacts["binaries"]["secs-devgraph-work-v1"]["path"], "admin", "status"])
    require(
        status.get("ready") is True
        and status["policy_id"] == old.policy_id
        and status["policy_version"] == old.policy_version
        and status["policy_digest_sha256"] == old.digest,
        "native_status_mismatch",
    )
    registry = object_file(bundle / "secs-public-key-registry.json")
    require(
        registry == status["registry"] and len(registry["keys"]) == 1,
        "native_registry_changed",
    )
    key = registry["keys"][0]
    require(key["status"] == "active" and now < key["not_after"], "native_key_not_current")
    receiver_path = config.data_root / "secrets/secs-magik/devgraph.work.v1/receiver.json"
    receiver = object_file(receiver_path)
    require(
        receiver["policy_binding"]
        == dict(
            policy_id=old.policy_id,
            policy_version=old.policy_version,
            policy_digest_sha256=old.digest,
        ),
        "managed_receiver_mismatch",
    )
    registry_path = receiver_path.with_name("secs-public-key-registry.json")
    require(object_file(registry_path) == registry, "managed_key_mismatch")
    # A new instance cannot inherit old local generic origin configuration.
    targets = [native / "bin" / n for n in ("secs-devgraph-work-v2", "secs-read-authority-v1")]
    targets += [
        native / "authority/credential-presentation-v2.json",
        config.data_root / "secrets/secs-magik/devgraph.work.v2",
    ]
    require(
        all(not p.exists() and not p.is_symlink() for p in targets),
        "existing_generic_configuration_requires_review",
    )
    live_roots = verify_live_graph(home, plan)
    policy = compile_operator_policy(
        plan,
        now=now,
        published_global_roots=live_roots if plan.publish_hierarchy else (),
    )
    operations = [g.operation for g in policy.ceiling.write_grants]
    require(
        len(holders) <= 32 and len(set(holders)) == len(holders),
        "invalid_native_holders",
    )
    require(not holders, "member_admission_is_part_of_exact_grant_approval")
    native_next = converted_native(
        native_policy,
        holders,
        operations,
        json.loads(policy.ceiling.validity.to_json()),
    )
    root = config_path.parent / "wallet-endpoints" / plan.resource_id / plan_digest(plan)
    require(not root.exists(), "prepared_run_exists_reconcile_original")
    directory(root)
    os.chmod(root, 0o700)
    save(root / "plan.json", read(plan_file, maximum=65536))
    save(
        root / "state/prepared.json",
        encoded(dict(phase="preparing", authority_changed=False)),
    )
    if existing_service is None:
        # New service-owned key, never a human Wallet seed or any fixture key.
        read_key = ed25519.Ed25519PrivateKey.generate()
        read_public = (
            read_key.public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            .hex()
        )
        seed = read_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        save(root / "authority/read.key", seed.hex().encode() + b"\n")
        tls_key, tls_cert, fingerprint = tls_material(host, now)
        save(root / "tls/tls.key", tls_key)
        save(root / "tls/tls.crt", tls_cert)
    else:
        # Reuse the launched instance's exact issuer, TLS material and address.
        from cryptography import x509

        service_root = Path(existing_service["root"])
        for relative in ("authority/read.key", "tls/tls.key", "tls/tls.crt"):
            save(root / relative, read(service_root / relative))
        read_public = existing_service["connection"]["authority"]["public_key"]
        fingerprint = (
            x509.load_pem_x509_certificate(read(root / "tls/tls.crt"))
            .fingerprint(hashes.SHA256())
            .hex()
        )
    ceiling = policy.ceiling.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    root_policy = PathEnrollmentPolicy.model_validate(
        dict(
            schema="devgraph.native-read-policy.v3",
            issuer=policy.ceiling.issuer,
            receiver=plan.wallet_endpoint,
            grants=ceiling["read_grants"],
            lifetime_seconds=900,
        )
    )
    authority = SecsReadAuthority.policy_for(
        root_policy,
        key_id="devgraph-" + plan.resource_id + "-read-v1",
        public_key=read_public,
    )
    documents = {
        "operators.json": policy.model_dump(mode="json", by_alias=True, exclude_defaults=True),
        "members.json": StandingMemberPolicy.model_validate(
            dict(
                schema="devgraph.member-access-policy.v4",
                revision=1,
                ceiling=ceiling,
                assignments=[],
            )
        ).model_dump(mode="json", by_alias=True, exclude_defaults=True),
        "enrollment.json": root_policy.model_dump(
            mode="json", by_alias=True, exclude_defaults=True
        ),
        "read-authority.json": authority,
        "native-policy.json": native_next.model_dump(mode="json", by_alias=True),
        "membership-trust.json": {},
        "revoked.json": dict(schema="devgraph.revoked-memberships.v1", membership_ids=[]),
    }
    for name, value in documents.items():
        save(root / "authority" / name, encoded(value))
    for name, raw in binaries.items():
        save(root / "bin" / name, raw, executable=True)
    import devgraph

    copy_package(Path(devgraph.__file__).parent, root / "source/devgraph")
    runtime = dict(
        schema="devgraph.signed-read-runtime.v9",
        membership_requirement="optional",
        owner_native_admission_directory=str(root / "state/native-admission"),
        access_inbox_database_file=str(root / "state/inbox.sqlite"),
        enrollment_database_file=str(root / "state/enrollment.sqlite"),
        secs_binary=str(root / "bin/secs-read-authority-v1"),
        secs_binary_sha256=sha(binaries["secs-read-authority-v1"]),
        issuer_key_file=str(root / "authority/read.key"),
        write_native_policy_file=str(bundle / "receiver-policy.json"),
    )
    for key_name, filename in [
        ("access_operator_policy_file", "operators.json"),
        ("member_policy_file", "members.json"),
        ("enrollment_policy_file", "enrollment.json"),
        ("authority_policy_file", "read-authority.json"),
        ("membership_trust_file", "membership-trust.json"),
        ("revoked_memberships_file", "revoked.json"),
    ]:
        runtime[key_name] = str(root / "authority" / filename)
    if local_upgrade:
        from devgraph.ops.wallet_admin_upgrade import registry_path as owner_registry_path

        runtime.update(
            owner_bootstrap_database_file=str(owner_registry_path(home, plan.resource_id)),
            owner_install_review_file=str(root / "review.json"),
        )
    save(root / "runtime.json", encoded(runtime))
    connection = dict(
        name=plan.resource_name,
        receiver=plan.wallet_endpoint,
        issuer=policy.ceiling.issuer,
        authority={
            k: authority[k] for k in ("issuer", "key_id", "public_key", "receiver", "audience")
        },
        network="local" if local_upgrade else "lan",
        tls_certificate_sha256=fingerprint,
    )
    save(root / "connection.json", encoded(connection))
    cli_path = home / ".local/bin/devgraph"
    cli_hash = sha(read(cli_path, private=False))
    inputs = {
        str(p): sha(read(p))
        for p in [
            config_path,
            registration_path,
            receiver_path,
            registry_path,
            bundle / "receiver-policy.json",
            bundle / "producer-manifest.json",
            bundle / "secs-public-key-registry.json",
            native / "bin/secs-devgraph-work-v1",
        ]
    }
    inputs[str(cli_path)] = cli_hash
    require(
        inputs[str(config_path)] == plan.configuration_sha256
        and inputs[str(registration_path)] == plan.registration_sha256,
        "instance_changed_during_preparation",
    )
    agent_status = cli_read(home, ["auth", "work", "status"])
    require(
        agent_status.get("ready") is True
        and agent_status.get("signer_matches") is True
        and agent_status.get("receiver_matches") is True,
        "existing_agent_authority_not_ready",
    )
    review = dict(
        schema="devgraph.owner-install-review.v1",
        status="prepared_not_activated",
        resource_id=plan.resource_id,
        owner_public_key=plan.owner_public_key,
        endpoint=plan.wallet_endpoint,
        root=str(root),
        plan_digest=plan_digest(plan),
        public_inputs=[str(cli_path)],
        cli_update_required=True,
        previous_agent_status=agent_status,
        tls_certificate_sha256=fingerprint,
        tls_certificate_not_after=now + 365 * 86400,
        connection=connection,
        inputs=inputs,
        staged=inventory(root),
        native_policy_id=old.policy_id,
        previous_native_version=old.policy_version,
        previous_native_digest=old.digest,
        native_policy_version=native_next.policy_version,
        native_policy_digest=native_next.digest,
        native_key_id=key["key_id"],
        native_key_not_after=key["not_after"],
        native_key_registry=registry,
        native_holders=[],
        native_admission_on_grant=True,
        operations=operations,
        kinds=["Initiative", "Project", "Issue", "Task"],
        graph_data_root=str(config.data_root),
        python=str(config.python_executable),
        members_preassigned=0,
        existing_rules_preserved=len(old.rules),
        new_native_rules=len(native_next.rules) - len(old.rules),
        onward_delegation=False,
        native_key_unchanged=True,
        private_api_unchanged=True,
        database_restart=False,
        certificate_trust_changed=False,
        owner_wallet_confirmation_required=True,
    )
    if local_upgrade:
        review.update(
            source_endpoint=plan.local_endpoint, local_only=True, owner_claim_required=True
        )
    if existing_service is not None:
        require(connection == existing_service["connection"], "selected_service_profile_changed")
        review["existing_service"] = existing_service
    save(root / "review.json", encoded(review))
    return dict(review=review, review_digest=sha(encoded(review)))


def checked_review(plan_file, review_digest, *, home=None):
    home = home or Path.home()
    plan = decode_plan(read(plan_file, maximum=65536))
    root = (
        home
        / "Library/Application Support/Zenith/Devgraph/wallet-endpoints"
        / plan.resource_id
        / plan_digest(plan)
    )
    review = object_file(root / "review.json", maximum=1048576)
    require(
        review_digest == sha(encoded(review))
        and review["plan_digest"] == plan_digest(plan)
        and review["root"] == str(root),
        "review_changed",
    )
    for name, expected in review["staged"].items():
        path = root / name
        require(
            path.is_relative_to(root)
            and ".." not in Path(name).parts
            and sha(read(path)) == expected,
            "staged_input_changed",
        )
    return plan, root, review


def active_status(plan_file, *, home=None):
    home = home or Path.home()
    plan = decode_plan(read(plan_file, maximum=65536))
    root = (
        home
        / "Library/Application Support/Zenith/Devgraph/wallet-endpoints"
        / plan.resource_id
        / plan_digest(plan)
    )
    phases = [p.stem for p in sorted((root / "state").glob("*.json"))] if root.is_dir() else []
    result = dict(
        schema="devgraph.owner-install-status.v1",
        prepared=(root / "review.json").is_file(),
        active=(root / "state/active.json").is_file(),
        phases=phases,
        root=str(root),
        instruction="Inspect the retained run before resuming any partial activation.",
    )
    if result["prepared"]:
        review = object_file(root / "review.json", maximum=1048576)
        result["preparation"] = dict(review=review, review_digest=sha(encoded(review)))
    if result["active"]:
        result["installed_result"] = object_file(root / "state/active.json")
    return result


def activate(plan_file, review_digest, *, home=None):
    """Explicit local owner installation. No member grant or Work is created."""
    import fcntl
    import http.client
    import plistlib
    import ssl

    home = home or Path.home()
    plan, root, review = checked_review(plan_file, review_digest, home=home)
    require(plan.schema_ == "zenith.devgraph-owner-setup.v3", "signed_owner_upgrade_required")
    from devgraph.ops.wallet_admin_upgrade import require_owner

    try:
        require_owner(plan_file, review_digest, home=home)
    except ValueError as error:
        raise OwnerInstallError("owner_registration_required") from error
    lock_path = root.parent / ".installation.lock"
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and info.st_mode & 0o077 == 0
            and info.st_nlink == 1,
            "unsafe_install_lock",
        )
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(
            not (root / "state/activation-started.json").exists(),
            "prior_activation_requires_reconciliation",
        )
        for filename, expected in review["inputs"].items():
            require(
                sha(
                    read(
                        Path(filename),
                        private=filename not in review.get("public_inputs", []),
                    )
                )
                == expected,
                "production_input_changed",
            )
        require(int(time.time()) < review["native_key_not_after"], "native_key_expired")
        verify_live_graph(home, plan)
        config_path = home / "Library/Application Support/Zenith/Devgraph/local.json"
        config = load_local_config(config_path)
        require(str(config.data_root) == review["graph_data_root"], "graph_changed")
        origin = urlsplit(plan.wallet_endpoint)
        existing_service = review.get("existing_service")
        if existing_service is None:
            with socket.socket() as check:
                require(
                    check.connect_ex((origin.hostname, origin.port)) != 0, "endpoint_port_in_use"
                )
        else:
            from devgraph.ops.service_owner import selected_service

            require(
                selected_service(
                    home, plan.resource_id, existing_service["instance_id"], plan.wallet_endpoint
                )
                == existing_service,
                "selected_service_changed",
            )
        native = home / "Library/Application Support/Zenith/secS"
        data_bundle = config.data_root / "secrets/secs-magik/devgraph.work.v1"
        require(
            not (config.data_root / "secrets/secs-magik/devgraph.work.v2").exists(),
            "generic_receiver_already_exists",
        )
        for name in ("secs-devgraph-work-v2", "secs-read-authority-v1"):
            require(not (native / "bin" / name).exists(), "generic_binary_already_exists")
        native_config = native / "authority/credential-presentation-v2.json"
        require(not native_config.exists(), "native_origin_configuration_exists")
        admin = root / "bin/secs-devgraph-work-v1"
        status = invoke([admin, "admin", "status"])
        require(
            status.get("ready") is True
            and status["policy_digest_sha256"] == review["previous_native_digest"]
            and status["secs_verifier_key_id"] == review["native_key_id"],
            "native_changed_before_install",
        )
        snapshot = root / "prior/native-snapshot"
        directory(snapshot)
        os.chmod(snapshot, 0o700)
        saved = invoke([admin, "admin", "snapshot", "--output-directory", snapshot])
        verified = invoke([admin, "admin", "verify-snapshot", "--input-directory", snapshot])
        require(
            saved["file_hashes"] == verified["file_hashes"]
            and saved["policy_digest_sha256"] == review["previous_native_digest"]
            and saved["secs_verifier_key_id"] == review["native_key_id"],
            "native_snapshot_mismatch",
        )
        # Preserve public receiver pins and the old binary separately. Never
        # restore a snapshot automatically over newer policy/replay/revocation.
        originals = {}
        for name in ("receiver.json", "secs-public-key-registry.json"):
            p = data_bundle / name
            originals[str(p)] = review["inputs"][str(p)]
            require(sha(read(p)) == originals[str(p)], "managed_receiver_changed")
            save(root / "prior" / name, read(p))
        save(
            root / "prior/secs-devgraph-work-v1",
            read(native / "bin/secs-devgraph-work-v1"),
        )
        save(
            root / "state/activation-started.json",
            encoded(dict(phase="native_dispatch_possible", at=int(time.time()))),
        )
        installed = invoke(
            [
                admin,
                "admin",
                "provision",
                "--policy-file",
                root / "authority/native-policy.json",
                "--expected-policy-digest",
                review["previous_native_digest"],
                "--verifier-not-after",
                review["native_key_not_after"],
            ]
        )
        require(
            installed.get("ready") is True
            and installed["policy_version"] == review["native_policy_version"]
            and installed["policy_digest_sha256"] == review["native_policy_digest"]
            and installed["secs_verifier_key_id"] == review["native_key_id"],
            "native_readback_failed",
        )
        registry = installed["registry"]
        old_key = review["native_key_registry"]["keys"][0]
        require(
            len(registry["keys"]) == 1
            and registry["keys"][0]["not_after"] == old_key["not_after"]
            and {
                k: v for k, v in registry["keys"][0].items() if k not in ("not_before", "not_after")
            }
            == {k: v for k, v in old_key.items() if k not in ("not_before", "not_after")},
            "native_key_changed",
        )
        save(
            root / "state/native-installed.json",
            encoded(dict(policy_digest=installed["policy_digest_sha256"], same_key=True)),
        )
        receiver = object_file(data_bundle / "receiver.json")
        receiver["policy_binding"].update(
            policy_version=review["native_policy_version"],
            policy_digest_sha256=review["native_policy_digest"],
        )
        replace(
            data_bundle / "receiver.json",
            encoded(receiver),
            originals[str(data_bundle / "receiver.json")],
        )
        replace(
            data_bundle / "secs-public-key-registry.json",
            encoded(registry),
            originals[str(data_bundle / "secs-public-key-registry.json")],
        )
        v2 = config.data_root / "secrets/secs-magik/devgraph.work.v2"
        modern = dict(receiver, schema="devgraph-secs-work-receiver.v2", schema_version=2)
        save(v2 / "receiver.json", encoded(modern))
        save(v2 / "secs-public-key-registry.json", encoded(registry))
        save(
            v2 / "browser.json",
            encoded(
                dict(
                    schema="devgraph.browser-credential-transport.v2",
                    origins=[plan.wallet_endpoint],
                )
            ),
        )
        public_key = base64.urlsafe_b64decode(
            registry["keys"][0]["public_key_base64url"] + "=="
        ).hex()
        save(
            v2 / "wallet-provider.json",
            encoded(
                dict(
                    schema="castalia.provider-profile.v1",
                    display_name="DevGraph Work",
                    origins=[plan.wallet_endpoint],
                    membership=None,
                    presentations=[
                        dict(
                            issuer=receiver["stable_issuer"],
                            key_id=review["native_key_id"],
                            public_key=public_key,
                            audience="devgraph://receiver-local",
                            callers=[dict(kind="browser", id=plan.wallet_endpoint)],
                        )
                    ],
                )
            ),
        )
        save(
            native_config,
            encoded(
                dict(
                    schema="secs-devgraph-credential-config.v2",
                    schema_version=2,
                    issuer=receiver["stable_issuer"],
                    callers=[dict(kind="browser", id=plan.wallet_endpoint)],
                )
            ),
        )
        old_binary = native / "bin/secs-devgraph-work-v1"
        replace(
            old_binary,
            read(root / "bin/secs-devgraph-work-v1"),
            review["inputs"][str(old_binary)],
        )
        old_binary.chmod(0o700)
        for name in ("secs-devgraph-work-v2", "secs-read-authority-v1"):
            save(native / "bin" / name, read(root / "bin" / name), executable=True)
        save(
            root / "state/receiver-configured.json",
            encoded(dict(member_grants_created=0)),
        )
        # Use the matched decoder for future agent administration; preserve all
        # existing signer settings, finite rules and verifier identity.
        cli_path = home / ".local/bin/devgraph"
        save(root / "prior/devgraph-cli", read(cli_path, private=False))
        python = str(config.python_executable)
        require("'" not in python and "\n" not in python, "unsupported_python_path")
        script = (
            "#!/bin/sh\n'''exec' '"
            + python
            + "' \"$0\" \"$@\"\n' '''\n"
            + "import sys\nsys.path.insert(0, "
            + repr(str(root / "source"))
            + ")\n"
            + "from devgraph.cli import main\nraise SystemExit(main())\n"
        ).encode()
        replace(cli_path, script, review["inputs"][str(cli_path)], private=False)
        cli_path.chmod(0o700)
        agent = cli_read(home, ["auth", "work", "status"])
        before = review["previous_agent_status"]
        require(
            agent.get("ready") is True
            and agent.get("signer_matches") is True
            and agent.get("receiver_matches") is True
            and agent.get("actor_id") == before.get("actor_id")
            and agent.get("operations_authorized") == before.get("operations_authorized")
            and agent.get("arena_operations_authorized")
            == before.get("arena_operations_authorized"),
            "existing_agent_readback_failed",
        )
        save(root / "state/agent-authority-verified.json", encoded(agent))
        # Separate HTTPS service against the configured canonical graph. The
        # existing API and database jobs are neither stopped nor restarted.
        env = dict(
            HOME=str(home),
            PATH="/usr/bin:/bin:/usr/sbin:/sbin",
            LANG="en_US.UTF-8",
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONPATH=str(root / "source"),
            DEVGRAPH_DATA_ROOT=str(config.data_root),
            DEVGRAPH_SIGNED_READ_CONFIG=str(root / "runtime.json"),
            DEVGRAPH_LAN_ORIGIN=plan.wallet_endpoint,
            DEVGRAPH_LAN_NAME=plan.resource_name,
            NEO4J_URI="bolt://127.0.0.1:7687",
            NEO4J_USER="neo4j",
            NEO4J_DATABASE="neo4j",
            NEO4J_PASSWORD_FILE=str(config.data_root / "secrets/neo4j_password"),
        )
        label = (
            existing_service["label"] if existing_service else "ca.zenith.devgraph.wallet-endpoint"
        )
        plist = home / "Library/LaunchAgents" / (label + ".plist")
        if existing_service:
            require(sha(read(plist)) == existing_service["plist_sha256"], "selected_job_changed")
        else:
            require(not plist.exists(), "existing_wallet_endpoint_job")
        arguments = [
            str(config.python_executable),
            "-m",
            "uvicorn",
            "devgraph.local_access_runtime:create_app"
            if plan.schema_ == "zenith.devgraph-owner-setup.v3"
            else "devgraph.lan_access_runtime:create_app",
            "--factory",
            "--host",
            origin.hostname,
            "--port",
            str(origin.port),
            "--ssl-certfile",
            str(root / "tls/tls.crt"),
            "--ssl-keyfile",
            str(root / "tls/tls.key"),
            "--no-proxy-headers",
            "--no-access-log",
        ]
        directory(root / "logs")
        document = dict(
            Label=label,
            ProgramArguments=arguments,
            EnvironmentVariables=env,
            RunAtLoad=True,
            KeepAlive=False,
            WorkingDirectory=str(root),
            StandardOutPath=str(root / "logs/out.log"),
            StandardErrorPath=str(root / "logs/error.log"),
        )
        save(root / "launch-agent.plist", plistlib.dumps(document, sort_keys=True))
        if existing_service:
            # Preserve and replace only the previously verified, named instance.
            # A durable marker precedes bootout; unknown outcomes never retry.
            save(root / "prior/unclaimed-launch-agent.plist", read(plist))
            save(
                root / "state/unclaimed-cutover-started.json",
                encoded(dict(label=label, instance_id=existing_service["instance_id"])),
            )
            stopped = subprocess.run(
                ["/bin/launchctl", "bootout", "gui/" + str(os.geteuid()) + "/" + label],
                capture_output=True,
                timeout=20,
                env={},
            )
            require(stopped.returncode == 0, "selected_service_stop_requires_inspection")
            replace(plist, read(root / "launch-agent.plist"), existing_service["plist_sha256"])
        else:
            save(plist, read(root / "launch-agent.plist"))
        save(
            root / "state/listener-dispatch.json",
            encoded(dict(label=label, port=origin.port)),
        )
        result = subprocess.run(
            ["/bin/launchctl", "bootstrap", "gui/" + str(os.geteuid()), str(plist)],
            capture_output=True,
            timeout=20,
            env={},
        )
        require(result.returncode == 0, "listener_bootstrap_failed_inspect_job")
        context = ssl.create_default_context(cafile=str(root / "tls/tls.crt"))
        deadline = time.monotonic() + 30
        profile = None
        while time.monotonic() < deadline:
            try:
                connection = http.client.HTTPSConnection(
                    origin.hostname, origin.port, context=context, timeout=2
                )
                connection.request("GET", "/.well-known/devgraph-connection.json")
                response = connection.getresponse()
                raw = response.read(65537)
                connection.close()
                if response.status == 200 and len(raw) <= 65536:
                    profile = json.loads(raw)
                    break
            except (OSError, ValueError, http.client.HTTPException):
                pass
            time.sleep(0.25)
        require(
            profile is not None
            and profile["receiver"] == plan.wallet_endpoint
            and profile["issuer"] == review["connection"]["issuer"]
            and profile["authority"] == review["connection"]["authority"],
            "listener_verification_failed_inspect_job",
        )
        if existing_service:
            from devgraph.ops.service_owner import verify_active

            verify_active(root, existing_service)
        # A Wallet listener is another service instance of this graph. Do not
        # overwrite the private API registration with its address/profile.
        from devgraph.local_services import publish_service

        registration = publish_service(
            config_path,
            interface="wallet-api",
            endpoint=plan.wallet_endpoint,
            connection=review["connection"],
            home=home,
            now=int(time.time()),
            expected_registration_sha256=review["inputs"][
                str(config_path.with_name("resource-registration.json"))
            ],
        )
        require(registration["resource_id"] == plan.resource_id, "resource_identity_changed")
        state = dict(
            schema="devgraph.owner-install-result.v1",
            active=True,
            resource_id=plan.resource_id,
            endpoint=plan.wallet_endpoint,
            owner_public_key=plan.owner_public_key,
            member_grants_created=0,
            native_key_unchanged=True,
            database_restarted=False,
            private_api_unchanged=True,
            owner_wallet_confirmation_required=True,
            connection=review["connection"],
            root=str(root),
        )
        save(root / "state/active.json", encoded(state))
        return state
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "activate", "status"))
    parser.add_argument("--plan-file", type=Path, required=True)
    parser.add_argument("--native-holder", action="append", default=[])
    parser.add_argument("--review-digest")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.plan_file, holders=tuple(args.native_holder))
    elif args.command == "activate":
        result = activate(args.plan_file, args.review_digest)
    else:
        result = active_status(args.plan_file)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        reason = str(error) if isinstance(error, OwnerInstallError) else "owner_setup_failed_closed"
        print(
            json.dumps(
                dict(
                    ready=False,
                    reason=reason,
                    instruction="Preserve this run; inspect status before continuing.",
                )
            )
        )
        raise SystemExit(1) from None
