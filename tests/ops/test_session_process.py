"""Real Python provider stdio → Rust custody → native secS → scoped Work read."""

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.native_read.test_access_operator import signed

from devgraph.disposable_sessions import create_disposable_profile


def permissions_for(service):
    return [
        dict(
            operation="devgraph.work.get",
            resource="https://work.zenith-research.ca/devgraph",
            roles=[
                dict(
                    role="target",
                    selector=dict(
                        schema="secs.resource-selector.v1",
                        service=service,
                        kinds=["Task"],
                        selection=dict(mode="objects", refs=[dict(kind="Task", id="example-task")]),
                    ),
                )
            ],
            constraints=dict(
                scope="devgraph.work.read",
                operations=["get"],
                relationship_types=["parent"],
                work_kinds=["Task"],
                work_ids=["Task/example-task"],
                include_archived=False,
            ),
        )
    ]


@pytest.fixture
def process(tmp_path):
    names = (
        "CASTALIA_SESSION_TEST_BINARY",
        "SECS_HOLDER_GRANT_TEST_BINARY",
        "SECS_READ_AUTHORITY_TEST_BINARY",
    )
    if not all(os.environ.get(name) for name in names):
        pytest.skip("real qualified native session executables required")
    owner = Ed25519PrivateKey.generate()
    profile = create_disposable_profile(
        tmp_path / "fixture",
        owner.public_key().public_bytes_raw().hex(),
        session_binary=os.environ[names[0]],
        holder_binary=os.environ[names[1]],
        read_binary=os.environ[names[2]],
    )
    script = Path(__file__).resolve().parents[2] / "scripts/session_agent_service.py"
    child = subprocess.Popen(
        [sys.executable, "-I", str(script), "--profile-file", str(profile)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    def call(client, operation, **arguments):
        request = dict(
            schema="devgraph.session-provider-command.v1",
            request_id=secrets.token_hex(16),
            client_id=client,
            operation=operation,
            arguments=arguments,
        )
        child.stdin.write(json.dumps(request) + "\n")
        child.stdin.flush()
        raw = child.stdout.readline()
        assert raw, "provider process exited"
        value = json.loads(raw)
        assert value["request_id"] == request["request_id"]
        return value

    try:
        yield call, owner, profile
    finally:
        child.stdin.close()
        child.wait(timeout=10)


def test_complete_process_flow_recovers_exact_root_and_returns_only_business_data(process):
    call, owner, _ = process
    client = secrets.token_hex(16)
    service = call(client, "services")["value"]["services"][0]
    assert service["mode"] == "disposable"
    assert service["capabilities"]["session_write_issuance"] is False
    permissions = [
        dict(
            operation="devgraph.work.get",
            resource="https://work.zenith-research.ca/devgraph",
            roles=[
                dict(
                    role="target",
                    selector=dict(
                        schema="secs.resource-selector.v1",
                        service=service["service"],
                        kinds=["Task"],
                        selection=dict(mode="objects", refs=[dict(kind="Task", id="example-task")]),
                    ),
                )
            ],
            constraints=dict(
                scope="devgraph.work.read",
                operations=["get"],
                relationship_types=["parent"],
                work_kinds=["Task"],
                work_ids=["Task/example-task"],
                include_archived=False,
            ),
        )
    ]
    request = dict(
        service=service["service"],
        request_id=secrets.token_hex(16),
        permissions=permissions,
        expires_at=int(time.time()) + 300,
        display=dict(session_label="Fixture", purpose="Read one task"),
    )
    prepared = call(client, "prepare", **request)
    assert prepared["ok"]
    review = prepared["value"]["review"]
    assert call(client, "prepare", **request)["value"]["review"] == review
    handle = review["request_handle"]
    challenge = call(
        client,
        "owner_challenge",
        request_handle=handle,
        owner_public_key=owner.public_key().public_bytes_raw().hex(),
        installation_id="aa" * 16,
    )["value"]
    assert challenge["details"]["session_review"]["manifest"] == review["manifest"]
    issued = call(
        client,
        "submit",
        request_handle=handle,
        owner_proof=dict(challenge=challenge, signature=signed(owner, challenge)),
    )
    assert issued["ok"] and issued["value"]["state"] == "issued"
    result = issued["value"]
    assert call(client, "reconcile", request_handle=handle)["value"] == result
    assert call(client, "reconcile", request_handle=handle)["value"] == result
    assert call(secrets.token_hex(16), "reconcile", request_handle=handle)["ok"] is False
    get = dict(
        operation="devgraph.work.get",
        resource="https://work.zenith-research.ca/devgraph",
        parameters=dict(kind="Task", work_id="example-task"),
    )
    data = call(
        client,
        "execute",
        credential_handle=result["credential_handle"],
        request_id=secrets.token_hex(16),
        call=get,
    )
    assert data["ok"] and data["value"]["result"]["title"] == "Disposable example task"
    assert set(data["value"]) == {"schema", "request_id", "provider_service", "operation", "result"}
    get["parameters"]["work_id"] = "hidden-task"
    assert (
        call(
            client,
            "execute",
            credential_handle=result["credential_handle"],
            request_id=secrets.token_hex(16),
            call=get,
        )["ok"]
        is False
    )
    assert (
        call(
            secrets.token_hex(16),
            "execute",
            credential_handle=result["credential_handle"],
            request_id=secrets.token_hex(16),
            call=get,
        )["ok"]
        is False
    )


def test_invalid_client_cannot_create_files_or_choose_a_profile(process):
    call, _, profile = process
    result = call(
        "../escape",
        "prepare",
        service="unknown",
        request_id=secrets.token_hex(16),
        permissions=[],
        expires_at=int(time.time()) + 300,
        display={},
    )
    assert result["ok"] is False
    assert not (profile.parent.parent / "escape.json").exists()
    result = call(secrets.token_hex(16), "services", profile_file="do-not-read")
    assert result["ok"] is False


@pytest.mark.parametrize("lifetime", [60, 3600])
def test_relative_lifetime_starts_at_review_and_retry_cannot_extend_it(process, lifetime):
    call, _, _ = process
    client = secrets.token_hex(16)
    service = call(client, "services")["value"]["services"][0]["service"]
    request = dict(
        service=service,
        request_id=secrets.token_hex(16),
        permissions=permissions_for(service),
        lifetime_seconds=lifetime,
        display=dict(session_label="Finite session", purpose="Read one task"),
    )
    prepared = call(client, "prepare", **request)
    assert prepared["ok"]
    review = prepared["value"]["review"]
    assert review["manifest"]["expires_at"] - review["manifest"]["not_before"] == lifetime
    assert call(client, "prepare", **request)["value"]["review"] == review
    assert call(client, "prepare", **(request | {"lifetime_seconds": lifetime + 1}))["ok"] is False
    assert (
        call(client, "prepare", **(request | {"expires_at": int(time.time()) + 300}))["ok"] is False
    )
    assert call(client, "prepare", **request)["value"]["review"] == review
