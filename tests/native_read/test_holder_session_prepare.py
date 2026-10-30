"""Offline preparation must preserve request intent without a registry oracle."""

import copy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.native_read.test_holder_grants import host
from tests.native_read.test_signed_authority import NOW

from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.holder_sessions import HolderSessionPolicy, compile_prepare


def setup(tmp_path, monkeypatch):
    h, _, _, args, _, _, _ = host(tmp_path)
    h.grant_schema = "secs.holder-grant.v2"
    root = h.issue(**args)["grant"]
    claims = copy.deepcopy(root["claims"])
    write = copy.deepcopy(claims["permissions"][0])
    write["operation"] = "devgraph.work.patch"
    claims["permissions"].append(write)
    root = h.invoke("issue_v2", claims=claims, now=int(NOW.timestamp()))
    request = {
        "schema": "secs.holder-session-prepare.v1",
        "request_id": "44" * 16,
        "parent_grant": root,
        "installation_id": "a" * 32,
        "session_installation_id": "b" * 32,
        "session_public_key": Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex(),
        "origin": "https://graph.example",
        "audience": "devgraph",
        "permissions": None,
        "requested_ttl_seconds": 300,
    }
    policy = HolderSessionPolicy.model_validate(
        {"enabled": True, "allowed_origins": [request["origin"]]}
    )

    def no_registry(*_args, **_kwargs):
        raise AssertionError("offline prepare must not inspect canonical registry state")

    monkeypatch.setattr(h.provider, "_database", no_registry)
    return h, policy, request


def test_default_is_off_and_unknown_policy_fields_reject():
    from pydantic import ValidationError

    assert HolderSessionPolicy().enabled is False
    with pytest.raises(UnauthenticatedError, match="holder_sessions_not_enabled"):
        compile_prepare(None, HolderSessionPolicy(), {}, now=NOW)
    with pytest.raises(ValidationError):
        HolderSessionPolicy.model_validate({"enabled": True, "allow_writes": True})


def test_null_permissions_select_only_trusted_read_catalogue(tmp_path, monkeypatch):
    h, policy, body = setup(tmp_path, monkeypatch)
    challenge = compile_prepare(h, policy, body, now=NOW)
    assert challenge["permissions"]
    assert {p["operation"] for p in challenge["permissions"]} <= {
        "devgraph.work.get",
        "devgraph.work.list",
        "devgraph.work.relationships",
        "devgraph.arena.get",
        "devgraph.arena.list",
    }
    assert challenge["session_installation_id"] != challenge["installation_id"]
    current = int(NOW.timestamp())
    assert current < challenge["expires_at"] <= current + 300
    assert challenge["review_deadline"] == min(current + 120, challenge["expires_at"])
    assert "parent_grant" not in challenge


def test_explicit_array_keeps_exact_order_and_scope(tmp_path, monkeypatch):
    h, policy, body = setup(tmp_path, monkeypatch)
    reads = [
        p
        for p in body["parent_grant"]["claims"]["permissions"]
        if p["operation"] in {"devgraph.work.get", "devgraph.work.list"}
    ]
    body["permissions"] = list(reversed(reads))
    assert compile_prepare(h, policy, body, now=NOW)["permissions"] == body["permissions"]


@pytest.mark.parametrize(
    "variant", ["write", "alias", "origin", "parent", "empty", "ttl", "malformed"]
)
def test_preparation_rejects_untrusted_or_non_read_requests(tmp_path, monkeypatch, variant):
    h, policy, body = setup(tmp_path, monkeypatch)
    if variant in {"write", "alias"}:
        body["permissions"] = [copy.deepcopy(body["parent_grant"]["claims"]["permissions"][-1])]
        if variant == "alias":
            body["permissions"][0]["operation"] = "devgraph.work.read_admin"
    elif variant == "origin":
        body["origin"] = "https://other.example"
    elif variant == "parent":
        body["parent_grant"]["claims"]["revision"] = "99" * 32
    elif variant == "empty":
        body["permissions"] = []
    elif variant == "malformed":
        body["parent_grant"]["claims"]["permissions"] = ["invalid"]
    else:
        body["requested_ttl_seconds"] = 0
    with pytest.raises((UnauthenticatedError, ValueError)):
        compile_prepare(h, policy, body, now=NOW)


def test_v1_parent_is_explicitly_unsupported_without_conversion(tmp_path, monkeypatch):
    h, policy, body = setup(tmp_path, monkeypatch)
    body["parent_grant"]["claims"]["schema"] = "secs.holder-grant.v1"
    with pytest.raises(UnauthenticatedError, match="holder_session_parent_unsupported"):
        compile_prepare(h, policy, body, now=NOW)
