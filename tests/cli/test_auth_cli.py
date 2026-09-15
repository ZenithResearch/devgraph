import json
from pathlib import Path

import pytest

import devgraph.cli as cli
from devgraph.local_host import LocalHostConfig, read_local_read_credential, write_local_config
from devgraph.ops import auth_setup, signer_profile


@pytest.fixture
def configured(monkeypatch, tmp_path):
    root = tmp_path / "Zenith"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(signer_profile, "INSTALL_ROOT", root)
    monkeypatch.setattr(signer_profile, "check_wallet_identity", lambda env: None)
    for name in signer_profile.SIGNER_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    data = tmp_path / "data"
    for relative in ("devgraph", "secrets"):
        (data / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
    config = LocalHostConfig.build(data_root=data)
    path = tmp_path / "local.json"
    write_local_config(config, path)
    return root, config, path


def test_auth_setup_status_forget_and_missing_check(monkeypatch, configured, capsys):
    root, _, path = configured
    assert (
        cli.main(["auth", "setup", "--key-file", "/private/existing/key", "--public-key", "a" * 64])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["identity_verified"] is True
    assert cli.main(["auth", "status", "--check", "--config", str(path)]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["signer"]["source"] == "saved_profile"
    assert value["named_work_authority"]["producer_bundle"] == "missing"
    assert value["named_work_authority"]["receiver_bundle"] == "missing"
    assert value["named_work_authority"]["grant_validity"] == "not_evaluated"
    assert cli.main(["auth", "forget"]) == 0
    assert json.loads(capsys.readouterr().out)["removed"] is True
    assert cli.main(["auth", "status", "--check", "--config", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["signer"]["configured"] is False


def test_provision_read_is_idempotent_rotate_is_explicit_and_secret_free(configured, capsys):
    _, config, path = configured
    args = ["auth", "read", "provision", "--config", str(path)]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "created"
    credential = read_local_read_credential(config)
    assert cli.main(args) == 0
    output = capsys.readouterr().out
    assert credential not in output
    assert json.loads(output)["state"] == "existing"
    assert read_local_read_credential(config) == credential
    assert cli.main(["auth", "read", "rotate", "--config", str(path)]) == 0
    output = capsys.readouterr().out
    assert credential not in output
    assert json.loads(output)["scope"] == "devgraph.read"
    assert read_local_read_credential(config) != credential


def test_auth_status_still_reports_signer_without_storage(configured, capsys):
    _, config, path = configured
    signer_profile.configure_signer(key_file=Path("/existing/key"), public_key="a" * 64)
    config.data_root.rename(config.data_root.with_name("offline"))
    assert cli.main(["auth", "status", "--config", str(path)]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["signer"]["configured"] is True
    assert value["storage_available"] is False
    assert cli.main(["auth", "read", "provision", "--config", str(path)]) == 2
    assert not config.data_root.exists()


def test_bundle_presence_is_never_reported_as_current_authority(configured):
    root, _, path = configured
    bundle = root / "secS/authority/devgraph.work.v1"
    bundle.mkdir(parents=True, mode=0o700)
    for name in (
        "producer-manifest.json",
        "receiver-policy.json",
        "secs-public-key-registry.json",
        "verifier.key",
    ):
        file = bundle / name
        file.write_text("not an actual grant")
        file.chmod(0o600)
    result = auth_setup.auth_status(config_path=path)
    assert result["named_work_authority"]["producer_bundle"] == "present_unverified"
    assert result["named_work_authority"]["grant_validity"] == "not_evaluated"


def test_missing_host_configuration_does_not_hide_signer_status(configured, capsys):
    _, _, path = configured
    path.unlink()
    assert cli.main(["auth", "status", "--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["read_credential"]["valid"] is False


def test_invalid_setup_prints_safe_failure_before_writing(configured, capsys):
    root, _, _ = configured
    assert (
        cli.main(["auth", "setup", "--key-file", "/existing/key", "--public-key", "not-a-pin"]) == 2
    )
    output = capsys.readouterr()
    assert "not-a-pin" not in output.err
    assert not (root / "Devgraph/auth/signer.json").exists()


def test_key_create_and_inspect_are_explicit_cli_commands(monkeypatch, configured, capsys):
    root, _, _ = configured
    calls = []

    def wallet(mode, path):
        calls.append((mode, path))
        return "a" * 64

    monkeypatch.setattr(signer_profile, "wallet_key_operation", wallet)
    assert cli.main(["auth", "key", "create"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["created"] is True and result["configured"] is True
    expected = root / "CastaliaWallet/keys/devgraph-dregg.key"
    assert calls[0] == ("--create-identity", expected)
    assert cli.main(["auth", "key", "inspect", "--key-file", str(expected)]) == 0
    assert json.loads(capsys.readouterr().out)["public_key"] == "a" * 64
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["auth", "key", "create", "--replace"])


def test_key_created_but_profile_incomplete_has_nonzero_exit(monkeypatch, configured, capsys):
    monkeypatch.setattr(cli, "create_signer", lambda **kw: {"created": True, "configured": False})
    assert cli.main(["auth", "key", "create"]) == 2
    assert json.loads(capsys.readouterr().out) == {"created": True, "configured": False}
