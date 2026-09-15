from __future__ import annotations

import json

import httpx
import pytest

from devgraph.cli import CliError, main, query_work_snapshot
from devgraph.local_host import LocalHostConfig, provision_local_read_credential, write_local_config


@pytest.mark.parametrize(
    ("kind", "relationship", "path"),
    [
        ("Project", "children", "/work/Project/p-1/children"),
        ("Task", "blockers", "/tasks/p-1/blockers"),
    ],
)
def test_relationship_reads_use_fixed_public_api(tmp_path, kind, relationship, path) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    config = LocalHostConfig.build(data_root=data, validate_data_root=False)
    config_path = tmp_path / "local.json"
    write_local_config(config, config_path)
    provision_local_read_credential(config)

    def handler(request):
        assert str(request.url) == f"http://127.0.0.1:8080{path}"
        assert request.method == "GET"
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(200, json=[])

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        assert query_work_snapshot(
            kind=kind,
            work_id="p-1",
            include_archived=False,
            relationship=relationship,
            config_path=config_path,
            transport=transport,
        ) == {"items": []}


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["query", "children", "Project", "p-1"], {"relationship": "children", "kind": "Project"}),
        (["query", "blockers", "t-1"], {"relationship": "blockers", "kind": "Task"}),
        (
            ["query", "work", "Issue", "--limit", "25", "--after-id", "i-2", "--descending"],
            {"limit": 25, "after_id": "i-2", "descending": True},
        ),
    ],
)
def test_cli_forwards_named_reads(monkeypatch, capsys, args, expected) -> None:
    calls = []
    monkeypatch.setattr(
        "devgraph.cli.query_work_snapshot", lambda **kwargs: calls.append(kwargs) or {"items": []}
    )
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == {"items": []}
    assert all(calls[0][key] == value for key, value in expected.items())


def test_get_does_not_silently_ignore_paging_options() -> None:
    with pytest.raises(CliError, match="list options"):
        query_work_snapshot(kind="Issue", work_id="i-1", include_archived=False, limit=2)
