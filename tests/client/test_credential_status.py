"""A v2 status lookup cannot become a mutation or expose another holder's receipt."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import build_services
from tests.auth.test_secs_work import KEY, b64, request
from tests.auth.test_secs_work_v2 import proof_v2
from tests.client.test_http_client import RecordingTransport, Response
from tests.ops.test_named_work_v2_receiver import fixture

from devgraph.api import create_app
from devgraph.client import (
    DevgraphHttpClient,
    DevgraphInvalidSuccessEnvelope,
    DevgraphProblem,
    DevgraphWorkContext,
    DevgraphWorkV2Context,
)


def client_fixture(tmp_path):
    receiver, storage, path, _, _ = fixture(tmp_path)
    services = replace(build_services(frozenset()), storage=storage, named_work_v2=receiver)
    transport = TestClient(create_app(services))
    client = DevgraphHttpClient(transport=transport, base_url="http://testserver", timeout=10)
    return client, transport, receiver, storage, path


def context(**overrides):
    return DevgraphWorkV2Context(projection_json=proof_v2(request(), **overrides))


def test_absence_is_unknown_and_status_requires_current_holder_and_policy(tmp_path):
    client, _, receiver, storage, path = client_fixture(tmp_path)
    args = dict(request_json=request(), idempotency_key=KEY)
    assert client.reconcile_credential_work(context(), **args).state == "unknown"
    assert storage.query() == [] and receiver.audit_log.records == []
    first = client.execute_named_work(context(), **args)
    other = client.reconcile_credential_work(context(actor_id="pubkey:sha256:" + "b" * 64), **args)
    assert other.state == "unknown" and other.receipt is None
    found = client.reconcile_credential_work(context(nonce="55" * 16), **args)
    assert found.receipt.receipt_id == first.receipt.receipt_id
    assert len(receiver.audit_log.records) == 1
    (path.with_name("devgraph.work.v1") / "receiver.json").unlink()
    with pytest.raises(DevgraphProblem, match="403"):
        client.reconcile_credential_work(context(nonce="66" * 16), **args)
    assert len(receiver.audit_log.records) == 1


def test_legacy_proofs_and_routes_cannot_query_v2_status(tmp_path):
    client, transport, _, storage, _ = client_fixture(tmp_path)
    args = dict(request_json=request(), idempotency_key=KEY)
    with pytest.raises(TypeError):
        client.reconcile_credential_work(DevgraphWorkContext(projection_json=b"{}"), **args)
    assert transport.post("/work-operations/v1/status", content=request()).status_code == 404
    response = transport.post("/work-operations/v2/status", content=request(), headers={
        "X-Devgraph-Work-Authority": b64(b"{}"), "Idempotency-Key": KEY,
    })
    assert response.status_code == 403 and storage.query() == []


@pytest.mark.parametrize("body", [
    {"state": "not-executed"},
    {"state": "committed"},
    {"state": "unknown", "extra": True},
    {"state": "committed", "receipt": {
        "receipt_id": "one", "operation": "devgraph.work.create.v1", "subject_label": "Issue",
        "subject_id": "other", "receipt_status": "pending", "duplicate": True,
        "correlation_id": "two",
    }},
    {"state": "committed", "receipt": {
        "receipt_id": "one", "operation": "devgraph.work.create.v1", "subject_label": "Issue",
        "subject_id": "test-one", "receipt_status": "pending", "duplicate": False,
        "correlation_id": "two",
    }},
])
def test_status_rejects_inconsistent_or_uncorrelated_success(body):
    transport = RecordingTransport([Response(200, body)])
    client = DevgraphHttpClient(transport=transport, base_url="http://testserver", timeout=10)
    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        client.reconcile_credential_work(context(), request_json=request(), idempotency_key=KEY)
    assert len(transport.calls) == 1
    assert transport.calls[0]["url"].endswith("/work-operations/v2/status")
