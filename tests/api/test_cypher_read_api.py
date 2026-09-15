from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.auth import AuditLog
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.scopes import SCOPE_READ, SCOPE_WRITE
from devgraph.cypher_read import MAX_REQUEST_BYTES, REQUEST_SCHEMA, RESULT_SCHEMA, CypherReadError
from devgraph.storage.cypher_read import CypherReadService

BODY = {
    "schema": REQUEST_SCHEMA, "query": "MATCH (n:Issue) RETURN n.id AS id LIMIT 2",
    "parameters": {},
}
AUTH = {"Authorization": f"Bearer {FAKE_CREDENTIAL}"}


def fixture(scopes=frozenset({SCOPE_READ})):
    services = build_services(scopes)
    runner = Mock()
    runner.execute.return_value = {
        "schema": RESULT_SCHEMA, "columns": ["id"], "rows": [["issue-1"]],
        "row_count": 1, "limit": 2,
    }
    audit = AuditLog()
    service = CypherReadService(
        runner, verifier=services.verifier, audience=services.audience, audit_log=audit,
    )
    client = TestClient(create_app(replace(services, cypher_read=service)))
    return client, runner, audit


def test_authorized_query_and_safe_audit():
    client, runner, audit = fixture()
    response = client.post("/query/cypher", json=BODY, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["rows"] == [["issue-1"]]
    runner.execute.assert_called_once()
    assert len(audit.records) == 1
    assert audit.records[0].operation == "cypher_read_v1"
    assert audit.records[0].safe_summary == {"row_count": 1}
    assert "MATCH" not in repr(audit.records)
    assert FAKE_CREDENTIAL not in response.text


@pytest.mark.parametrize("scopes,headers,status", [
    (frozenset({SCOPE_READ}), {}, 401),
    (frozenset({SCOPE_READ}), {"Authorization": "Bearer unknown-secret"}, 401),
    (frozenset(), AUTH, 403),
    (frozenset({SCOPE_WRITE}), AUTH, 403),
])
def test_denied_auth_never_reaches_runner(scopes, headers, status):
    client, runner, audit = fixture(scopes)
    response = client.post("/query/cypher", json=BODY, headers=headers)
    assert response.status_code == status
    runner.execute.assert_not_called()
    assert not audit.records


def test_unsupported_storage_is_closed():
    client = TestClient(create_app(build_services(frozenset({SCOPE_READ}))))
    response = client.post("/query/cypher", json=BODY, headers=AUTH)
    assert response.status_code == 503
    assert response.json()["detail"] == "cypher_backend_unavailable"


@pytest.mark.parametrize("query", [
    "MATCH (n:Issue) DELETE n",
    "MATCH (n:EventReceipt) RETURN n.id AS id LIMIT 1",
    "CALL apoc.load.json($url) YIELD value RETURN value LIMIT 1",
])
def test_unsafe_queries_do_not_reach_runner(query):
    client, runner, _ = fixture()
    response = client.post("/query/cypher", json={**BODY, "query": query}, headers=AUTH)
    assert response.status_code == 400
    assert query not in response.text
    runner.execute.assert_not_called()


def test_transport_duplicates_querystring_encoding_and_oversize():
    client, runner, _ = fixture()
    attempts = [
        client.post("/query/cypher?query=secret", json=BODY, headers=AUTH),
        client.post("/query/cypher", json=BODY, headers={**AUTH, "Content-Encoding": "gzip"}),
        client.post("/query/cypher", content=json.dumps(BODY), headers=AUTH),
        client.post("/query/cypher", json=BODY, headers=[
            ("Authorization", f"Bearer {FAKE_CREDENTIAL}"),
            ("Authorization", "Bearer other-secret"),
        ]),
    ]
    assert [response.status_code for response in attempts] == [400, 400, 400, 401]
    huge = client.post(
        "/query/cypher", content=b" " * (MAX_REQUEST_BYTES + 1),
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert huge.status_code == 413
    duplicated = client.post(
        "/query/cypher", content=json.dumps(BODY)[:-1] + ',"parameters":{}}',
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert duplicated.status_code == 400
    runner.execute.assert_not_called()


@pytest.mark.parametrize("code,status", [
    ("cypher_query_capacity_exceeded", 429), ("cypher_query_timeout", 504),
    ("cypher_result_too_large", 413), ("cypher_backend_unavailable", 503),
])
def test_safe_runner_failures_have_stable_problem_envelopes(code, status):
    client, runner, audit = fixture()
    runner.execute.side_effect = CypherReadError(code, status)
    response = client.post("/query/cypher", json=BODY, headers=AUTH)
    assert response.status_code == status
    assert response.json() == {
        "type": "about:blank", "title": "Cypher read failed", "status": status, "detail": code,
    }
    assert not audit.records


def test_credential_is_reverified_after_body_before_execution(monkeypatch):
    client, runner, _ = fixture()
    verifier = client.app.state.services.verifier
    context = verifier.verify(FAKE_CREDENTIAL, audience="devgraph")
    verify = Mock(side_effect=[context, UnauthenticatedError("credential expired")])
    monkeypatch.setattr(verifier, "verify", verify)
    response = client.post("/query/cypher", json=BODY, headers=AUTH)
    assert response.status_code == 401
    assert verify.call_count == 2
    runner.execute.assert_not_called()


@pytest.mark.parametrize("deadline", [5, 8])
def test_real_asyncio_deadline_returns_safe_504(monkeypatch, deadline):
    client, runner, _ = fixture()
    original = asyncio.wait_for

    async def expire_selected(awaitable, timeout):
        return await original(awaitable, timeout=0 if timeout == deadline else timeout)

    monkeypatch.setattr(asyncio, "wait_for", expire_selected)
    response = client.post("/query/cypher", json=BODY, headers=AUTH)
    assert response.status_code == 504
    assert response.json()["detail"] == "cypher_query_timeout"
    runner.execute.assert_not_called()
