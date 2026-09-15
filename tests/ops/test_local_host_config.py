from __future__ import annotations

import json
import os
import plistlib
import stat
import subprocess
from pathlib import Path

import pytest

from devgraph import local_host
from devgraph.local_host import (
    MIGRATION_MANIFEST_PATH,
    ConfigurationError,
    LocalHostConfig,
    configure_local_host,
    load_local_config,
    render_agents,
    write_local_config,
)

ROOT = Path(__file__).resolve().parents[2]


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    path.chmod(0o700)
    return path


def _config(tmp_path: Path) -> LocalHostConfig:
    data_root = tmp_path / "chosen-data"
    data_root.mkdir()
    host_root = tmp_path / "host"
    neo4j_home = host_root / "neo4j"
    java_home = host_root / "java"
    python_executable = _touch(host_root / "python" / "bin" / "python")
    _touch(neo4j_home / "bin" / "neo4j")
    _touch(neo4j_home / "bin" / "neo4j-admin")
    _touch(java_home / "bin" / "java")
    return LocalHostConfig.build(
        data_root=data_root,
        host_root=host_root,
        log_root=tmp_path / "logs",
        launch_agent_root=tmp_path / "agents",
        neo4j_home=neo4j_home,
        java_home=java_home,
        python_executable=python_executable,
    )


def test_packaged_migrations_exactly_match_the_canonical_bundle() -> None:
    canonical_root = ROOT / "migrations"
    packaged_root = MIGRATION_MANIFEST_PATH.parent

    assert {path.name for path in packaged_root.iterdir()} == {
        path.name for path in canonical_root.iterdir() if path.is_file()
    }
    for canonical in canonical_root.iterdir():
        if canonical.is_file():
            assert (packaged_root / canonical.name).read_bytes() == canonical.read_bytes()


def test_packaged_neo4j_template_matches_the_deployment_template() -> None:
    packaged = ROOT / "src" / "devgraph" / "resources" / "neo4j.conf.in"
    canonical = ROOT / "deploy" / "local" / "native" / "neo4j.conf.in"

    assert packaged.read_bytes() == canonical.read_bytes()


def test_local_config_round_trips_privately_and_allows_additive_fields(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config_path = tmp_path / "config" / "local.json"

    assert write_local_config(config, config_path) == "created"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert load_local_config(config_path) == config

    mapping = json.loads(config_path.read_text(encoding="utf-8"))
    mapping["future_optional_field"] = "ignored"
    config_path.write_text(json.dumps(mapping), encoding="utf-8")
    config_path.chmod(0o600)
    assert load_local_config(config_path) == config


def test_local_config_preserves_the_isolated_python_entrypoint(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    interpreter = _touch(tmp_path / "runtime" / "python-real")
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(interpreter)

    config = LocalHostConfig.build(
        data_root=data_root,
        python_executable=venv_python,
    )

    assert config.python_executable == venv_python


def test_local_config_rejects_broad_or_overexposed_configuration(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigurationError, match="dedicated directory"):
        LocalHostConfig.build(data_root=Path.home())

    config = _config(tmp_path)
    config_path = tmp_path / "local.json"
    write_local_config(config, config_path)
    config_path.chmod(0o644)
    with pytest.raises(ConfigurationError, match="permissions"):
        load_local_config(config_path)


def test_local_config_accepts_an_immutable_root_owned_mountpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = (tmp_path / "mounted-data").resolve()
    data_root.mkdir()
    original_lstat = Path.lstat

    def fake_lstat(path: Path):
        if path == data_root:
            return os.stat_result(
                (stat.S_IFDIR | 0o775, 1, 1, 1, 0, 0, 0, 0, 0, 0)
            )
        return original_lstat(path)

    monkeypatch.setattr(local_host, "_containing_mount", lambda _path: data_root)
    monkeypatch.setattr(
        local_host.os,
        "access",
        lambda _path, mode: mode == os.X_OK,
    )
    monkeypatch.setattr(Path, "lstat", fake_lstat)

    config = LocalHostConfig.build(
        data_root=data_root,
        require_mounted_volume=True,
    )

    assert config.data_root == data_root
    assert config.availability_path == data_root


def test_local_config_rejects_an_unrelated_availability_gate(tmp_path: Path) -> None:
    config = _config(tmp_path)
    mapping = config.to_mapping()
    mapping["availability_path"] = str(tmp_path / "unrelated")
    config_path = tmp_path / "local.json"
    config_path.write_text(json.dumps(mapping), encoding="utf-8")
    config_path.chmod(0o600)

    with pytest.raises(ConfigurationError, match="availability path"):
        load_local_config(config_path)


def test_agents_use_selected_root_package_runtime_and_local_read_auth(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    agents = render_agents(config)
    api = plistlib.loads(agents["ca.zenith.devgraph.api.plist"])
    neo4j = plistlib.loads(agents["ca.zenith.devgraph.neo4j.plist"])

    assert api["ProgramArguments"][:3] == [
        str(config.python_executable),
        "-m",
        "uvicorn",
    ]
    assert api["EnvironmentVariables"]["DEVGRAPH_AUTH_MODE"] == "local-read"
    assert "PYTHONPATH" not in api["EnvironmentVariables"]
    assert api["EnvironmentVariables"]["NEO4J_PASSWORD_FILE"] == str(
        config.data_root / "secrets" / "neo4j_password"
    )
    assert neo4j["EnvironmentVariables"]["NEO4J_CONF"] == str(config.data_root / "neo4j" / "conf")
    assert api["KeepAlive"] == {"PathState": {str(config.availability_path): True}}


def test_configure_provisions_an_isolated_selected_root_without_secret_output(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    config_path = config.host_root / "local.json"
    observed_commands: list[list[str]] = []

    def password_runner(command, **kwargs):
        observed_commands.append(command)
        marker = config.data_root / "neo4j" / "data" / "initialized"
        marker.write_text("fixture", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    result = configure_local_host(
        config,
        config_path=config_path,
        password_runner=password_runner,
    )

    assert result["ready_to_start"] is True
    assert result["neo4j_credential"] == "created"
    assert result["read_credential"] == "created"
    assert result["neo4j_initial_password"] == "initialized"
    assert len(observed_commands) == 1
    secret = config.data_root / "secrets" / "neo4j_password"
    assert secret.read_text(encoding="utf-8").strip() not in json.dumps(result)
    read_credential = (
        config.data_root / "devgraph" / "credentials" / "devgraph.read"
    )
    assert read_credential.read_text(encoding="utf-8").strip() not in json.dumps(result)
    assert load_local_config(config_path) == config
    assert (config.launch_agent_root / "ca.zenith.devgraph.api.plist").is_file()
    assert f"server.directories.data={config.data_root}/neo4j/data" in (
        config.data_root / "neo4j" / "conf" / "neo4j.conf"
    ).read_text(encoding="utf-8")

    second = configure_local_host(
        config,
        config_path=config_path,
        password_runner=password_runner,
    )
    assert second["neo4j_credential"] == "existing"
    assert second["read_credential"] == "existing"
    assert second["neo4j_initial_password"] == "existing"
    assert len(observed_commands) == 1
