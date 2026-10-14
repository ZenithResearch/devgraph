import json
import subprocess
from pathlib import Path

import pytest
from tests.auth.test_secs_work import KEY, client_and_services, proof, request

import devgraph.cli as cli
import devgraph.ops.named_work_agent as agent
from devgraph.ops import signer_profile
from devgraph.work_requests import WORK_OPERATIONS, WorkRequest


def private(path, raw):
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


def setup(monkeypatch, tmp_path):
    root = tmp_path / "Zenith"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(agent, "INSTALL_ROOT", root)
    for relative in (agent.WALLET_BINARY, agent.SECS_BINARY):
        path = root / relative
        path.parent.mkdir(mode=0o700, parents=True)
        private(path, b"fixture").chmod(0o700)
    return private(tmp_path / "request.json", request()), private(
        tmp_path / "key.txt", KEY.encode()
    )


@pytest.mark.parametrize("source", ["environment", "saved_profile"])
def test_cli_snapshots_signs_and_submits_fixed_http_request(monkeypatch, tmp_path, source):
    request_file, key_file = setup(monkeypatch, tmp_path)
    raw = WorkRequest.from_json(request_file.read_bytes()).canonical
    calls = []
    monkeypatch.setenv("DEVGRAPH_SIGNING_KEY_FILE", "/existing/private/identity.key")
    monkeypatch.setenv("DEVGRAPH_SIGNING_PUBLIC_KEY", "a" * 64)
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-reach-child")
    if source == "saved_profile":
        monkeypatch.setattr(signer_profile, "INSTALL_ROOT", agent.INSTALL_ROOT)
        monkeypatch.setattr(signer_profile, "check_wallet_identity", lambda env: None)
        signer_profile.configure_signer(
            key_file=Path("/existing/private/identity.key"), public_key="a" * 64
        )
        for name in agent.SIGNER_ENVIRONMENT:
            monkeypatch.delenv(name)

    def run(command, *, env):
        calls.append(command)
        if len(calls) == 1:
            assert set(env) == set(agent.SIGNER_ENVIRONMENT)
            assert env["DEVGRAPH_SIGNING_KEY_FILE"] == "/existing/private/identity.key"
            assert Path(command[2]).read_bytes() == raw
            assert Path(command[4]).read_bytes() == (KEY + "\n").encode()
            request_file.write_bytes(b"changed after signing")
            key_file.write_bytes(b"changed after signing")
            private(Path(command[-1]), b"wallet-envelope")
        else:
            assert env == {}
            assert Path(command[2]).read_bytes() == b"wallet-envelope"
            private(Path(command[-1]), proof(raw))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(agent, "_run_adapter", run)
    transport, services, _ = client_and_services()
    result = agent.execute_local_named_work(
        request_file=request_file,
        idempotency_key_file=key_file,
        operation="create",
        transport=transport,
    )
    assert result["work"]["id"] == "test-one"
    assert result["receipt"]["operation"] == "devgraph.work.create.v1"
    assert len(services.storage.query("Issue")) == 1
    assert len(calls) == 2
    assert not Path(calls[0][2]).parent.exists()


@pytest.mark.parametrize("operation", WORK_OPERATIONS)
def test_every_named_command_dispatches_without_authority_overrides(
    monkeypatch, tmp_path, capsys, operation
):
    seen = []
    monkeypatch.setattr(
        cli, "execute_local_named_work", lambda **kw: seen.append(kw) or {"ok": True}
    )
    args = [
        "work",
        operation,
        "--request-file",
        str(tmp_path / "request"),
        "--idempotency-key-file",
        str(tmp_path / "key"),
    ]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}
    assert seen[0]["operation"] == operation
    for flag in ["--url", "--policy", "--wallet-binary", "--signing-key", "--audience", "--bearer"]:
        with pytest.raises(SystemExit):
            cli._parser().parse_args([*args, flag, "untrusted"])


@pytest.mark.parametrize("failure", [1, 2])
def test_native_denials_stop_before_http(monkeypatch, tmp_path, failure):
    request_file, key_file = setup(monkeypatch, tmp_path)
    calls = []

    def run(command, *, env):
        calls.append(command)
        if len(calls) == failure:
            return subprocess.CompletedProcess(command, 2)
        private(Path(command[-1]), b"envelope")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(agent, "_run_adapter", run)

    class NoTransport:
        def request(self, *a, **kw):
            pytest.fail("denied request reached HTTP")

    with pytest.raises(agent.LocalNamedWorkAgentError):
        agent.execute_local_named_work(
            request_file=request_file,
            idempotency_key_file=key_file,
            operation="create",
            transport=NoTransport(),
        )
    assert not Path(calls[0][2]).parent.exists()


def test_mismatched_command_is_rejected_before_signing(monkeypatch, tmp_path):
    request_file, key_file = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(agent, "_run_adapter", lambda *a, **kw: pytest.fail("mismatch was signed"))
    with pytest.raises(agent.LocalNamedWorkAgentError, match="differ"):
        agent.execute_local_named_work(
            request_file=request_file, idempotency_key_file=key_file, operation="archive"
        )


@pytest.mark.parametrize("failure,code,category", [
    (1, "invalid_work_request", "wallet_request_incompatible"),
    (1, "unsafe_signing_key_file", "wallet_custody_unavailable"),
    (1, "signing_identity_mismatch", "wallet_custody_unavailable"),
    (1, "invalid_arguments", "wallet_installation_incompatible"),
    (2, "invalid_work_request", "secs_request_incompatible"),
    (2, "work_permission_denied", "secs_authority_denied"),
    (2, "work_manifest_binding_mismatch", "secs_authority_unavailable"),
    (2, "work_authority_expired_before_output", "secs_authority_denied"),
])
def test_valid_workflow_failure_is_classified_without_http_or_request_changes(
    monkeypatch, tmp_path, failure, code, category,
):
    request_file, key_file = setup(monkeypatch, tmp_path)
    # This is accepted by today's request contract, but older native parsers
    # reject workflow.transition before touching custody or dispatching HTTP.
    raw = json.dumps({
        "schema": "devgraph.work-request.v1", "operation": "workflow.transition",
        "kind": "Initiative", "id": "example-initiative", "expected_version": 6,
        "payload": {"stage": "planning"},
    }).encode()
    request_file.write_bytes(raw)
    canonical = WorkRequest.from_json(raw).canonical
    original_key = key_file.read_bytes()
    monkeypatch.setattr(agent, "signer_environment", lambda: {})
    calls = []

    def run(command, *, env):
        calls.append(command)
        if len(calls) == 1:
            assert Path(command[2]).read_bytes() == canonical
        if len(calls) == failure:
            return subprocess.CompletedProcess(
                command, 2, stderr=json.dumps({"error": code}).encode(),
            )
        private(Path(command[-1]), b"wallet-envelope")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(agent, "_run_adapter", run)

    class NoTransport:
        def request(self, *args, **kwargs):
            pytest.fail("native rejection reached HTTP")

    with pytest.raises(agent.LocalNamedWorkAgentError, match=category):
        agent.execute_local_named_work(
            request_file=request_file, idempotency_key_file=key_file,
            operation="workflow.transition", transport=NoTransport(),
        )
    assert len(calls) == failure
    assert request_file.read_bytes() == raw
    assert key_file.read_bytes() == original_key
    assert not Path(calls[0][2]).parent.exists()


def test_cli_does_not_disclose_unrecognized_adapter_stderr(monkeypatch, tmp_path, capsys):
    request_file, key_file = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(agent, "signer_environment", lambda: {})
    monkeypatch.setattr(agent, "_run_adapter", lambda *a, **kw: subprocess.CompletedProcess(
        a[0], 2, stderr=b'{"error":"invalid_work_request","secret":"private-marker"}',
    ))
    assert cli.main([
        "work", "create", "--request-file", str(request_file),
        "--idempotency-key-file", str(key_file),
    ]) != 0
    output = capsys.readouterr()
    assert "private-marker" not in output.err + output.out
    assert "wallet_signing_failed" in output.err + output.out
