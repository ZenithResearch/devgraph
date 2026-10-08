"""Same-origin browser transport: admission, guarded writes and receipt-only status."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import build_services
from tests.auth.test_secs_work import KEY, canonical, request
from tests.auth.test_secs_work_v2 import proof_v2
from tests.ops.test_named_work_v2_receiver import fixture as receiver_fixture

from devgraph.api import create_app
from devgraph.credential_requests import wallet_presentation_request
from devgraph.ops.browser_credential_work import BrowserCredentialWorkHost

ORIGIN = "http://127.0.0.1:8080"
HEADERS = {"Origin": ORIGIN, "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}


def fixture(monkeypatch, tmp_path):
    receiver, storage, path, _, _ = receiver_fixture(tmp_path)
    config = path / "browser.json"
    config.write_bytes(
        canonical({"schema": "devgraph.browser-credential-transport.v2", "origins": [ORIGIN]})
    )
    config.chmod(0o600)
    host = BrowserCredentialWorkHost(data_root=tmp_path, receiver=receiver)
    calls = []

    def produce(action, value, key):
        calls.append((action, value, key))
        if action == "issue-credential":
            return canonical(
                wallet_presentation_request(
                    canonical(value["request"]), key, {"claims": {"caller": value["caller"]}}
                )
            )
        return proof_v2(canonical(value["request"]), key)

    monkeypatch.setattr(host, "_produce", produce)
    services = replace(build_services(frozenset()), storage=storage, credential_work_host=host)
    return TestClient(create_app(services), base_url=ORIGIN), storage, config, calls


def prepare_body():
    return {
        "request": request().decode(),
        "idempotency_key": KEY,
        "holder_public_key": "ab" * 32,
        "caller": {"kind": "browser", "id": ORIGIN},
    }


def signed_body(client):
    response = client.post("/credential-work/v2/prepare", json=prepare_body(), headers=HEADERS)
    assert response.status_code == 200, response.text
    prepared = response.json()
    return {
        "request": request().decode(),
        "idempotency_key": KEY,
        "credential": prepared["credential"],
        "disclosure": prepared["disclosure"],
        "presentation": {"caller": {"kind": "browser", "id": ORIGIN}},
    }


def test_prepare_does_not_mutate_execute_and_status_share_exact_receipt(monkeypatch, tmp_path):
    client, storage, _, calls = fixture(monkeypatch, tmp_path)
    body = signed_body(client)
    assert storage.query() == []
    absent = client.post("/credential-work/v2/status", json=body, headers=HEADERS)
    assert absent.json() == {"state": "unknown"}
    assert storage.query() == []
    result = client.post("/credential-work/v2/execute", json=body, headers=HEADERS)
    assert result.status_code == 200, result.text
    status = client.post("/credential-work/v2/status", json=body, headers=HEADERS)
    assert status.json()["receipt"]["receipt_id"] == result.json()["receipt"]["receipt_id"]
    assert len(storage.query("Issue")) == 1
    assert calls[0][0] == "issue-credential"
    assert client.get("/assets/credential-v2/coordinator.mjs").status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {**HEADERS, "Origin": "http://127.0.0.1:9999"},
        {**HEADERS, "Authorization": "Bearer read-cannot-write"},
        {**HEADERS, "Sec-Fetch-Site": "cross-site"},
        [("origin", ORIGIN), ("origin", ORIGIN), ("content-type", "application/json")],
    ],
)
def test_untrusted_browser_fails_before_producer(monkeypatch, tmp_path, headers):
    client, storage, _, calls = fixture(monkeypatch, tmp_path)
    response = client.post("/credential-work/v2/prepare", json=prepare_body(), headers=headers)
    assert response.status_code == 403
    assert calls == [] and storage.query() == []


@pytest.mark.parametrize("body", [b'{"request":1,"request":2}', b"{}", b"[" + b" " * 262145])
def test_invalid_and_oversized_json_fails_safely(monkeypatch, tmp_path, body):
    client, storage, _, calls = fixture(monkeypatch, tmp_path)
    assert (
        client.post("/credential-work/v2/prepare", content=body, headers=HEADERS).status_code == 403
    )
    assert calls == [] and storage.query() == []


def test_origin_revocation_and_caller_substitution_stop_before_authorization(monkeypatch, tmp_path):
    client, storage, config, calls = fixture(monkeypatch, tmp_path)
    body = signed_body(client)
    body["presentation"]["caller"]["id"] = "https://other.example"
    assert client.post("/credential-work/v2/execute", json=body, headers=HEADERS).status_code == 403
    assert len(calls) == 1
    body["presentation"]["caller"]["id"] = ORIGIN
    config.unlink()
    assert client.post("/credential-work/v2/execute", json=body, headers=HEADERS).status_code == 403
    assert len(calls) == 1 and storage.query() == []


def provider_fixture(monkeypatch, tmp_path):
    from tests.auth.test_secs_work import SEED

    client, storage, config, calls = fixture(monkeypatch, tmp_path)
    profile = {
        "schema": "castalia.provider-profile.v1",
        "display_name": "Devgraph local authority",
        "origins": [ORIGIN],
        "membership": None,
        "presentations": [
            {
                "issuer": "secs://devgraph-work",
                "key_id": "test-secs",
                "public_key": SEED.public_key().public_bytes_raw().hex(),
                "audience": "devgraph://receiver-local",
                "callers": [{"kind": "browser", "id": ORIGIN}],
            }
        ],
    }
    path = config.with_name("wallet-provider.json")
    path.write_text(json.dumps(profile))
    path.chmod(0o600)
    return client, storage, calls, path, profile


def test_provider_setup_returns_only_explicit_public_trust_without_issuing_authority(
    monkeypatch, tmp_path
):
    client, storage, calls, _, profile = provider_fixture(monkeypatch, tmp_path)
    result = client.post("/credential-work/v2/provider-profile", json={}, headers=HEADERS)
    assert result.status_code == 200, result.text
    assert result.json() == profile
    from pathlib import Path

    fixture_path = Path(__file__).parents[1] / "fixtures/credential-v2/wallet-provider-profile.json"
    assert result.json() == json.loads(fixture_path.read_text())
    assert result.headers["cache-control"] == "no-store"
    assert calls == [] and storage.query() == []


@pytest.mark.parametrize("change", ["key", "audience", "caller", "membership", "extra", "mode"])
def test_provider_setup_rejects_unpinned_or_broadened_trust(monkeypatch, tmp_path, change):
    client, _, calls, path, profile = provider_fixture(monkeypatch, tmp_path)
    if change == "key":
        profile["presentations"][0]["public_key"] = "ab" * 32
    elif change == "audience":
        profile["presentations"][0]["audience"] = "other://receiver"
    elif change == "caller":
        profile["presentations"][0]["callers"].append(
            {"kind": "browser", "id": "https://other.test"}
        )
    elif change == "membership":
        profile["membership"] = {"protocol": "unrequested"}
    elif change == "extra":
        profile["secret"] = "never-return-extra-fields"
    else:
        path.chmod(0o644)
    path.write_text(json.dumps(profile))
    result = client.post("/credential-work/v2/provider-profile", json={}, headers=HEADERS)
    assert result.status_code == 403
    assert "never-return" not in result.text and calls == []


@pytest.mark.parametrize("change", ["expired", "revoked", "managed-revoked", "missing-profile"])
def test_provider_setup_reloads_current_pins(monkeypatch, tmp_path, change):
    from tests.auth.test_secs_work import NOW

    client, _, calls, path, _ = provider_fixture(monkeypatch, tmp_path)
    assert (
        client.post("/credential-work/v2/provider-profile", json={}, headers=HEADERS).status_code
        == 200
    )
    if change == "missing-profile":
        path.unlink()
    elif change == "managed-revoked":
        (path.parent.with_name("devgraph.work.v1") / "receiver.json").unlink()
    else:
        for root in [path.parent, path.parent.with_name("devgraph.work.v1")]:
            registry_path = root / "secs-public-key-registry.json"
            registry = json.loads(registry_path.read_text())
            registry["keys"][0].update(
                {"not_after": NOW} if change == "expired" else {"status": "revoked"}
            )
            registry_path.write_text(json.dumps(registry))
    assert (
        client.post("/credential-work/v2/provider-profile", json={}, headers=HEADERS).status_code
        == 403
    )
    assert calls == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {**HEADERS, "Authorization": "Bearer read-key"},
        {**HEADERS, "Origin": "https://other.test"},
    ],
)
def test_provider_setup_obeys_browser_admission(monkeypatch, tmp_path, headers):
    client, _, _, _, _ = provider_fixture(monkeypatch, tmp_path)
    assert (
        client.post("/credential-work/v2/provider-profile", json={}, headers=headers).status_code
        == 403
    )
