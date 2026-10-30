"""Actual session exchange and scoped read through the bounded HTTPS surface."""

from functools import partial
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from tests.auth.test_write_scope import create
from tests.native_read.test_holder_grants import host
from tests.native_read.test_holder_session_exchange import read_request, setup
from tests.native_read.test_http import sign
from tests.native_read.test_signed_authority import NOW

from devgraph.api.holder_sessions import register_holder_sessions
from devgraph.api.wallet_monitor import register_wallet_monitor
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.errors import UnauthenticatedError
from devgraph.endpoint_read_runtime import EndpointReadBoundary
from devgraph.storage.memory import MemoryGraphStorage


def test_popup_preflight_and_invalid_holder_signature_do_not_issue_grants(tmp_path, monkeypatch):
    holder, provider, owner, args, _, _, _ = host(tmp_path)
    retained = holder.issue(**args)
    s = SimpleNamespace(host=holder)
    calls = []
    original = provider.holder_grants.issue_v2

    def issue(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs, now=NOW)

    monkeypatch.setattr(provider.holder_grants, "issue_v2", issue)
    target = EndpointReadBoundary(app(s, provider), origin="https://graph.example")
    origin = "chrome-extension://" + "a" * 32
    with TestClient(target, base_url="https://graph.example") as client:
        for path in ("/monitor/wallet-access/v1/challenges", "/monitor/wallet-access/v1/grants/v2"):
            preflight = client.options(
                path,
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            assert preflight.status_code == 204
        assert not calls
        response = client.post(
            "/monitor/wallet-access/v1/grants/v2", json={}, headers={"Origin": origin}
        )
        assert response.status_code == 401
        assert response.headers["access-control-allow-origin"] == origin
        assert not calls
        fresh = provider.challenge(owner.public_key().public_bytes_raw().hex(), "a" * 32, now=NOW)
        signature = sign(owner, fresh)
        bad_signature = ("B" if signature[0] == "A" else "A") + signature[1:]
        denied = client.post(
            "/monitor/wallet-access/v1/grants/v2",
            json={"challenge": fresh, "proof": args["proof"], "signature": bad_signature},
            headers={"Origin": origin},
        )
        assert denied.status_code == 401
        assert denied.headers["access-control-allow-origin"] == origin
        assert len(calls) == 1  # Valid shape reached actual native admission.
        with provider._database() as db:
            assert db.execute("SELECT COUNT(*) FROM holder_roots").fetchone()[0] == 1
        assert holder.issue(**args) == retained


def app(s, provider):
    storage = MemoryGraphStorage()
    for identifier in ("allowed-1", "allowed-2", "hidden"):
        create(storage, "Task", identifier)
    provider.verify_request = partial(provider.verify_request, now=NOW)
    s.host.current_read = partial(s.host.current_read, now=NOW)
    target = FastAPI()

    @target.exception_handler(UnauthenticatedError)
    async def denied(_request, _error):
        return JSONResponse({"error": "access_denied"}, status_code=401)

    register_wallet_monitor(target, storage=storage, enrollment=provider, audit_log=AuditLog())
    register_holder_sessions(target, host=s.host, now=lambda: NOW)
    return target


def test_http_exchange_read_after_wallet_lock_and_cross_origin_transport(tmp_path):
    s, auth, key, body, provider, _ = setup(tmp_path)
    origin = "https://graph.example"
    with TestClient(
        EndpointReadBoundary(app(s, provider), origin=origin), base_url=origin
    ) as client:
        extension = {"Origin": "chrome-extension://example"}
        response = client.get("/holder-sessions/v1/capabilities", headers=extension)
        assert response.status_code == 200
        caps = response.json()
        assert caps["supported"] is True
        assert caps["challenge_path"] == "/holder-sessions/v1/challenges"
        assert (
            client.get("/holder-sessions/v1/capabilities?selected_base=/evil").json()[
                "challenge_path"
            ]
            == caps["challenge_path"]
        )
        assert response.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in response.headers
        assert (
            client.options(
                caps["exchange_path"],
                headers={
                    **extension,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            ).status_code
            == 204
        )
        challenge = client.post(caps["challenge_path"], json=body, headers=extension).json()
        assert challenge["challenge"] == auth["challenge"]
        issued = client.post(caps["exchange_path"], json=auth, headers=extension)
        assert issued.status_code == 200, issued.text
        token = issued.json()["credential"]
        # No owner key or Wallet API is called for business requests.
        request = read_request(key, token)
        response = client.post(
            "/monitor/wallet-access/v1/requests", json=request, headers={"Origin": origin}
        )
        assert response.status_code == 200, response.text
        assert [item["key"] for item in response.json()["items"]] == [
            "Task/allowed-1",
            "Task/allowed-2",
        ]
        assert (
            client.post(
                "/monitor/wallet-access/v1/requests", json=request, headers={"Origin": origin}
            ).status_code
            == 401
        )
        assert (
            client.post("/work-operations/v1", json=request, headers={"Origin": origin}).status_code
            == 404
        )
        assert (
            client.post(
                caps["exchange_path"],
                json=auth,
                headers={"Origin": "https://other.example", "Host": "evil.example"},
            ).status_code
            == 421
        )


def test_new_transport_cors_does_not_relax_old_routes(tmp_path):
    s, _, _, _, provider, _ = setup(tmp_path)
    origin = "https://graph.example"
    with TestClient(
        EndpointReadBoundary(app(s, provider), origin=origin), base_url=origin
    ) as client:
        assert (
            client.post(
                "/monitor/wallet-access/v1/requests",
                json={},
                headers={"Origin": "chrome-extension://example"},
            ).status_code
            == 421
        )
        assert (
            client.options(
                "/monitor/wallet-access/v1/requests", headers={"Origin": origin}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/holder-sessions/v1/exchange",
                content=b"x" * 131073,
                headers={"Origin": "chrome-extension://example"},
            ).status_code
            == 413
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b'{"x":1.0}',
        b'{"x":9007199254740992}',
        b'{"x":NaN}',
        b'{"x":"\\ud800"}',
        b"[" * 34 + b"0" + b"]" * 34,
    ],
)
def test_new_transport_rejects_unsafe_json_before_exchange(tmp_path, raw):
    s, _, _, _, provider, _ = setup(tmp_path)
    with TestClient(app(s, provider), base_url="https://graph.example") as client:
        result = client.post("/holder-sessions/v1/exchange", content=raw)
        assert result.status_code == 401
        assert result.json() == {"error": "holder_session_denied"}


def test_capabilities_default_unsupported_without_policy(tmp_path):
    s, _, _, _, provider, _ = setup(tmp_path)
    del s.host.holder_sessions
    with TestClient(app(s, provider), base_url="https://graph.example") as client:
        assert client.get("/holder-sessions/v1/capabilities").json()["supported"] is False
        assert client.post("/holder-sessions/v1/exchange", json={}).status_code == 501


def test_canonical_prefix_is_retained_in_capabilities_and_bounded_transport(tmp_path):
    from devgraph.api.canonical_service import CanonicalServiceRoute
    from devgraph.owner_https_runtime import OwnerHTTPSBoundary

    s, auth, _, body, provider, _ = setup(tmp_path)
    outer = FastAPI()
    origin = "https://graph.example"
    outer.mount(
        "/service/v1",
        CanonicalServiceRoute(
            app(s, provider), origin=origin, authority_receiver="https://canonical.example"
        ),
    )
    boundary = OwnerHTTPSBoundary(outer, origin=origin, canonical_paths=True)
    with TestClient(boundary, base_url=origin) as client:
        headers = {"Origin": "chrome-extension://example"}
        response = client.get("/service/v1/holder-sessions/v1/capabilities", headers=headers)
        assert response.status_code == 200, response.text
        caps = response.json()
        assert caps["challenge_path"] == "/service/v1/holder-sessions/v1/challenges"
        assert (
            client.post(caps["challenge_path"], json=body, headers=headers).json()["challenge"]
            == auth["challenge"]
        )
        assert (
            client.post(caps["exchange_path"], json=auth, headers=headers).json()["state"]
            == "issued"
        )


def test_runtime_session_policy_is_explicit_optional_and_requires_canonical_authority():
    from devgraph.signed_read_runtime import ReadRuntimeConfiguration

    values = {
        "schema": "devgraph.signed-read-runtime.v1",
        "secs_binary_sha256": "ab" * 32,
        **{
            name: "/private/fixture/" + name
            for name in (
                "enrollment_policy_file",
                "authority_policy_file",
                "membership_trust_file",
                "revoked_memberships_file",
                "enrollment_database_file",
                "secs_binary",
                "issuer_key_file",
            )
        },
    }
    assert ReadRuntimeConfiguration.model_validate(values).holder_session_policy_file is None
    values["holder_session_policy_file"] = "/private/fixture/holder-sessions.json"
    with pytest.raises(ValueError, match="holder sessions require"):
        ReadRuntimeConfiguration.model_validate(values)
