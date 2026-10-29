"""Private broker wrappers over the real receiver; no broker-owned key custody."""

import copy
from functools import partial

import pytest
from tests.auth.test_write_scope import create
from tests.native_read.test_holder_session_exchange import read_request, setup, status
from tests.native_read.test_signed_authority import NOW

from devgraph.auth.enforcement import AuditLog
from devgraph.holder_session_provider import HolderSessionApplication, dispatch
from devgraph.storage.memory import MemoryGraphStorage


def command(operation, arguments):
    return {
        "schema": "devgraph.holder-session-provider-command.v1",
        "request_id": "ab" * 16,
        "client_id": "cd" * 16,
        "operation": operation,
        "arguments": arguments,
    }


def application(tmp_path):
    s, auth, key, body, provider, _ = setup(tmp_path)
    provider.verify_request = partial(provider.verify_request, now=NOW)
    s.host.current_read = partial(s.host.current_read, now=NOW)
    storage = MemoryGraphStorage()
    create(storage, "Task", "allowed-1")
    create(storage, "Task", "hidden")
    app = HolderSessionApplication(
        sessions=s,
        storage=storage,
        audit_log=AuditLog(),
        name="Example graph",
        context={"origin": "https://graph.example", "audience": "devgraph"},
        now=lambda: NOW,
    )
    return app, auth, key, body, provider


def test_private_wrappers_use_original_exchange_and_external_temporary_key(tmp_path):
    app, auth, key, body, _ = application(tmp_path)
    services = dispatch(app, command("services", {}))
    assert services["schema"] == "devgraph.holder-session-services.v1"
    assert services["services"][0]["issuer_pin"] == app.sessions.host.pin
    prepared = dispatch(app, command("prepare", {"body": body}))
    assert prepared["challenge"] == auth["challenge"]
    issued = dispatch(app, command("exchange", {"authorization": auth}))
    assert issued["state"] == "issued"
    recovered = dispatch(app, command("status", {"proof": status(key, auth["challenge"])}))
    assert recovered == issued
    result = dispatch(
        app, command("execute", {"presentation": read_request(key, issued["credential"])})
    )
    assert result["schema"] == "devgraph.holder-session-call-result.v1"
    assert result["service"] == app.sessions.host.service
    assert [item["key"] for item in result["result"]["items"]] == ["Task/allowed-1"]
    assert "parent_grant" not in result
    assert not hasattr(app, "session_key") and not hasattr(app, "session_binary")


@pytest.mark.parametrize(
    "variant",
    [
        "old_schema",
        "origin_argument",
        "owner_challenge",
        "update_expiry",
        "close_client",
        "key_import",
    ],
)
def test_old_or_caller_selected_authority_commands_reject(tmp_path, variant):
    app, _, _, _, _ = application(tmp_path)
    value = command("services", {})
    if variant == "old_schema":
        value["schema"] = "devgraph.session-provider-command.v1"
    elif variant == "origin_argument":
        value["arguments"] = {"origin": "https://other.example"}
    else:
        value["operation"] = variant
    with pytest.raises(ValueError):
        dispatch(app, value)


def test_private_prepare_and_exchange_cannot_override_profile_context(tmp_path):
    app, auth, _, body, _ = application(tmp_path)
    body = copy.deepcopy(body)
    body["origin"] = "https://other.example"
    with pytest.raises(ValueError):
        dispatch(app, command("prepare", {"body": body}))
    auth = copy.deepcopy(auth)
    auth["challenge"]["audience"] = "other"
    with pytest.raises(ValueError):
        dispatch(app, command("exchange", {"authorization": auth}))
