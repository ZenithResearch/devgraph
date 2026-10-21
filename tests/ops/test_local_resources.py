import json
from pathlib import Path

import pytest

from devgraph.local_resources import (
    LocalResourceError,
    _decode,
    refresh_resource,
    register_resource,
    validate_record,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
VECTORS = json.loads((FIXTURES / "zenith-local-resource-v1.json").read_text())["cases"]


@pytest.mark.parametrize("case", VECTORS, ids=lambda case: case["name"])
def test_shared_discovery_vectors(case):
    if case["accepted"]:
        validate_record(_decode(case["raw"].encode()))
    else:
        with pytest.raises(LocalResourceError):
            validate_record(_decode(case["raw"].encode()))


def instance(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    root = home / "instance"
    root.mkdir(mode=0o700)
    config = root / "local.json"
    config.write_text('{"existing_config":"unchanged"}')
    config.chmod(0o600)
    return home, config


def test_stable_registration_refresh_and_public_config_preserved(tmp_path):
    home, config = instance(tmp_path)
    zenith = home / ".zenith"
    zenith.mkdir(mode=0o700)
    shared_config = zenith / "config.json"
    shared_config.write_text('{"storage":"existing"}')
    before = config.read_bytes(), shared_config.read_bytes()
    first = register_resource(config, home=home)
    second = register_resource(config, home=home)
    assert first["resource_id"] == second["resource_id"]
    target = Path(first["record_path"])
    record = validate_record(_decode(target.read_bytes()))
    assert record["connection"] is None and record["endpoint"] == "http://127.0.0.1:8080"
    assert target.stat().st_mode & 0o777 == 0o600
    target.unlink()  # Losing discovery cache must not replace instance identity.
    restored = refresh_resource(config, home=home)
    assert restored["resource_id"] == first["resource_id"]
    assert target.is_file()
    assert before == (config.read_bytes(), shared_config.read_bytes())
    assert first["authority_granted"] is False


def test_changed_profile_needs_replace_and_keeps_id(tmp_path):
    home, config = instance(tmp_path)
    first = register_resource(config, home=home)
    profile = tmp_path / "public-profile.json"
    profile.write_text(
        json.dumps(
            json.loads((FIXTURES / "zenith-local-resource-example.json").read_text())["connection"]
        )
    )
    before = Path(first["record_path"]).read_bytes()
    with pytest.raises(LocalResourceError):
        register_resource(config, connection_file=profile, home=home)
    assert Path(first["record_path"]).read_bytes() == before
    changed = register_resource(config, connection_file=profile, replace=True, home=home)
    assert changed["resource_id"] == first["resource_id"]
    record = json.loads(Path(changed["record_path"]).read_text())
    assert record["endpoint"] == "https://graph.example"
    assert record["connection"]["authority"]["audience"] == "devgraph"


def test_links_permissions_and_bad_profiles_stop_without_overwrite(tmp_path):
    home, config = instance(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (home / ".zenith").symlink_to(outside, target_is_directory=True)
    with pytest.raises(LocalResourceError):
        register_resource(config, home=home)
    assert list(outside.iterdir()) == []
    (home / ".zenith").unlink()
    record = register_resource(config, home=home)
    target = Path(record["record_path"])
    original = target.read_bytes()
    target.chmod(0o644)
    with pytest.raises(LocalResourceError):
        register_resource(config, home=home)
    assert target.read_bytes() == original
    target.chmod(0o600)
    victim = outside / "victim.json"
    victim.write_bytes(original)
    target.unlink()
    target.symlink_to(victim)
    with pytest.raises(LocalResourceError):
        register_resource(config, home=home)
    assert victim.read_bytes() == original
    profile = tmp_path / "bad.json"
    profile.write_text('{"private_key":"must not enter discovery"}')
    with pytest.raises(LocalResourceError):
        register_resource(config, home=home, connection_file=profile)


def test_refresh_does_not_register_unconfigured_instance_or_follow_links(tmp_path):
    home, config = instance(tmp_path)
    assert refresh_resource(config, home=home) == {"published": False, "reason": "not_registered"}
    assert not (home / ".zenith").exists()
    config.with_name("resource-registration.json").symlink_to(config)
    assert refresh_resource(config, home=home)["published"] is False


def test_cli_registration_and_successful_start_refresh(monkeypatch, tmp_path, capsys):
    import devgraph.cli as cli
    import devgraph.local_resources as local

    home, config = instance(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    sentinel = object()
    monkeypatch.setattr(cli, "load_local_config", lambda _: sentinel)
    assert cli.main(["local", "resource", "register", "--config", str(config)]) == 0
    registered = json.loads(capsys.readouterr().out)
    assert registered["published"] is True
    monkeypatch.setattr(cli, "_optional_local_config", lambda _: sentinel)
    # CLI's launch-agent-root access is deliberately represented, not a live host.
    from types import SimpleNamespace

    sentinel = SimpleNamespace(launch_agent_root=tmp_path)
    monkeypatch.setattr(cli, "start_local_services", lambda _: {"successful": True})
    assert cli.main(["local", "start", "--config", str(config)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["local_resource"]["resource_id"] == registered["resource_id"]
    called = []
    monkeypatch.setattr(local, "refresh_resource", lambda _: called.append(True))
    monkeypatch.setattr(cli, "start_local_services", lambda _: {"successful": False})
    assert cli.main(["local", "start", "--config", str(config)]) == 1
    assert not called
