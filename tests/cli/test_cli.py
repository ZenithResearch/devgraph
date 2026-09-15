from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from devgraph.cli import (
    launchd_snapshot,
    local_status_snapshot,
    main,
    manage_services,
    ontology_snapshot,
    query_work_snapshot,
    start_local_services,
    status_snapshot,
)
from devgraph.local_host import (
    LocalHostConfig,
    provision_local_read_credential,
    read_local_read_credential,
    write_local_config,
)


def _status_transport(*, ready: bool = True, protected_status: int = 401):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/live":
            return httpx.Response(200, json={"live": True})
        if request.url.path == "/ready":
            return httpx.Response(
                200 if ready else 503,
                json={
                    "ready": ready,
                    "reason": "ready" if ready else "storage_unavailable",
                    "current_applied_version": 24,
                    "minimum_schema_version": 1,
                    "maximum_schema_version": 24,
                },
            )
        if request.url.path == "/work/Issue":
            return httpx.Response(protected_status, json={"detail": "never project this"})
        raise AssertionError(request.url.path)

    return httpx.MockTransport(handler)


def test_status_proves_live_ready_and_closed_protected_route() -> None:
    with httpx.Client(transport=_status_transport()) as client:
        result = status_snapshot(client=client)

    assert result["healthy"] is True
    assert result["api"]["protected_route_closed"] is True
    assert "detail" not in json.dumps(result)


def test_status_fails_when_protected_route_is_open() -> None:
    with httpx.Client(transport=_status_transport(protected_status=200)) as client:
        result = status_snapshot(client=client)

    assert result["healthy"] is False
    assert result["api"]["protected_route_closed"] is False


def test_status_sanitizes_transport_failures() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sensitive upstream detail", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        result = status_snapshot(client=client)

    assert result["healthy"] is False
    assert "sensitive" not in json.dumps(result)


def test_ontology_snapshot_reads_canonical_release() -> None:
    result = ontology_snapshot("0.1.0")

    assert result["release"] == "v0.1.0"
    assert result["authority"]["name"] == "Devgraph"
    assert result["manifest_path"].endswith("resources/ontology-releases/v0.1.0/manifest.json")


def test_launchd_snapshot_projects_only_bounded_state() -> None:
    outputs = iter(
        [
            subprocess.CompletedProcess([], 0, "state = running\npid = 123\nsecret = no\n", ""),
            subprocess.CompletedProcess([], 1, "", "raw failure"),
        ]
    )

    result = launchd_snapshot(runner=lambda command: next(outputs))

    assert result == {
        "services": {
            "neo4j": {
                "label": "ca.zenith.devgraph.neo4j",
                "loaded": True,
                "pid": 123,
                "state": "running",
            },
            "api": {
                "label": "ca.zenith.devgraph.api",
                "loaded": False,
                "pid": None,
                "state": "not_loaded",
            },
        }
    }
    assert "secret" not in json.dumps(result)


def test_cli_ontology_emits_json(capsys) -> None:
    assert main(["ontology", "--release", "v0.1.0"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["version"] == "0.1.0"


def test_project_registers_devgraph_console_script() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'devgraph = "devgraph.cli:main"' in pyproject


def test_packaged_ontology_releases_match_every_canonical_release() -> None:
    canonical_root = Path("ontology/releases")
    packaged_root = Path("src/devgraph/resources/ontology-releases")

    assert {path.name for path in packaged_root.iterdir()} == {
        path.name for path in canonical_root.iterdir()
    }
    for canonical_release in canonical_root.iterdir():
        packaged_release = packaged_root / canonical_release.name
        assert {path.name for path in packaged_release.iterdir()} == {
            path.name for path in canonical_release.iterdir()
        }
        for canonical in canonical_release.iterdir():
            assert (packaged_release / canonical.name).read_bytes() == canonical.read_bytes()


def test_ontology_snapshot_defaults_to_the_latest_packaged_release() -> None:
    assert ontology_snapshot()["version"] == "0.6.0"


def test_cli_packaged_arena_contract_and_legacy_actor_remain_available() -> None:
    manifest_path = Path(ontology_snapshot()["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    assert {entry["path"] for entry in manifest["files"]} >= {
        "arenas.md", "arena-contract.json"
    }
    profile = json.loads((manifest_path.parent / "arena-contract.json").read_text())
    assert profile["direct_membership"]["allowed_kinds"] == ["Initiative", "Task"]
    assert profile["runtime_binding"] is True
    current = json.loads((manifest_path.parent / "ontology.jsonld").read_text())
    by_id = {item.get("@id"): item for item in current["@graph"]}
    assert by_id["zn:Arena"]["runtimeLabel"] is True
    assert by_id["zn:Actor"]["equivalentClass"] == "zn:Entity"
    previous_path = Path(ontology_snapshot("0.4.0")["manifest_path"])
    previous = json.loads((previous_path.parent / "ontology.jsonld").read_text())
    previous_by_id = {item.get("@id"): item for item in previous["@graph"]}
    assert previous_by_id["zn:Person"]["subClassOf"] == "zn:Actor"
    assert "zn:Arena" not in previous_by_id


def test_service_start_uses_configured_launch_agent_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    installed: list[list[str]] = []

    def runner(command):
        if command[1] == "print":
            return subprocess.CompletedProcess(command, 1, "", "not loaded")
        installed.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("devgraph.cli.sys.platform", "darwin")
    result = manage_services("up", runner=runner, launch_agent_root=tmp_path)

    assert result["successful"] is True
    assert [Path(command[-1]).parent for command in installed] == [tmp_path, tmp_path]


def test_service_stop_is_idempotent_when_jobs_are_not_loaded(monkeypatch) -> None:
    commands: list[list[str]] = []

    def runner(command):
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, "", "not loaded")

    monkeypatch.setattr("devgraph.cli.sys.platform", "darwin")
    result = manage_services("down", runner=runner)

    assert result["successful"] is True
    assert all(command[1] == "print" for command in commands)


def test_cli_local_config_emits_only_persisted_paths(tmp_path: Path, capsys) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    config = LocalHostConfig.build(data_root=data_root)
    config_path = tmp_path / "local.json"
    write_local_config(config, config_path)

    assert main(["local", "config", "--config", str(config_path)]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["configuration"]["data_root"] == str(data_root)
    assert "credential" not in json.dumps(output).lower()


def test_query_work_uses_the_private_read_credential_without_projecting_it(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    (data_root / "secrets").mkdir(mode=0o700)
    config = LocalHostConfig.build(
        data_root=data_root,
        host_root=tmp_path / "host",
        validate_data_root=False,
    )
    config_path = tmp_path / "local.json"
    write_local_config(config, config_path)
    provision_local_read_credential(config)
    credential = read_local_read_credential(config)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "http"
        assert request.url.host == "127.0.0.1"
        assert request.url.port == 8080
        assert request.headers["Authorization"] == f"Bearer {credential}"
        assert request.url.path == "/work/Issue"
        assert request.url.params["include_archived"] == "false"
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "items": [
                    {
                        "artifact_ids": [],
                        "description": "",
                        "external_link_ids": [],
                        "id": "secure-local-devgraph-completion",
                        "kind": "Issue",
                        "priority": 1,
                        "status": "draft",
                        "title": "Complete secure local Devgraph",
                        "version": 1,
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        result = query_work_snapshot(
            kind="Issue",
            work_id=None,
            include_archived=False,
            config_path=config_path,
            transport=transport,
        )

    assert result["items"][0]["id"] == "secure-local-devgraph-completion"
    assert credential not in json.dumps(result)


def test_query_work_rejects_a_caller_selected_origin_before_credential_access(
    monkeypatch,
) -> None:
    def fail_query(**_kwargs) -> None:
        raise AssertionError("query must not start")

    monkeypatch.setattr("devgraph.cli.query_work_snapshot", fail_query)

    with pytest.raises(SystemExit) as captured:
        main(["query", "work", "Issue", "--base-url", "https://attacker.invalid"])

    assert captured.value.code == 2


def test_query_work_ignores_environment_http_proxies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    (data_root / "secrets").mkdir(mode=0o700)
    config = LocalHostConfig.build(
        data_root=data_root,
        host_root=tmp_path / "host",
        validate_data_root=False,
    )
    config_path = tmp_path / "local.json"
    write_local_config(config, config_path)
    provision_local_read_credential(config)
    credential = read_local_read_credential(config)
    constructor_calls: list[dict[str, object]] = []

    class LocalOnlyClient:
        def request(self, method: str, url: str, **kwargs) -> httpx.Response:
            assert method == "GET"
            assert url.startswith("http://127.0.0.1:8080/work/Issue")
            assert kwargs["headers"] == {"Authorization": f"Bearer {credential}"}
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"items": []},
            )

        def close(self) -> None:
            pass

    def build_client(**kwargs):
        constructor_calls.append(kwargs)
        return LocalOnlyClient()

    monkeypatch.setenv("HTTP_PROXY", "http://attacker.invalid:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr("devgraph.cli.httpx.Client", build_client)

    assert query_work_snapshot(
        kind="Issue",
        work_id=None,
        include_archived=False,
        config_path=config_path,
    ) == {"items": []}
    assert constructor_calls == [{"trust_env": False}]


def test_cli_read_credential_status_does_not_reveal_the_capability(
    tmp_path: Path,
    capsys,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    (data_root / "secrets").mkdir(mode=0o700)
    config = LocalHostConfig.build(
        data_root=data_root,
        host_root=tmp_path / "host",
        validate_data_root=False,
    )
    config_path = tmp_path / "local.json"
    write_local_config(config, config_path)
    provision_local_read_credential(config)
    credential = read_local_read_credential(config)

    assert main(
        ["local", "read-credential", "status", "--config", str(config_path)]
    ) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["scope"] == "devgraph.read"
    assert credential not in output


def _healthy_local_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(
        "devgraph.cli.neo4j_process_snapshot",
        lambda _config: {"pid": 123, "state": "running", "reason": "neo4j_pid_alive"},
    )
    monkeypatch.setattr(
        "devgraph.cli.status_snapshot",
        lambda **kwargs: {"api": {"base_url": kwargs["base_url"]}, "healthy": True},
    )
    monkeypatch.setattr(
        "devgraph.cli.launchd_snapshot",
        lambda: {
            "services": {
                "neo4j": {"loaded": True},
                "api": {"loaded": True},
            }
        },
    )


def _touch_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    path.chmod(0o700)
    return path


def test_local_status_fails_closed_without_persisted_configuration(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    _healthy_local_dependencies(monkeypatch)
    config_path = tmp_path / "missing-local.json"

    result = local_status_snapshot(config_path=config_path)

    assert result["configured"] is False
    assert result["configuration"] is None
    assert result["healthy"] is False
    assert main(["local", "status", "--config", str(config_path)]) == 1
    assert json.loads(capsys.readouterr().out)["healthy"] is False


def test_local_status_fails_closed_when_configured_runtime_is_incomplete(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    _healthy_local_dependencies(monkeypatch)
    data_root = tmp_path / "data"
    data_root.mkdir()
    config_path = tmp_path / "local.json"
    write_local_config(
        LocalHostConfig.build(
            data_root=data_root,
            host_root=tmp_path / "missing-host",
            neo4j_home=tmp_path / "missing-neo4j",
            java_home=tmp_path / "missing-java",
            python_executable=tmp_path / "missing-python",
        ),
        config_path,
    )

    result = local_status_snapshot(config_path=config_path)

    assert result["configured"] is True
    assert result["configuration"]["runtime"]["data_root"] is True
    assert result["configuration"]["runtime"]["neo4j"] is False
    assert result["healthy"] is False
    assert main(["local", "status", "--config", str(config_path)]) == 1
    assert json.loads(capsys.readouterr().out)["healthy"] is False


def test_local_status_succeeds_only_with_valid_runtime_and_loaded_services(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    _healthy_local_dependencies(monkeypatch)
    data_root = tmp_path / "data"
    data_root.mkdir()
    host_root = tmp_path / "host"
    neo4j_home = host_root / "neo4j"
    java_home = host_root / "java"
    config = LocalHostConfig.build(
        data_root=data_root,
        host_root=host_root,
        neo4j_home=neo4j_home,
        java_home=java_home,
        python_executable=_touch_executable(host_root / "python"),
    )
    _touch_executable(neo4j_home / "bin" / "neo4j")
    _touch_executable(neo4j_home / "bin" / "neo4j-admin")
    _touch_executable(java_home / "bin" / "java")
    config_path = tmp_path / "local.json"
    write_local_config(config, config_path)

    result = local_status_snapshot(config_path=config_path)

    assert result["configured"] is True
    assert all(result["configuration"]["runtime"].values())
    assert result["healthy"] is True
    assert main(["local", "status", "--config", str(config_path)]) == 0
    assert json.loads(capsys.readouterr().out)["healthy"] is True


def test_local_start_migrates_between_database_and_api_admission(
    monkeypatch,
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    config = LocalHostConfig.build(data_root=data_root)
    events: list[str] = []

    def manage(action, *, launch_agent_root, services):
        assert launch_agent_root == config.launch_agent_root
        events.append(f"{services[0]}-{action}")
        return {"successful": True}

    def migrate(selected):
        assert selected == config
        events.append("migrate")
        return {"ready": True, "reason": "clean"}

    monkeypatch.setattr("devgraph.cli.manage_services", manage)
    monkeypatch.setattr("devgraph.cli.apply_local_migrations", migrate)

    result = start_local_services(config)

    assert result["successful"] is True
    assert events == ["api-down", "neo4j-up", "migrate", "api-up"]
