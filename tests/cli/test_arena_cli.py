import json
import subprocess
from pathlib import Path

import pytest
from tests.api.test_arenas import arena_proof, create_request
from tests.auth.test_secs_work import client_and_services
from tests.cli.test_named_work_cli import private, setup

import devgraph.cli as cli
import devgraph.ops.named_work_agent as agent
from devgraph.arena_requests import ARENA_OPERATIONS, InvalidArenaRequest


def test_arena_cli_uses_fixed_signer_and_secs_then_strict_arena_client(monkeypatch, tmp_path):
    request_file, key_file = setup(monkeypatch, tmp_path)
    raw = create_request()
    request_file.write_bytes(raw)
    calls = []
    monkeypatch.setattr(agent, "signer_environment", lambda: {
        "DEVGRAPH_SIGNING_KEY_FILE": "/private/fixture.key",
        "DEVGRAPH_SIGNING_PUBLIC_KEY": "a" * 64,
    })

    def run(command, *, env):
        calls.append(command)
        if len(calls) == 1:
            assert set(env) == set(agent.SIGNER_ENVIRONMENT)
            assert Path(command[2]).read_bytes() == raw
            request_file.write_bytes(b"changed after signing")
            private(Path(command[-1]), b"wallet-envelope")
        else:
            assert env == {}
            private(Path(command[-1]), arena_proof(raw))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(agent, "_run_adapter", run)
    transport, services, _ = client_and_services()
    result = agent.execute_local_named_work(request_file=request_file,
        idempotency_key_file=key_file, operation="create", request_domain="arena",
        transport=transport)
    assert result["arena"]["id"] == "gallery"
    assert result["receipt"]["operation"] == "devgraph.arena.create.v1"
    assert result["work"] is None and len(services.storage.query("Arena")) == 1
    assert len(calls) == 2 and not Path(calls[0][2]).parent.exists()


@pytest.mark.parametrize("operation", ARENA_OPERATIONS)
def test_arena_commands_do_not_offer_authority_overrides(monkeypatch, tmp_path, capsys, operation):
    seen = []
    monkeypatch.setattr(cli, "execute_local_named_work",
                        lambda **kw: seen.append(kw) or {"ok": True})
    args = ["arena", operation, "--request-file", str(tmp_path / "request"),
            "--idempotency-key-file", str(tmp_path / "key")]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}
    assert seen[0]["request_domain"] == "arena" and seen[0]["operation"] == operation
    for flag in ("--url", "--policy", "--wallet-binary", "--signing-key", "--bearer"):
        with pytest.raises(SystemExit):
            cli._parser().parse_args([*args, flag, "untrusted"])


def test_arena_command_rejects_work_request_before_signing(monkeypatch, tmp_path):
    request_file, key_file = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(agent, "_run_adapter", lambda *a, **kw: pytest.fail("Work request signed"))
    with pytest.raises(InvalidArenaRequest):
        agent.execute_local_named_work(request_file=request_file, idempotency_key_file=key_file,
                                      operation="create", request_domain="arena")
