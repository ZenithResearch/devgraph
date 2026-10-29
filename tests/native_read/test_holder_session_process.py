"""Actual private receiver process, native signatures, disposable public-holder parent."""

import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from tests.native_read.holder_session_fixture import create_fixture
from tests.native_read.test_holder_session_exchange import read_request, status
from tests.native_read.test_holder_session_provider import command

from devgraph.auth.delegated_contract import canonical_json


def test_actual_private_process_issues_recovers_reads_and_revokes_unbound_parent(tmp_path):
    holder, read = (
        os.environ.get(k)
        for k in ("SECS_HOLDER_GRANT_TEST_BINARY", "SECS_READ_AUTHORITY_TEST_BINARY")
    )
    if not holder or not read:
        pytest.skip("matched native authority binaries required")
    owner, key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    profile = create_fixture(
        tmp_path / "isolated",
        holder_public_key=owner.public_key().public_bytes_raw().hex(),
        holder_binary=holder,
        read_binary=read,
        origin="app://zenith-transfer",
    )
    parent = json.loads((profile.parent / "parent.json").read_bytes())
    assert parent["claims"]["installation_id"] is None
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("holder_session_fixture.py")),
            "--profile-file",
            str(profile),
        ],
        env={},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def call(operation, arguments):
        value = command(operation, arguments)
        value["request_id"] = secrets.token_hex(16)
        child.stdin.write(canonical_json(value) + b"\n")
        child.stdin.flush()
        raw = child.stdout.readline(262145)
        assert raw, child.stderr.read().decode()
        result = json.loads(raw)
        assert result["request_id"] == value["request_id"] and result["ok"], result
        return result["value"]

    try:
        assert call("services", {})["services"][0]["context"]["origin"] == "app://zenith-transfer"
        body = {
            "schema": "secs.holder-session-prepare.v1",
            "request_id": secrets.token_hex(16),
            "parent_grant": parent,
            "installation_id": "cc" * 16,
            "session_installation_id": "dd" * 16,
            "session_public_key": key.public_key().public_bytes_raw().hex(),
            "origin": "app://zenith-transfer",
            "audience": "devgraph",
            "permissions": None,
            "requested_ttl_seconds": 60,
        }
        challenge = call("prepare", {"body": body})["challenge"]
        authorization = {
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
        issued = call("exchange", {"authorization": authorization})
        assert issued["state"] == "issued"
        assert call("exchange", {"authorization": authorization}) == issued
        # No owner key is needed or called below this point.
        del owner, authorization
        for _ in range(2):
            result = call(
                "execute",
                {
                    "presentation": read_request(
                        key,
                        issued["credential"],
                        nonce=secrets.token_hex(16),
                        now=datetime.now(timezone.utc),
                    )
                },
            )
            assert [v["key"] for v in result["result"]["items"]] == ["Task/allowed-1"]
        assert (
            call(
                "status",
                {
                    "proof": status(
                        key, challenge, nonce=secrets.token_hex(16), now=datetime.now(timezone.utc)
                    )
                },
            )
            == issued
        )
        (profile.parent / "REVOKE").write_text("fixture-only")
        result = call(
            "status",
            {
                "proof": status(
                    key, challenge, nonce=secrets.token_hex(16), now=datetime.now(timezone.utc)
                )
            },
        )
        assert result["state"] == "revoked" and result["credential"] is None
        child.stdin.close()
        assert child.wait(timeout=10) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_valid_finite_session_cannot_authorize_registered_mutation_transport(tmp_path, monkeypatch):
    from tests.api.test_credential_work import HEADERS, fixture, signed_body
    from tests.native_read.test_holder_session_exchange import setup
    from tests.native_read.test_signed_authority import NOW

    fixture_root = tmp_path / "session"
    fixture_root.mkdir(mode=0o700)
    sessions, authorization, key, _, _, _ = setup(fixture_root)
    issued = sessions.exchange(authorization, now=NOW)
    presentation = read_request(key, issued["credential"], now=NOW)
    client, storage, _, calls = fixture(monkeypatch, tmp_path)
    # The actual registered browser write handler and receiver composition run.
    # A genuine finite read token cannot replace its write authority credential.
    body = signed_body(client)
    assert len(calls) == 1
    body["credential"] = presentation["credential"]
    body["presentation"] = presentation["proof"]
    response = client.post("/credential-work/v2/execute", json=body, headers=HEADERS)
    assert response.status_code == 403, response.text
    assert len(calls) == 1 and storage.query() == []
