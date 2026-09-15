from __future__ import annotations

import importlib.util
import plistlib
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NEO4J_CONFIG = ROOT / "deploy" / "local" / "native" / "neo4j.conf.in"
RUNBOOK = ROOT / "docs" / "runbooks" / "local-macos-self-host.md"
PROVISIONER = ROOT / "scripts" / "configure_local_host.py"
AGENT_RENDERER = ROOT / "scripts" / "render_local_launch_agents.py"


def test_neo4j_config_is_loopback_only_and_external() -> None:
    config = NEO4J_CONFIG.read_text(encoding="utf-8")

    for required in (
        "server.default_listen_address=127.0.0.1",
        "server.bolt.listen_address=127.0.0.1:7687",
        "server.http.enabled=false",
        "server.https.enabled=false",
        "server.directories.data=__DATA_ROOT__/neo4j/data",
        "server.directories.transaction.logs.root=__DATA_ROOT__/neo4j/transactions",
    ):
        assert required in config

    assert "0.0.0.0" not in config
    assert "password" not in config.lower()


def test_local_provisioner_generates_one_private_stable_secret(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("configure_local_host", PROVISIONER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.provision(tmp_path, require_mount=False) == ("created", "created")
    secret = tmp_path / "secrets" / "neo4j_password"
    config = tmp_path / "neo4j" / "conf" / "neo4j.conf"
    first_value = secret.read_text(encoding="utf-8")
    assert len(first_value.strip()) == 64
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    assert f"server.directories.data={tmp_path}/neo4j/data" in config.read_text(encoding="utf-8")

    assert module.provision(tmp_path, require_mount=False) == ("existing", "existing")
    assert secret.read_text(encoding="utf-8") == first_value


def test_launch_agents_are_path_gated_loopback_only_and_secret_file_backed(
    tmp_path: Path,
) -> None:
    spec = importlib.util.spec_from_file_location("render_local_launch_agents", AGENT_RENDERER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    host_root = tmp_path / "host"
    data_root = tmp_path / "data"
    log_root = tmp_path / "logs"
    agents = module.render_agents(host_root, data_root, log_root)
    api = plistlib.loads(agents["ca.zenith.devgraph.api.plist"])
    neo4j = plistlib.loads(agents["ca.zenith.devgraph.neo4j.plist"])

    assert api["ProgramArguments"][-4:] == ["--host", "127.0.0.1", "--port", "8080"]
    assert api["EnvironmentVariables"]["DEVGRAPH_AUTH_MODE"] == "local-read"
    assert api["EnvironmentVariables"]["DEVGRAPH_DATA_ROOT"] == str(data_root)
    assert api["EnvironmentVariables"]["NEO4J_PASSWORD_FILE"] == str(
        data_root / "secrets" / "neo4j_password"
    )
    assert "NEO4J_PASSWORD" not in api["EnvironmentVariables"]
    assert neo4j["EnvironmentVariables"]["NEO4J_CONF"] == str(data_root / "neo4j" / "conf")
    assert api["KeepAlive"] == {"PathState": {str(data_root): True}}
    assert api["StandardErrorPath"] == str(log_root / "launchd-api.err.log")
    assert neo4j["StandardOutPath"] == str(log_root / "launchd-neo4j.out.log")


def test_runbook_preserves_external_storage_and_non_claims() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    for required in (
        "/Volumes/Devgraph-Data",
        "/Volumes/MyExternalDrive/Devgraph",
        "--require-mounted-volume",
        "devgraph local configure",
        "devgraph local start",
        "devgraph local status",
        "Hub",
        "Neo4j 5.26.29",
        "protected work",
        "http://127.0.0.1:8080/work/Issue",
        "HTTP 401",
        "unencrypted",
        "initial-password command as a recovery step",
        "canonical or production",
    ):
        assert required in runbook

    assert "/work-objects" not in runbook
