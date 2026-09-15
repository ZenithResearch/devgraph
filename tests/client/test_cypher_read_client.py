from __future__ import annotations

import json
from unittest.mock import Mock

import httpx
import pytest

from devgraph.client.http import (
    DevgraphHttpClient,
    DevgraphInvalidSuccessEnvelope,
    DevgraphMalformedProblem,
    DevgraphProblem,
    DevgraphRequestContext,
    DevgraphTransportError,
)
from devgraph.cypher_read import REQUEST_SCHEMA, RESULT_SCHEMA, CypherReadError

RAW = json.dumps({
    "schema": REQUEST_SCHEMA, "query": "MATCH (n:Issue) RETURN n.id AS id LIMIT 2",
    "parameters": {},
}).encode()
RESULT = {
    "schema": RESULT_SCHEMA, "columns": ["id"], "rows": [["x"]], "row_count": 1, "limit": 2,
}


def client(response):
    transport = Mock()
    transport.stream = None
    transport.request.return_value = response
    return DevgraphHttpClient(
        transport=transport, base_url="http://127.0.0.1:8080", timeout=15,
    ), transport


def test_cypher_client_sends_existing_read_context_and_validates_result():
    api, transport = client(httpx.Response(200, json=RESULT))
    assert api.query_cypher(
        DevgraphRequestContext(credential="test-secret"), request_json=RAW,
    ) == RESULT
    call = transport.request.call_args
    assert call.args == ("POST", "http://127.0.0.1:8080/query/cypher")
    assert call.kwargs["headers"] == {"Authorization": "Bearer test-secret"}
    assert call.kwargs["json"] == json.loads(RAW)


@pytest.mark.parametrize("change", [
    {"limit": 3}, {"columns": ["private"]}, {"rows": [[{}]]}, {"extra": "secret"},
    {"row_count": True}, {"rows": [["x"]] * 3, "row_count": 3},
])
def test_invalid_server_results_are_safe(change):
    api, _ = client(httpx.Response(200, json={**RESULT, **change}))
    with pytest.raises(DevgraphInvalidSuccessEnvelope):
        api.query_cypher(DevgraphRequestContext(credential="secret"), request_json=RAW)


def test_invalid_request_never_uses_transport():
    api, transport = client(httpx.Response(200, json=RESULT))
    with pytest.raises(CypherReadError):
        api.query_cypher(DevgraphRequestContext(credential="secret"), request_json=b"{}")
    transport.request.assert_not_called()


def test_problem_and_transport_failures_are_safe():
    api, transport = client(httpx.Response(429, json={
        "type": "about:blank", "title": "Cypher read failed", "status": 429,
        "detail": "cypher_query_capacity_exceeded",
    }, headers={"content-type": "application/problem+json"}))
    with pytest.raises(DevgraphProblem) as problem:
        api.query_cypher(DevgraphRequestContext(credential="secret"), request_json=RAW)
    assert problem.value.status == 429
    transport.request.side_effect = RuntimeError("secret transport details")
    with pytest.raises(DevgraphTransportError) as error:
        api.query_cypher(DevgraphRequestContext(credential="secret"), request_json=RAW)
    assert "secret" not in str(error.value)


def test_real_httpx_transport_streams_bounded_response_and_never_follows_redirect():
    visited = []

    def handler(request):
        visited.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://untrusted.invalid/steal"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as transport:
        api = DevgraphHttpClient(transport=transport, base_url="http://127.0.0.1:8080", timeout=15)
        with pytest.raises(DevgraphMalformedProblem):
            api.query_cypher(DevgraphRequestContext(credential="secret"), request_json=RAW)
    assert visited == ["http://127.0.0.1:8080/query/cypher"]


def test_stream_size_is_bounded_before_whole_body_is_retained():
    consumed = []

    class Oversized(httpx.SyncByteStream):
        def __iter__(self):
            for i in range(100):
                consumed.append(i)
                yield b"x" * 8192

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"content-type": "application/json"}, stream=Oversized(),
    ))) as transport:
        api = DevgraphHttpClient(transport=transport, base_url="http://127.0.0.1:8080", timeout=15)
        with pytest.raises(DevgraphInvalidSuccessEnvelope):
            api.query_cypher(DevgraphRequestContext(credential="secret"), request_json=RAW)
    assert len(consumed) == 33


def test_real_httpx_transport_reads_normal_response():
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=RESULT),
    )) as transport:
        api = DevgraphHttpClient(transport=transport, base_url="http://127.0.0.1:8080", timeout=15)
        assert api.query_cypher(
            DevgraphRequestContext(credential="secret"), request_json=RAW,
        ) == RESULT
