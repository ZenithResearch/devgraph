"""Explicit native app context on signed reads, never general CORS admission."""

import json
import os
import secrets
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from tests.native_read.holder_session_fixture import create_fixture, fixture_application
from tests.native_read.test_holder_session_exchange import read_request

from devgraph.api.holder_sessions import configured_native_origins, register_holder_sessions
from devgraph.api.wallet_monitor import register_wallet_monitor
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.endpoint_read_runtime import EndpointReadBoundary


def test_native_origin_is_admitted_only_on_signed_reads_with_current_proof(tmp_path):
    binaries = [
        os.environ.get(k)
        for k in ("SECS_HOLDER_GRANT_TEST_BINARY", "SECS_READ_AUTHORITY_TEST_BINARY")
    ]
    if not all(binaries):
        pytest.skip("matched native authorities required")
    owner, key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    origin, app_origin = "https://graph.example", "app://zenith-ios"
    path = create_fixture(
        tmp_path / "fixture",
        holder_public_key=owner.public_key().public_bytes_raw().hex(),
        holder_binary=binaries[0],
        read_binary=binaries[1],
        origin=app_origin,
    )
    app = fixture_application(path)
    parent = json.loads((path.parent / "parent.json").read_bytes())
    target = FastAPI()

    @target.exception_handler(UnauthenticatedError)
    async def denied(_request, _error):
        return JSONResponse({"error": "access_denied"}, status_code=401)

    register_wallet_monitor(
        target, storage=app.storage, enrollment=app.sessions.host.enrollment, audit_log=app.audit
    )
    register_holder_sessions(target, host=app.sessions.host)
    boundary = EndpointReadBoundary(
        target,
        origin=origin,
        native_session_origins=configured_native_origins(app.sessions.host.enrollment),
    )
    with TestClient(boundary, base_url=origin) as client:
        body = {
            "schema": "secs.holder-session-prepare.v1",
            "request_id": secrets.token_hex(16),
            "parent_grant": parent,
            "installation_id": "cc" * 16,
            "session_installation_id": "dd" * 16,
            "session_public_key": key.public_key().public_bytes_raw().hex(),
            "origin": app_origin,
            "audience": "devgraph",
            "permissions": None,
            "requested_ttl_seconds": 60,
        }
        challenge = client.post("/holder-sessions/v1/challenges", json=body).json()["challenge"]
        auth = {
            "schema": "secs.holder-session-authorization.v1",
            "challenge": challenge,
            "parent_grant": parent,
            "holder_signature": owner.sign(
                b"secs.holder-session-authorization.v1/signature\0" + canonical_json(challenge)
            ).hex(),
            "session_signature": key.sign(
                b"secs.holder-session-possession.v1/signature\0" + canonical_json(challenge)
            ).hex(),
        }
        credential = client.post("/holder-sessions/v1/exchange", json=auth).json()["credential"]
        proof = read_request(key, credential, now=datetime.now(timezone.utc))
        response = client.post(
            "/monitor/wallet-access/v1/requests", json=proof, headers={"Origin": app_origin}
        )
        assert response.status_code == 200, response.text
        assert [v["key"] for v in response.json()["items"]] == ["Task/allowed-1"]
        assert (
            client.post(
                "/monitor/wallet-access/v1/requests", json=proof, headers={"Origin": app_origin}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/monitor/wallet-access/v1/requests", json=proof, headers={"Origin": "app://other"}
            ).status_code
            == 421
        )
        assert (
            client.post(
                "/work-operations/v1", json=proof, headers={"Origin": app_origin}
            ).status_code
            == 421
        )
        assert (
            client.get("/monitor/wallet-access/v1", headers={"Origin": app_origin}).status_code
            == 421
        )
