from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import devgraph.cli as cli
import devgraph.ops.secs_issue_create_agent as agent


def private(path: Path, raw: bytes) -> Path:
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


def fixture(monkeypatch, tmp_path):
    root = tmp_path / "Zenith"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(agent, "INSTALL_ROOT", root)
    for relative in (agent.WALLET_BINARY, agent.SECS_BINARY):
        path = root / relative
        path.parent.mkdir(parents=True, mode=0o700)
        private(path, b"fixture executable").chmod(0o700)
    request = private(tmp_path / "request.json", b'{"id":"issue-a","kind":"Issue","title":"A"}')
    key = private(tmp_path / "idempotency.txt", b"agent-idempotency-0001\n")
    return root, request, key


def test_agent_composition_snapshots_inputs_and_isolates_key_reference(monkeypatch, tmp_path):
    root, request, key = fixture(monkeypatch, tmp_path)
    original = request.read_bytes()
    commands = []
    observed = []
    monkeypatch.setenv("DEVGRAPH_SIGNING_KEY_FILE", "/private/identity.key")
    monkeypatch.setenv("DEVGRAPH_SIGNING_PUBLIC_KEY", "ab" * 32)
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-reach-child")

    def run(command, *, env):
        commands.append((command, env))
        assert "UNRELATED_SECRET" not in env
        assert Path(command[2]).parent.stat().st_mode & 0o077 == 0
        if len(commands) == 1:
            assert command[0] == str(root / agent.WALLET_BINARY)
            assert env["DEVGRAPH_SIGNING_KEY_FILE"] == "/private/identity.key"
            assert Path(command[2]).read_bytes() == original
            # Editing the caller's files after signing must not change execution.
            request.write_bytes(b"different request")
            key.write_bytes(b"different idempotency")
            private(Path(command[-1]), b"signed producer envelope")
        else:
            assert command[0] == str(root / agent.SECS_BINARY)
            assert "DEVGRAPH_SIGNING_KEY_FILE" not in env
            assert "DEVGRAPH_SIGNING_PUBLIC_KEY" not in env
            assert Path(command[2]).read_bytes() == b"signed producer envelope"
            private(Path(command[-1]), b"signed authority")
        return subprocess.CompletedProcess(command, 0)

    def consume(**kwargs):
        observed.append(kwargs)
        assert kwargs["request_file"].read_bytes() == original
        assert kwargs["idempotency_key_file"].read_bytes() == b"agent-idempotency-0001\n"
        assert kwargs["signed_projection_file"].read_bytes() == b"signed authority"
        return {"operation": "devgraph.issue.create.v1", "issue": {"id": "issue-a"}}

    monkeypatch.setattr(agent, "_run_adapter", run)
    monkeypatch.setattr(agent, "execute_local_secs_issue_create_v1", consume)
    result = agent.execute_local_agent_secs_issue_create_v1(
        request_file=request, idempotency_key_file=key
    )
    assert result["issue"]["id"] == "issue-a"
    assert len(commands) == 2
    assert not observed[0]["request_file"].parent.exists()


@pytest.mark.parametrize("failure", (1, 2))
def test_signer_or_policy_denial_never_calls_receiver(monkeypatch, tmp_path, failure):
    _, request, key = fixture(monkeypatch, tmp_path)
    calls = []

    def run(command, *, env):
        calls.append(command)
        if len(calls) == failure:
            return subprocess.CompletedProcess(command, 2)
        private(Path(command[-1]), b"envelope")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(agent, "_run_adapter", run)
    monkeypatch.setattr(
        agent,
        "execute_local_secs_issue_create_v1",
        lambda **_: pytest.fail("denial reached receiver"),
    )
    with pytest.raises(agent.LocalAgentIssueCreateError):
        agent.execute_local_agent_secs_issue_create_v1(
            request_file=request, idempotency_key_file=key
        )
    assert len(calls) == failure
    assert not Path(calls[0][-1]).parent.exists()


def test_unsafe_fixed_signer_is_rejected_before_spawn(monkeypatch, tmp_path):
    root, request, key = fixture(monkeypatch, tmp_path)
    (root / agent.WALLET_BINARY).chmod(0o722)
    monkeypatch.setattr(
        agent, "_run_adapter", lambda *a, **kw: pytest.fail("unsafe executable ran")
    )
    with pytest.raises(agent.LocalAgentIssueCreateError, match="installation"):
        agent.execute_local_agent_secs_issue_create_v1(
            request_file=request, idempotency_key_file=key
        )


def test_cli_dispatch_and_no_authority_overrides(monkeypatch, tmp_path, capsys):
    request = tmp_path / "request.json"
    key = tmp_path / "idempotency.txt"
    seen = []

    def execute(**kwargs):
        seen.append(kwargs)
        return {"operation": "devgraph.issue.create.v1"}

    monkeypatch.setattr(cli, "execute_local_agent_secs_issue_create_v1", execute)
    args = [
        "agent-issue-create-v1",
        "--request-file",
        str(request),
        "--idempotency-key-file",
        str(key),
    ]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["operation"] == "devgraph.issue.create.v1"
    assert seen == [{"request_file": request, "idempotency_key_file": key}]
    for flag in ("--url", "--policy", "--wallet-binary", "--signing-key", "--audience"):
        with pytest.raises(SystemExit):
            cli._parser().parse_args([*args, flag, "untrusted"])


def test_subprocess_errors_are_redacted(monkeypatch):
    def fail(*args, **kwargs):
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 30
        raise OSError("secret child details")

    monkeypatch.setattr(agent.subprocess, "run", fail)
    with pytest.raises(agent.LocalAgentIssueCreateError, match="could not complete") as error:
        agent._run_adapter(["fixed"], env={})
    assert "secret" not in str(error.value)
