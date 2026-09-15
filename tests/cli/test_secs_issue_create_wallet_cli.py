from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import devgraph.cli as cli
import devgraph.ops.secs_issue_create_wallet as wallet
from devgraph.cli import _parser, main


def _private_executable(path: Path) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(b"fixed adapter")
    path.chmod(0o700)
    return path


def _private_wallet_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path]:
    install_root = tmp_path / "Zenith"
    install_root.mkdir(mode=0o700)
    binary = _private_executable(
        install_root / wallet.SECS_WALLET_BINARY_RELATIVE_PATH
    )
    monkeypatch.setattr(wallet, "SECS_WALLET_INSTALL_ROOT", install_root)
    monkeypatch.setattr(wallet, "SECS_WALLET_BINARY_PATH", binary)
    return install_root, binary


def test_wallet_composition_invokes_fixed_adapter_then_exact_receiver(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, binary = _private_wallet_install(monkeypatch, tmp_path)
    request = tmp_path / "request.json"
    idempotency = tmp_path / "idempotency-key.txt"
    commands: list[list[str]] = []
    receiver_calls: list[dict[str, Path]] = []

    def run(command):
        commands.append(list(command))
        projection = Path(command[-1])
        assert projection.name == "signed-projection.json"
        assert projection.exists() is False
        assert projection.parent.stat().st_mode & 0o077 == 0
        projection.write_bytes(b"signed authority")
        projection.chmod(0o600)
        return subprocess.CompletedProcess(command, 0)

    def consume(**kwargs):
        receiver_calls.append(kwargs)
        assert kwargs["signed_projection_file"].read_bytes() == b"signed authority"
        return {"operation": "devgraph.issue.create.v1", "receipt": {"id": "receipt-1"}}

    monkeypatch.setattr(wallet, "_run_wallet_adapter", run)
    monkeypatch.setattr(wallet, "execute_local_secs_issue_create_v1", consume)

    result = wallet.execute_local_wallet_secs_issue_create_v1(
        request_file=request,
        idempotency_key_file=idempotency,
    )

    assert result["receipt"] == {"id": "receipt-1"}
    assert commands == [
        [
            str(binary),
            "--request-file",
            str(request),
            "--idempotency-key-file",
            str(idempotency),
            "--signed-projection-output",
            receiver_calls[0]["signed_projection_file"].as_posix(),
        ]
    ]
    assert receiver_calls[0]["request_file"] == request
    assert receiver_calls[0]["idempotency_key_file"] == idempotency
    assert receiver_calls[0]["signed_projection_file"].exists() is False


def test_wallet_composition_does_not_consume_cancelled_ceremony(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, binary = _private_wallet_install(monkeypatch, tmp_path)
    consumed = False

    def consume(**_kwargs):
        nonlocal consumed
        consumed = True

    monkeypatch.setattr(
        wallet,
        "_run_wallet_adapter",
        lambda command: subprocess.CompletedProcess(command, 2),
    )
    monkeypatch.setattr(wallet, "execute_local_secs_issue_create_v1", consume)

    with pytest.raises(
        wallet.LocalSecSWalletIssueCreateError,
        match="ceremony did not complete",
    ):
        wallet.execute_local_wallet_secs_issue_create_v1(
            request_file=tmp_path / "request.json",
            idempotency_key_file=tmp_path / "idempotency-key.txt",
        )
    assert consumed is False


def test_wallet_composition_accepts_owner_execute_only_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, binary = _private_wallet_install(monkeypatch, tmp_path)
    binary.chmod(0o100)
    monkeypatch.setattr(
        wallet,
        "_run_wallet_adapter",
        lambda command: subprocess.CompletedProcess(command, 2),
    )

    with pytest.raises(
        wallet.LocalSecSWalletIssueCreateError,
        match="ceremony did not complete",
    ):
        wallet.execute_local_wallet_secs_issue_create_v1(
            request_file=tmp_path / "request.json",
            idempotency_key_file=tmp_path / "idempotency-key.txt",
        )


def test_wallet_composition_accepts_owner_search_only_install_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root, _ = _private_wallet_install(monkeypatch, tmp_path)
    (install_root / "secS").chmod(0o100)
    monkeypatch.setattr(
        wallet,
        "_run_wallet_adapter",
        lambda command: subprocess.CompletedProcess(command, 2),
    )

    with pytest.raises(
        wallet.LocalSecSWalletIssueCreateError,
        match="ceremony did not complete",
    ):
        wallet.execute_local_wallet_secs_issue_create_v1(
            request_file=tmp_path / "request.json",
            idempotency_key_file=tmp_path / "idempotency-key.txt",
        )


@pytest.mark.parametrize("mode", (0o600, 0o720))
def test_wallet_composition_rejects_unsafe_fixed_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: int,
) -> None:
    _, binary = _private_wallet_install(monkeypatch, tmp_path)
    binary.chmod(mode)

    with pytest.raises(wallet.LocalSecSWalletIssueCreateError, match="installation is unsafe"):
        wallet.execute_local_wallet_secs_issue_create_v1(
            request_file=tmp_path / "request.json",
            idempotency_key_file=tmp_path / "idempotency-key.txt",
        )


def test_wallet_composition_rejects_group_writable_install_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root, _ = _private_wallet_install(monkeypatch, tmp_path)
    (install_root / "secS" / "bin").chmod(0o720)

    with pytest.raises(wallet.LocalSecSWalletIssueCreateError, match="installation is unsafe"):
        wallet.execute_local_wallet_secs_issue_create_v1(
            request_file=tmp_path / "request.json",
            idempotency_key_file=tmp_path / "idempotency-key.txt",
        )


def test_wallet_composition_rejects_group_writable_ancestor_above_install_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unsafe_ancestor = tmp_path / "unsafe-ancestor"
    unsafe_ancestor.mkdir(mode=0o700)
    install_root = unsafe_ancestor / "Zenith"
    install_root.mkdir(mode=0o700)
    binary = _private_executable(
        install_root / wallet.SECS_WALLET_BINARY_RELATIVE_PATH
    )
    unsafe_ancestor.chmod(0o720)
    monkeypatch.setattr(wallet, "SECS_WALLET_INSTALL_ROOT", install_root)
    monkeypatch.setattr(wallet, "SECS_WALLET_BINARY_PATH", binary)

    with pytest.raises(wallet.LocalSecSWalletIssueCreateError, match="installation is unsafe"):
        wallet.execute_local_wallet_secs_issue_create_v1(
            request_file=tmp_path / "request.json",
            idempotency_key_file=tmp_path / "idempotency-key.txt",
        )


def test_wallet_composition_rejects_symlinked_install_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "Zenith"
    install_root.mkdir(mode=0o700)
    real_secs = tmp_path / "real-secs"
    _private_executable(real_secs / "bin" / wallet.SECS_WALLET_BINARY_NAME)
    (install_root / "secS").symlink_to(real_secs, target_is_directory=True)
    monkeypatch.setattr(wallet, "SECS_WALLET_INSTALL_ROOT", install_root)
    monkeypatch.setattr(
        wallet,
        "SECS_WALLET_BINARY_PATH",
        install_root / wallet.SECS_WALLET_BINARY_RELATIVE_PATH,
    )

    with pytest.raises(wallet.LocalSecSWalletIssueCreateError, match="installation is unsafe"):
        wallet.execute_local_wallet_secs_issue_create_v1(
            request_file=tmp_path / "request.json",
            idempotency_key_file=tmp_path / "idempotency-key.txt",
        )


def test_wallet_composition_rejects_hard_linked_fixed_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, binary = _private_wallet_install(monkeypatch, tmp_path)
    os.link(binary, tmp_path / "second-wallet-link")

    with pytest.raises(wallet.LocalSecSWalletIssueCreateError, match="installation is unsafe"):
        wallet.execute_local_wallet_secs_issue_create_v1(
            request_file=tmp_path / "request.json",
            idempotency_key_file=tmp_path / "idempotency-key.txt",
        )


def test_wallet_composition_rejects_fixed_adapter_acl_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _private_wallet_install(monkeypatch, tmp_path)

    def reject_acl(_descriptor: int) -> None:
        raise wallet.LocalPathIntegrityError("configured receiver file has an extended ACL")

    monkeypatch.setattr(wallet, "require_file_descriptor_without_acl", reject_acl)

    with pytest.raises(wallet.LocalSecSWalletIssueCreateError, match="installation is unsafe"):
        wallet.execute_local_wallet_secs_issue_create_v1(
            request_file=tmp_path / "request.json",
            idempotency_key_file=tmp_path / "idempotency-key.txt",
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="requires the macOS ACL API")
@pytest.mark.parametrize("target_kind", ("directory", "file"))
def test_wallet_composition_rejects_real_darwin_install_acl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_kind: str,
) -> None:
    install_root, binary = _private_wallet_install(monkeypatch, tmp_path)
    target = install_root / "secS" if target_kind == "directory" else binary
    subprocess.run(
        ["chmod", "+a", "everyone allow read", os.fspath(target)],
        check=True,
        capture_output=True,
        text=True,
    )

    with pytest.raises(wallet.LocalSecSWalletIssueCreateError, match="installation is unsafe"):
        wallet.execute_local_wallet_secs_issue_create_v1(
            request_file=tmp_path / "request.json",
            idempotency_key_file=tmp_path / "idempotency-key.txt",
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="requires the macOS ACL API")
def test_wallet_composition_accepts_darwin_deny_only_directory_acl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root, _ = _private_wallet_install(monkeypatch, tmp_path)
    subprocess.run(
        ["chmod", "+a", "everyone deny delete", os.fspath(install_root / "secS")],
        check=True,
        capture_output=True,
        text=True,
    )
    monkeypatch.setattr(
        wallet,
        "_run_wallet_adapter",
        lambda command: subprocess.CompletedProcess(command, 2),
    )

    with pytest.raises(
        wallet.LocalSecSWalletIssueCreateError,
        match="ceremony did not complete",
    ):
        wallet.execute_local_wallet_secs_issue_create_v1(
            request_file=tmp_path / "request.json",
            idempotency_key_file=tmp_path / "idempotency-key.txt",
        )


def test_wallet_cli_has_only_exact_request_and_idempotency_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "execute_local_wallet_secs_issue_create_v1",
        lambda **_kwargs: {
            "duplicate": False,
            "operation": "devgraph.issue.create.v1",
            "receipt": {"id": "receipt-1"},
        },
    )
    code = main(
        [
            "wallet-issue-create-v1",
            "--request-file",
            str(tmp_path / "request.json"),
            "--idempotency-key-file",
            str(tmp_path / "idempotency-key.txt"),
        ]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out)["receipt"] == {"id": "receipt-1"}
    parser = _parser()
    command_parser = next(
        action.choices["wallet-issue-create-v1"]
        for action in parser._actions
        if isinstance(getattr(action, "choices", None), dict)
    )
    options = {
        option
        for action in command_parser._actions
        for option in action.option_strings
        if option not in {"--help", "-h"}
    }
    assert options == {"--idempotency-key-file", "--request-file"}


def test_wallet_cli_failure_is_bounded_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "execute_local_wallet_secs_issue_create_v1",
        lambda **_kwargs: (_ for _ in ()).throw(
            wallet.LocalSecSWalletIssueCreateError("the secS Wallet ceremony did not complete")
        ),
    )

    code = main(
        [
            "wallet-issue-create-v1",
            "--request-file",
            str(tmp_path / "private-request.json"),
            "--idempotency-key-file",
            str(tmp_path / "private-idempotency-key.txt"),
        ]
    )
    output = capsys.readouterr()

    assert code == 2
    assert output.out == ""
    assert json.loads(output.err) == {"error": "the secS Wallet ceremony did not complete"}
    assert "private-request" not in output.err
    assert "private-idempotency" not in output.err
