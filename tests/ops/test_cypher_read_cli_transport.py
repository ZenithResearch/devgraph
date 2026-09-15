from __future__ import annotations

import json
from unittest.mock import Mock

import httpx
import pytest

from devgraph.cypher_read import REQUEST_SCHEMA, RESULT_SCHEMA, CypherReadError
from devgraph.ops.cypher_client import query_cypher_snapshot


def test_cli_helper_keeps_origin_and_proxy_policy_fixed(tmp_path, monkeypatch):
    request = tmp_path / "query.json"
    request.write_text(json.dumps({
        "schema": REQUEST_SCHEMA, "query": "MATCH (n:Issue) RETURN n.id AS id LIMIT 2",
        "parameters": {},
    }))
    request.chmod(0o600)
    config = object()
    monkeypatch.setattr("devgraph.ops.cypher_client.load_local_config", lambda path: config)
    monkeypatch.setattr(
        "devgraph.ops.cypher_client.read_local_read_credential", lambda conf: "secret",
    )
    visited = []

    def handler(req):
        visited.append(req)
        return httpx.Response(200, json={
            "schema": RESULT_SCHEMA, "columns": ["id"], "rows": [], "row_count": 0, "limit": 2,
        })

    transport = httpx.Client(transport=httpx.MockTransport(handler))
    factory = Mock(return_value=transport)
    monkeypatch.setattr("devgraph.ops.cypher_client.httpx.Client", factory)
    result = query_cypher_snapshot(request_file=request)
    assert result["rows"] == []
    factory.assert_called_once_with(trust_env=False, follow_redirects=False)
    assert str(visited[0].url) == "http://127.0.0.1:8080/query/cypher"
    assert visited[0].headers["Authorization"] == "Bearer secret"
    assert transport.is_closed


def test_cli_helper_rejects_unsafe_files_and_invalid_input_before_credentials(
    tmp_path, monkeypatch,
):
    lookup = Mock()
    monkeypatch.setattr("devgraph.ops.cypher_client.read_local_read_credential", lookup)
    target = tmp_path / "query.json"
    target.write_text("{}")
    target.chmod(0o600)
    symlink = tmp_path / "linked.json"
    symlink.symlink_to(target)
    for path in (target, symlink, tmp_path / "missing.json"):
        with pytest.raises(CypherReadError):
            query_cypher_snapshot(request_file=path)
    lookup.assert_not_called()
