"""Direct local secS handler; no HTTP client, server, routing or local-auth bypass.

The fixed secS executable verifies the signed envelope before dispatch. The
canonical operator rechecks current policy, signature, challenge, replay, scope
and assignment under its existing transaction. HTTPS uses that same operator.
"""

import json
import sqlite3
import sys
from pathlib import Path

from devgraph.api.native_read import _unique
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.local_services import instance_id
from devgraph.signed_read_runtime import ReadRuntimeConfiguration, private_json

MAXIMUM = 65536
RESPONSE_MAXIMUM = 1048576


def dispatch(operator, operation, body):
    """Transport-neutral command boundary. It never substitutes owner authority."""
    try:
        if operation == "challenge":
            if set(body) != {"subject_public_key", "installation_id", "action"}:
                raise ValueError("invalid request")
            value = operator.challenge(
                body["subject_public_key"],
                body["installation_id"],
                operator.parse_action(body["action"]),
            )
        elif operation == "execute":
            if set(body) != {"challenge", "signature"}:
                raise ValueError("invalid request")
            value = operator.execute(body["challenge"], body["signature"])
        else:
            raise ValueError("unknown operation")
        return 200, value
    except UnauthenticatedError as error:
        if not getattr(error, "_access_operator_audited", False):
            try:
                operator.record_denial("local_rpc_request_rejected")
            except sqlite3.Error:
                return 503, {"error": "operator_result_unknown"}
        reason = str(error)
        for internal, public, status in (
            ("request_changed", "operator_request_changed", 409),
            ("assignment_changed", "operator_assignment_changed", 409),
            ("write_activation_unavailable", "write_activation_unavailable", 400),
            ("write_activation_upgrade_required", "write_activation_upgrade_required", 400),
            ("mixed_activation_unavailable", "mixed_activation_unavailable", 400),
            ("native_admission_required", "native_admission_required", 400),
            ("activation_unavailable", "activation_unavailable", 400),
            ("activation_upgrade_required", "activation_upgrade_required", 400),
        ):
            if reason == internal:
                return status, {"error": public}
        return 401, {"error": "operator_access_denied"}
    except (ValueError, TypeError, RecursionError):
        try:
            operator.record_denial("invalid_local_rpc_selection")
        except sqlite3.Error:
            return 503, {"error": "operator_result_unknown"}
        return 400, {"error": "invalid_operator_selection"}
    except sqlite3.Error:
        return 503, {"error": "operator_result_unknown"}


def selected(configuration, request):
    expected = {
        "schema",
        "request_id",
        "resource_id",
        "instance_id",
        "receiver",
        "issuer",
        "profile_sha256",
        "operation",
        "body",
    }
    if set(request) != expected or request["schema"] != "secs.local-admin-request.v1":
        raise ValueError("invalid local RPC envelope")
    for key in ("resource_id", "instance_id", "receiver", "issuer", "profile_sha256"):
        if request[key] != configuration[key]:
            raise ValueError("selected receiver changed")
    runtime = ReadRuntimeConfiguration.model_validate(private_json(Path(configuration["runtime"])))
    if runtime.schema_ not in (
        "devgraph.signed-read-runtime.v9",
        "devgraph.signed-read-runtime.v10",
    ):
        raise ValueError("owner runtime required")
    review = private_json(runtime.owner_install_review_file, maximum_bytes=262144)
    published = runtime.schema_ == "devgraph.signed-read-runtime.v10"
    if configuration.get("schema") == "secs.local-admin-config.v3" and not published:
        raise ValueError("secondary HTTPS RPC requires published owner runtime")
    profile = (
        private_json(runtime.owner_endpoint_profile_file) if published else review["connection"]
    )
    service_id = (
        instance_id(review["resource_id"], "wallet-api", profile["receiver"])
        if published
        else review["existing_service"]["instance_id"]
    )
    import hashlib

    if (
        review["resource_id"] != configuration["resource_id"]
        or service_id != configuration["instance_id"]
        or profile["receiver"] != configuration["receiver"]
        or profile["issuer"] != configuration["issuer"]
        or hashlib.sha256(canonical_json(profile)).hexdigest() != configuration["profile_sha256"]
    ):
        raise ValueError("installed owner binding changed")
    if configuration.get("schema") == "secs.local-admin-config.v3":
        from devgraph.local_services import validate_service

        service = validate_service(
            private_json(Path.home() / ".zenith/services/v1" / (service_id + ".json"))
        )
        if (
            service["resource_id"] != review["resource_id"]
            or service["interface"] != "wallet-api"
            or service["endpoint"] != profile["receiver"]
            or service["connection"] != profile
        ):
            raise ValueError("registered secondary endpoint changed")


def run():
    if len(sys.argv) != 3:
        raise ValueError("fixed configuration required")
    # runpy keeps the source path as argv[1], configuration as argv[2].
    path = Path(sys.argv[2])
    configuration = private_json(path)
    expected = Path.home() / ".zenith/rpc/v1" / (configuration["instance_id"] + ".json")
    if path != expected:
        raise ValueError("fixed configuration required")
    raw = sys.stdin.buffer.read(MAXIMUM + 1)
    if len(raw) > MAXIMUM:
        raise ValueError("request too large")
    request = json.loads(raw, object_pairs_hook=_unique)
    selected(configuration, request)
    # This factory composes the canonical handler/storage but opens no listener.
    from devgraph.signed_read_runtime import create_app, owner_operator_policy

    if request["operation"] == "inspect":
        runtime = ReadRuntimeConfiguration.model_validate(
            private_json(Path(configuration["runtime"]))
        )
        owner_operator_policy(runtime, data_root=configuration["data_root"])
        value = dict(
            transport="local-secs-rpc",
            owner_registered=True,
            credential_administration=True,
            service_configuration=False,
            https_hosted_by_transfer=False,
        )
        result = dict(
            schema="secs.local-admin-response.v1",
            request_id=request["request_id"],
            resource_id=request["resource_id"],
            instance_id=request["instance_id"],
            receiver=request["receiver"],
            status=200,
            body=value,
        )
        sys.stdout.buffer.write(canonical_json(result) + b"\n")
        return
    app = create_app()
    operator = app.state.access_operator
    try:
        status, value = dispatch(operator, request["operation"], request["body"])
    finally:
        storage = getattr(operator, "storage", None)
        if storage is not None:
            storage.close()
    result = dict(
        schema="secs.local-admin-response.v1",
        request_id=request["request_id"],
        resource_id=request["resource_id"],
        instance_id=request["instance_id"],
        receiver=request["receiver"],
        status=status,
        body=value,
    )
    encoded = canonical_json(result)
    if len(encoded) > RESPONSE_MAXIMUM:
        raise ValueError("result too large")
    sys.stdout.buffer.write(encoded + b"\n")


if __name__ == "__main__":
    try:
        run()
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        # No request, private policy, exception text or subprocess stderr escapes.
        sys.stdout.write('{"schema":"secs.local-admin-error.v1","error":"result_unknown"}\n')
        raise SystemExit(1) from None
