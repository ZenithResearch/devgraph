"""Protected first-owner setup for an already launched, exact Wallet instance.

No HTTP bootstrap route or remote-selected executable. Existing TLS, issuer,
graph identity and launch job are reused; setup never chooses another endpoint.
"""

import argparse
import http.client
import json
import ssl
import subprocess
import time
from pathlib import Path

from devgraph.ops import owner_install as install
from devgraph.ops import wallet_admin_upgrade as owner
from devgraph.ops import wallet_service as service
from devgraph.ops.owner_setup import SHORT_OPERATIONS


def selected_service(home, resource_id, instance_id, endpoint):
    root = service.root_for(home, resource_id)
    launch = install.object_file(root / "launch-review.json", maximum=1048576)
    root, launch, deployment = service.checked(
        home, resource_id, install.sha(install.encoded(launch))
    )
    install.require(
        deployment["instance_id"] == instance_id and deployment["endpoint"] == endpoint,
        "selected_service_changed",
    )
    _config, _record, config_hash, registration_hash = service.selected(
        home, resource_id, deployment["source_endpoint"]
    )
    install.require(
        config_hash == deployment["configuration_sha256"]
        and registration_hash == deployment["registration_sha256"],
        "source_graph_changed",
    )
    service.probe(root, deployment)
    running = install.object_file(root / "state/running.json")
    label = "ca.zenith.devgraph.wallet-service." + instance_id
    install.require(
        running["instance_id"] == instance_id
        and running["endpoint"] == endpoint
        and running["label"] == label
        and running["owner_registered"] is False,
        "unclaimed_launch_not_verified",
    )
    plist = home / "Library/LaunchAgents" / (label + ".plist")
    raw = install.read(plist)
    install.require(raw == install.read(root / "launch-agent.plist"), "selected_job_changed")
    return dict(
        root=str(root),
        instance_id=instance_id,
        endpoint=endpoint,
        label=label,
        plist_sha256=install.sha(raw),
        connection=deployment["connection"],
        launch_review_digest=install.sha(install.encoded(launch)),
    )


def verify_active(root, selected):
    from urllib.parse import urlsplit

    target = urlsplit(selected["endpoint"])
    context = ssl.create_default_context(cafile=str(root / "tls/tls.crt"))
    connection = http.client.HTTPSConnection(
        target.hostname, target.port, context=context, timeout=5
    )
    try:
        connection.request("GET", "/live")
        response = connection.getresponse()
        raw = response.read(16385)
        install.require(response.status == 200 and len(raw) <= 16384, "active_service_not_ready")
        health = json.loads(raw)
        install.require(
            health.get("live") is True
            and health.get("mode") == "owner-local-wallet-administration"
            and health.get("instance_id") == selected["instance_id"]
            and health.get("resource_id") == root.parent.name,
            "active_service_identity_changed",
        )
        return health
    finally:
        connection.close()


def plan_path(home, resource_id):
    return service.root_for(home, resource_id) / "owner-registration/plan.json"


def retained(home, resource_id, instance_id, endpoint):
    path = plan_path(home, resource_id)
    plan = install.decode_plan(install.read(path, maximum=65536))
    install.require(
        plan.resource_id == resource_id and plan.wallet_endpoint == endpoint, "owner_target_changed"
    )
    state = install.active_status(path, home=home)
    if state["prepared"]:
        review = state["preparation"]["review"]
        existing = review.get("existing_service", {})
        install.require(
            existing.get("instance_id") == instance_id
            and existing.get("endpoint") == endpoint
            and existing.get("connection") == review["connection"],
            "selected_service_changed",
        )
    return path, plan, state


def inspect(resource_id, instance_id, endpoint, *, home=None):
    home = home or Path.home()
    if not plan_path(home, resource_id).exists():
        selected_service(home, resource_id, instance_id, endpoint)
        return dict(
            schema="devgraph.service-owner-status.v1",
            prepared=False,
            blocked=False,
            owner_registered=False,
            activation_attempted=False,
            active=False,
        )
    path, plan, state = retained(home, resource_id, instance_id, endpoint)
    result = dict(
        schema="devgraph.service-owner-status.v1",
        prepared=state["prepared"],
        blocked=not state["prepared"],
        plan=json.loads(install.read(path)),
        owner_registered=False,
        activation_attempted="activation-started" in state["phases"],
        active=state["active"],
    )
    if state["prepared"]:
        result["preparation"] = state["preparation"]
        try:
            proof = owner.active_owner(
                owner.registry_path(home, resource_id),
                resource_id=resource_id,
                grant_id=state["preparation"]["review_digest"],
                public_key=plan.owner_public_key,
            )
        except ValueError:
            proof = None
        if proof is not None:
            install.require(
                proof["binding"] == owner.expected_binding(plan, endpoint).__dict__,
                "owner_binding_changed",
            )
            result["owner_registered"] = True
    if result["active"]:
        install.require(result["owner_registered"], "owner_registration_required")
        verify_active(Path(state["root"]), state["preparation"]["review"]["existing_service"])
    elif result["owner_registered"] and {"listener-dispatch", "agent-authority-verified"}.issubset(
        state["phases"]
    ):
        # Recover an unknown final readback by inspecting the original listener.
        # No second claim, native provision or job dispatch is performed.
        try:
            verify_active(Path(state["root"]), state["preparation"]["review"]["existing_service"])
            result["active"] = True
        except (OSError, ValueError, http.client.HTTPException):
            pass
    return result


def prepare(resource_id, instance_id, endpoint, public_key, *, home=None, now=None):
    home = home or Path.home()
    now = int(time.time()) if now is None else now
    install.require(
        isinstance(public_key, str)
        and len(public_key) == 64
        and bytes.fromhex(public_key).hex() == public_key,
        "invalid_owner_key",
    )
    existing = selected_service(home, resource_id, instance_id, endpoint)
    path = plan_path(home, resource_id)
    install.require(not path.exists() and not path.is_symlink(), "inspect_retained_owner_setup")
    deployment = install.object_file(Path(existing["root"]) / "deployment.json")
    plan = dict(
        schema="zenith.devgraph-owner-setup.v3",
        status="prepared_not_applied",
        resource_id=resource_id,
        resource_name=deployment["name"],
        local_endpoint=deployment["source_endpoint"],
        wallet_endpoint=endpoint,
        registration_sha256=deployment["registration_sha256"],
        configuration_sha256=deployment["configuration_sha256"],
        owner_public_key=public_key,
        preset="project-manager",
        write_operations=list(SHORT_OPERATIONS),
        read_operations=["list", "get", "relationships"],
        scope_mode="global",
        paths=[],
        not_before=now,
        publish_hierarchy=False,
    )
    install.decode_plan(install.encoded(plan))
    install.save(path, install.encoded(plan))
    preparation = install.prepare(path, home=home, now=now, existing_service=existing)
    return dict(schema="devgraph.service-owner-preparation.v1", plan=plan, preparation=preparation)


def dispatch(
    command, resource_id, instance_id, endpoint, review_digest, *, home=None, proof_file=None
):
    home = home or Path.home()
    path, plan, state = retained(home, resource_id, instance_id, endpoint)
    install.require(
        state["prepared"] and state["preparation"]["review_digest"] == review_digest,
        "owner_review_changed",
    )
    # Preparation/registration must still target the running, unclaimed instance.
    # Read-only recovery remains available after the deliberate same-job cutover.
    if command in ("claim-prepare", "claim-refresh", "claim-complete"):
        selected_service(home, resource_id, instance_id, endpoint)
    if command in ("claim-prepare", "claim-refresh"):
        return owner.prepare_claim(
            path, review_digest, home=home, refresh=command == "claim-refresh"
        )
    if command == "claim-complete":
        return owner.complete_claim(path, review_digest, proof_file, home=home)
    if command == "claim-status":
        return owner.inspect_claim(path, review_digest, proof_file, home=home)
    install.require(command == "activate", "unknown_owner_command")
    return install.activate(path, review_digest, home=home)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "inspect",
            "prepare",
            "claim-prepare",
            "claim-refresh",
            "claim-complete",
            "claim-status",
            "activate",
        ),
    )
    parser.add_argument("--resource-id", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--owner-public-key")
    parser.add_argument("--review-digest")
    parser.add_argument("--proof-file", type=Path)
    args = parser.parse_args()
    try:
        selected = (args.resource_id, args.instance_id, args.endpoint)
        if args.command == "inspect":
            result = inspect(*selected)
        elif args.command == "prepare":
            result = prepare(*selected, args.owner_public_key)
        else:
            result = dispatch(
                args.command, *selected, args.review_digest, proof_file=args.proof_file
            )
        print(install.encoded(result).decode())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print(
            json.dumps(
                dict(
                    error="service_owner_step_failed",
                    instruction="Inspect retained setup; do not retry uncertain activation.",
                )
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
