"""Prepare/launch one unclaimed Wallet interface for the exact actual graph.

This is service lifecycle only: no owner registration, bootstrap token, native
policy provisioning, member grants, migrations or private API restart.
"""

import argparse
import http.client
import json
import os
import plistlib
import socket
import ssl
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devgraph.local_access_runtime import local_origin
from devgraph.local_host import load_local_config
from devgraph.local_resources import validate_connection, validate_record
from devgraph.local_services import instance_id, publish_service
from devgraph.ops import owner_install as install


def base(home):
    return home / "Library/Application Support/Zenith/Devgraph"


def root_for(home, resource_id):
    install.require(str(UUID(resource_id)) == resource_id, "invalid_resource")
    return base(home) / "wallet-services" / resource_id


def selected(home, resource_id, source_endpoint):
    config = base(home) / "local.json"
    registration = config.with_name("resource-registration.json")
    raw = install.read(registration)
    record = validate_record(json.loads(raw))
    install.require(
        record["resource_id"] == resource_id
        and record["endpoint"] == source_endpoint
        and record["connection"] is None,
        "selected_graph_changed",
    )
    status = install.cli_read(home, ["local", "status"])
    install.require(
        status.get("healthy") is True
        and status.get("api", {}).get("api", {}).get("base_url") == source_endpoint,
        "selected_graph_not_ready",
    )
    return config, record, install.sha(install.read(config)), install.sha(raw)


def code_manifest():
    import devgraph

    source = Path(devgraph.__file__).resolve().parent.parent
    manifest = source.parent / "manifest.json"
    raw = install.read(manifest)
    return source, install.sha(raw)


def validate_deployment(value, *, root):
    install.require(
        set(value)
        == {
            "schema",
            "state",
            "resource_id",
            "instance_id",
            "name",
            "endpoint",
            "source_endpoint",
            "configuration_sha256",
            "registration_sha256",
            "connection",
            "created_at",
            "code_manifest_sha256",
        }
        and value["schema"] == "devgraph.unclaimed-wallet-service.v1"
        and value["state"] == "unclaimed",
        "invalid_deployment",
    )
    origin = local_origin(value["endpoint"])
    install.require(
        value["instance_id"] == instance_id(value["resource_id"], "wallet-api", origin),
        "service_identity_changed",
    )
    profile = validate_connection(value["connection"])
    install.require(
        profile["receiver"] == origin
        and profile["name"] == value["name"]
        and profile["network"] == "local",
        "profile_changed",
    )
    install.require(
        root.is_absolute() and root.resolve() == root and root.name == value["resource_id"],
        "deployment_root_changed",
    )
    for field in ("configuration_sha256", "registration_sha256", "code_manifest_sha256"):
        install.require(
            isinstance(value[field], str)
            and len(value[field]) == 64
            and bytes.fromhex(value[field]).hex() == value[field],
            "invalid_binding",
        )
    return value


def prepare(resource_id, source_endpoint, *, home=None, now=None):
    home = home or Path.home()
    now = int(time.time()) if now is None else now
    config, graph, config_hash, registration_hash = selected(home, resource_id, source_endpoint)
    root = root_for(home, resource_id)
    install.require(
        not root.exists() and not root.is_symlink(), "existing_service_requires_inspection"
    )
    # A running, unclaimed service must never silently replace an owner or old
    # partial activation. Existing agent authority is unrelated and preserved.
    install.require(
        not (base(home) / "owners" / resource_id / "registry.sqlite").exists(),
        "existing_owner_requires_review",
    )
    prior = base(home) / "wallet-endpoints" / resource_id
    install.require(
        not prior.exists() or not any(prior.iterdir()), "prior_owner_setup_requires_inspection"
    )
    source, code_hash = code_manifest()
    with socket.socket() as port:
        port.bind(("127.0.0.1", 0))
        endpoint = local_origin(f"https://127.0.0.1:{port.getsockname()[1]}")
    install.directory(root)
    os.chmod(root, 0o700)
    key = Ed25519PrivateKey.generate()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        .hex()
    )
    seed = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    install.save(root / "authority/read.key", seed.hex().encode() + b"\n")
    tls_key, tls_cert, fingerprint = install.tls_material("127.0.0.1", now)
    install.save(root / "tls/tls.key", tls_key)
    install.save(root / "tls/tls.crt", tls_cert)
    issuer = "devgraph-" + resource_id
    connection = dict(
        name=graph["name"],
        receiver=endpoint,
        issuer=issuer,
        authority=dict(
            issuer=issuer,
            key_id=issuer + "-read-v1",
            public_key=public,
            receiver=endpoint,
            audience="devgraph",
        ),
        network="local",
        tls_certificate_sha256=fingerprint,
    )
    deployment = dict(
        schema="devgraph.unclaimed-wallet-service.v1",
        state="unclaimed",
        resource_id=resource_id,
        instance_id=instance_id(resource_id, "wallet-api", endpoint),
        name=graph["name"],
        endpoint=endpoint,
        source_endpoint=source_endpoint,
        configuration_sha256=config_hash,
        registration_sha256=registration_hash,
        connection=connection,
        created_at=now,
        code_manifest_sha256=code_hash,
    )
    validate_deployment(deployment, root=root)
    install.save(root / "deployment.json", install.encoded(deployment))
    install.save(root / "connection.json", install.encoded(connection))
    review = dict(
        schema="devgraph.wallet-service-launch-review.v1",
        deployment=deployment,
        code_source=str(source),
        staged=install.inventory(root),
        administration_enabled=False,
        owner_registered=False,
        native_policy_changed=False,
        member_grants_created=0,
        private_api_unchanged=True,
        database_restart=False,
        global_trust_changed=False,
    )
    install.save(root / "launch-review.json", install.encoded(review))
    return dict(review=review, review_digest=install.sha(install.encoded(review)))


def checked(home, resource_id, review_digest):
    root = root_for(home, resource_id)
    review = install.object_file(root / "launch-review.json", maximum=1048576)
    install.require(install.sha(install.encoded(review)) == review_digest, "launch_review_changed")
    deployment = validate_deployment(install.object_file(root / "deployment.json"), root=root)
    install.require(deployment == review["deployment"], "deployment_changed")
    for name, digest in review["staged"].items():
        file = root / name
        install.require(
            ".." not in Path(name).parts
            and file.is_relative_to(root)
            and install.sha(install.read(file)) == digest,
            "staged_service_changed",
        )
    return root, review, deployment


def probe(root, deployment):
    url = urlsplit(deployment["endpoint"])
    context = ssl.create_default_context(cafile=str(root / "tls/tls.crt"))
    connection = http.client.HTTPSConnection(url.hostname, url.port, context=context, timeout=2)
    try:
        connection.request("GET", "/live")
        response = connection.getresponse()
        raw = response.read(16385)
        install.require(response.status == 200 and len(raw) <= 16384, "service_not_ready")
        result = json.loads(raw)
        install.require(
            result
            == dict(
                live=True,
                mode="owner-local-wallet-unclaimed",
                resource_id=deployment["resource_id"],
                instance_id=deployment["instance_id"],
                owner_registered=False,
                administration_enabled=False,
            ),
            "service_identity_changed",
        )
        return result
    finally:
        connection.close()


def _launch_locked(resource_id, review_digest, *, home=None):
    home = home or Path.home()
    root, review, deployment = checked(home, resource_id, review_digest)
    config_path, _, config_hash, registration_hash = selected(
        home, resource_id, deployment["source_endpoint"]
    )
    install.require(
        config_hash == deployment["configuration_sha256"]
        and registration_hash == deployment["registration_sha256"],
        "source_graph_changed",
    )
    source, code_hash = code_manifest()
    install.require(
        str(source) == review["code_source"] and code_hash == deployment["code_manifest_sha256"],
        "runtime_package_changed",
    )
    config = load_local_config(config_path)
    state = root / "state"
    install.require(not state.exists(), "prior_launch_requires_inspection")
    label = "ca.zenith.devgraph.wallet-service." + deployment["instance_id"]
    plist = home / "Library/LaunchAgents" / (label + ".plist")
    install.require(not plist.exists() and not plist.is_symlink(), "existing_service_job")
    url = urlsplit(deployment["endpoint"])
    with socket.socket() as port:
        port.bind(("127.0.0.1", url.port))  # Refuse any occupied port; never stop it.
    install.directory(root / "logs")
    document = dict(
        Label=label,
        ProgramArguments=[
            str(config.python_executable),
            "-m",
            "uvicorn",
            "devgraph.wallet_service_runtime:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(url.port),
            "--ssl-certfile",
            str(root / "tls/tls.crt"),
            "--ssl-keyfile",
            str(root / "tls/tls.key"),
            "--no-proxy-headers",
            "--no-access-log",
        ],
        EnvironmentVariables=dict(
            HOME=str(home),
            PATH="/usr/bin:/bin:/usr/sbin:/sbin",
            LANG="en_US.UTF-8",
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONPATH=str(source),
            DEVGRAPH_WALLET_SERVICE_DEPLOYMENT=str(root / "deployment.json"),
        ),
        RunAtLoad=True,
        KeepAlive=False,
        WorkingDirectory=str(root),
        StandardOutPath=str(root / "logs/out.log"),
        StandardErrorPath=str(root / "logs/error.log"),
    )
    install.save(root / "launch-agent.plist", plistlib.dumps(document, sort_keys=True))
    install.save(plist, install.read(root / "launch-agent.plist"))
    install.save(
        state / "launch-dispatch.json",
        install.encoded(dict(label=label, review_digest=review_digest, at=int(time.time()))),
    )
    result = subprocess.run(
        ["/bin/launchctl", "bootstrap", "gui/" + str(os.geteuid()), str(plist)],
        capture_output=True,
        timeout=20,
        env={},
    )
    install.require(result.returncode == 0, "service_start_outcome_requires_inspection")
    deadline = time.monotonic() + 30
    while True:
        try:
            health = probe(root, deployment)
            break
        except (OSError, ValueError, http.client.HTTPException):
            install.require(time.monotonic() < deadline, "service_readiness_requires_inspection")
            time.sleep(0.25)
    published = publish_service(
        config_path,
        interface="wallet-api",
        endpoint=deployment["endpoint"],
        connection=deployment["connection"],
        home=home,
        now=int(time.time()),
        expected_registration_sha256=registration_hash,
    )
    receipt = dict(
        schema="devgraph.wallet-service-launch-result.v1",
        running=True,
        endpoint=deployment["endpoint"],
        resource_id=resource_id,
        instance_id=published["instance_id"],
        owner_registered=False,
        administration_enabled=False,
        label=label,
        health=health,
        private_api_unchanged=True,
        native_policy_changed=False,
        member_grants_created=0,
        global_trust_changed=False,
    )
    install.save(state / "running.json", install.encoded(receipt))
    return receipt


def launch(resource_id, review_digest, *, home=None):
    import fcntl
    import stat

    home = home or Path.home()
    root = root_for(home, resource_id)
    # Validate the retained review before opening even the local launch lock.
    checked(home, resource_id, review_digest)
    fd = os.open(root / ".launch.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        install.require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and info.st_nlink == 1
            and info.st_mode & 0o077 == 0,
            "unsafe_launch_lock",
        )
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _launch_locked(resource_id, review_digest, home=home)
    finally:
        os.close(fd)


def status(resource_id, *, home=None):
    home = home or Path.home()
    root = root_for(home, resource_id)
    if not root.exists():
        return dict(prepared=False, running=False)
    review = install.object_file(root / "launch-review.json", maximum=1048576)
    root, review, deployment = checked(home, resource_id, install.sha(install.encoded(review)))
    try:
        health = probe(root, deployment)
    except (OSError, ValueError, http.client.HTTPException):
        health = None
    return dict(
        prepared=True,
        running=health is not None,
        deployment=deployment,
        review_digest=install.sha(install.encoded(review)),
        launch_dispatched=(root / "state/launch-dispatch.json").exists(),
        health=health,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "launch", "status"))
    parser.add_argument("--resource-id", required=True)
    parser.add_argument("--source-endpoint")
    parser.add_argument("--review-digest")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.resource_id, args.source_endpoint)
        elif args.command == "launch":
            result = launch(args.resource_id, args.review_digest)
        else:
            result = status(args.resource_id)
        print(install.encoded(result).decode())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print(
            json.dumps(
                dict(
                    error="wallet_service_step_failed",
                    instruction="Inspect retained launch state; do not retry.",
                )
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
