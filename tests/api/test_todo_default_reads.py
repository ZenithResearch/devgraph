"""The default Todo read must work on every supported Python version."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.cli import main
from devgraph.kanban import InvalidBoardFilter
from devgraph.local_host import (
    LocalHostConfig,
    provision_local_read_credential,
    write_local_config,
)
from devgraph.model.work import Initiative, Issue, Project, Proposal, Task, Todo
from devgraph.ops import workflow_client
from devgraph.todo_views import TodoFilters


def test_empty_query_uses_defaults():
    # Python 3.10's strict parse_qs rejects even an entirely empty query.
    assert TodoFilters.parse("") == TodoFilters()


@pytest.mark.parametrize(
    "query",
    ["kind", "progress", "progress=not_started&", "limit=1&limit=2", "unknown=value"],
)
def test_empty_query_support_keeps_nonempty_filter_validation_strict(query):
    with pytest.raises(InvalidBoardFilter, match="invalid_todo_filter"):
        TodoFilters.parse(query)


def test_api_without_filters_returns_all_six_types():
    services = build_services(frozenset({"devgraph.read"}))
    kinds = (Todo, Proposal, Initiative, Project, Issue, Task)
    for cls in kinds:
        work = cls(id=cls.__name__.lower(), title=cls.__name__)
        services.storage.create_node(work.kind, work.id, work.to_node_properties())
    client = TestClient(create_app(services))
    response = client.get("/todos/v2", headers={"Authorization": "Bearer " + FAKE_CREDENTIAL})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["schema"] == "devgraph.todos.v2"
    assert page["total"] == 6 and page["next_cursor"] is None
    assert {item["kind"] for item in page["items"]} == {cls.__name__ for cls in kinds}
    assert response.headers["cache-control"] == "no-store"


def test_cli_default_todos_reaches_the_authenticated_read_api(tmp_path, monkeypatch, capsys):
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    config = LocalHostConfig.build(data_root=data, validate_data_root=False)
    config_path = tmp_path / "local.json"
    write_local_config(config, config_path)
    provision_local_read_credential(config)
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "http://127.0.0.1:8080/todos/v2"
        assert request.method == "GET"
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(200, json={"schema": "devgraph.todos.v2", "total": 0, "items": []})

    transport = httpx.Client(transport=httpx.MockTransport(handler))

    def local_transport(**options):
        assert options == {"trust_env": False, "follow_redirects": False}
        return transport

    monkeypatch.setattr(workflow_client.httpx, "Client", local_transport)
    assert main(["query", "todos", "--config", str(config_path)]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 0
    assert len(calls) == 1
