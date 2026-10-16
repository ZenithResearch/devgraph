"""Process composition tests; cryptographic parity lives in shared authority fixtures."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.api.test_app_scaffold import build_services
from tests.auth.test_secs_work import KEY, SEED, b64, canonical, request
from tests.auth.test_secs_work_v2 import proof_v2, services_v2
from tests.ops.test_named_work_v2_receiver import fixture as receiver_fixture

import devgraph.cli as cli
import devgraph.ops.credential_work_agent as agent
from devgraph.api import create_app
from devgraph.auth.secs_work_v2 import SIGNATURE_DOMAIN
from devgraph.credential_requests import credential_request_binding, wallet_presentation_request
from devgraph.named_requests import parse_named_request


def private(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


def fixture(monkeypatch, tmp_path):
    root = tmp_path / "Zenith"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(agent, "INSTALL_ROOT", root)
    monkeypatch.setattr(agent, "_terminal_available", lambda: True)
    monkeypatch.setattr(
        agent,
        "signer_environment",
        lambda: {
            "DEVGRAPH_SIGNING_KEY_FILE": "/private/existing.key",
            "DEVGRAPH_SIGNING_PUBLIC_KEY": "ab" * 32,
        },
    )
    for relative in [agent.WALLET_BINARY, agent.SECS_BINARY]:
        private(root / relative, b"fixture").chmod(0o700)
    private(
        root / agent.TRUST_CONFIG,
        canonical(
            {
                "schema": "castalia.wallet-presentation-trust.v1",
                "caller": {"kind": "terminal", "id": "devgraph:terminal"},
                "pins": [],
            }
        ),
    )
    return private(tmp_path / "request.json", request()), private(
        tmp_path / "key.txt", KEY.encode()
    )


def test_terminal_unavailable_fails_before_reading_keys_or_issuing_credentials(monkeypatch):
    monkeypatch.setattr(agent, "_terminal_available", lambda: False)
    monkeypatch.setattr(agent, "signer_environment", lambda: pytest.fail("read signer"))
    with pytest.raises(agent.LocalCredentialWorkAgentError, match="approval_required"):
        agent.execute_local_credential_work(
            request_file=Path("missing"), idempotency_key_file=Path("missing"), operation="create"
        )


def test_exact_snapshots_run_issuer_wallet_authority_in_order(monkeypatch, tmp_path):
    request_file, key_file = fixture(monkeypatch, tmp_path)
    calls = []

    def run(command, *, env, interactive=False):
        calls.append(command)
        if len(calls) == 1:
            assert command[1] == "issue-credential"
            assert env == {} and not interactive
            source = json.loads(Path(command[3]).read_bytes())
            assert source["holder_public_key"] == "ab" * 32
            private(Path(command[-1]), canonical(wallet_presentation_request(request(), KEY, {})))
            request_file.write_bytes(b"mutated")
            key_file.write_bytes(b"mutated")
        elif len(calls) == 2:
            assert command[1] == "--request-file" and interactive
            assert set(env) == {"CASTALIA_SIGNING_KEY_FILE", "CASTALIA_SIGNING_PUBLIC_KEY"}
            assert env["CASTALIA_SIGNING_KEY_FILE"] == "/private/existing.key"
            private(Path(command[-1]), canonical({"schema": "castalia.credential-presentation.v2"}))
        else:
            assert command[1] == "authorize" and env == {} and not interactive
            source = json.loads(Path(command[3]).read_bytes())
            assert source["request"]["id"] == "test-one"
            private(Path(command[-1]), proof_v2(request()))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(agent, "_run_adapter", run)
    services, _ = services_v2()
    result = agent.execute_local_credential_work(
        request_file=request_file,
        idempotency_key_file=key_file,
        operation="create",
        transport=TestClient(create_app(services)),
    )
    assert result["work"]["id"] == "test-one"
    assert len(calls) == 3
    assert not Path(calls[0][3]).parent.exists()


@pytest.mark.parametrize("failure", [1, 2, 3])
def test_every_denial_stops_before_dispatch(monkeypatch, tmp_path, failure):
    request_file, key_file = fixture(monkeypatch, tmp_path)
    calls = []

    def run(command, *, env, interactive=False):
        calls.append(command)
        if len(calls) == failure:
            return subprocess.CompletedProcess(command, 2)
        value = (
            wallet_presentation_request(request(), KEY, {})
            if len(calls) == 1
            else {"schema": "castalia.credential-presentation.v2"}
        )
        private(Path(command[-1]), canonical(value))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(agent, "_run_adapter", run)

    class NoTransport:
        def request(self, *args, **kwargs):
            pytest.fail("denial reached transport")

    with pytest.raises(agent.LocalCredentialWorkAgentError):
        agent.execute_local_credential_work(
            request_file=request_file,
            idempotency_key_file=key_file,
            operation="create",
            transport=NoTransport(),
        )
    assert len(calls) == failure


def test_explicit_cli_flag_does_not_change_default_or_offer_autoapproval(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        cli, "execute_local_credential_work", lambda **args: calls.append(args) or {}
    )
    assert (
        cli.main(
            [
                "work",
                "create",
                "--request-file",
                str(tmp_path / "r"),
                "--idempotency-key-file",
                str(tmp_path / "k"),
                "--credential-v2",
            ]
        )
        == 0
    )
    assert calls[0]["operation"] == "create"
    with pytest.raises(SystemExit):
        cli._parser().parse_args(
            [
                "work",
                "create",
                "--request-file",
                "r",
                "--idempotency-key-file",
                "k",
                "--credential-v2",
                "--approve",
            ]
        )


def signed_proof(raw, *, nonce="44" * 16):
    parsed = parse_named_request(raw)
    binding = credential_request_binding(raw, KEY)
    value = json.loads(proof_v2(request()))
    value.pop("secs_verifier_signature")
    value.update(
        nonce=nonce, operation=parsed.authority_operation, resources=list(parsed.resources),
        request_digest_sha256=parsed.digest,
        credential_request_digest_sha256=binding.request_digest_sha256,
        disclosure_digest_sha256=binding.disclosure_digest_sha256,
    )
    value["secs_verifier_signature"] = b64(SEED.sign(SIGNATURE_DOMAIN + canonical(value)))
    return canonical(value)


@pytest.mark.parametrize("domain", ["work", "arena"])
@pytest.mark.parametrize("committed", [False, True])
def test_lost_response_then_fresh_approval_reconciles_without_another_mutation(
    monkeypatch, tmp_path, domain, committed
):
    request_file, key_file = fixture(monkeypatch, tmp_path)
    raw = request() if domain == "work" else canonical({
        "schema": "devgraph.arena-request.v1", "operation": "create", "kind": "Arena",
        "id": "gallery", "expected_version": None,
        "payload": {"id": "gallery", "title": "Gallery"},
    })
    request_file.write_bytes(raw)
    (tmp_path / "receiver").mkdir(mode=0o700)
    receiver, storage, _, _, _ = receiver_fixture(tmp_path / "receiver")
    services = replace(build_services(frozenset()), storage=storage, named_work_v2=receiver)
    server = TestClient(create_app(services))
    adapter_calls, transport_calls = [], []

    def run(command, *, env, interactive=False):
        adapter_calls.append(command)
        if command[1] == "issue-credential":
            value = wallet_presentation_request(raw, KEY, {})
        elif command[1] == "authorize":
            private(Path(command[-1]), signed_proof(raw, nonce=f"{len(adapter_calls):02x}" * 16))
            return subprocess.CompletedProcess(command, 0)
        else:
            assert interactive
            value = {"schema": "castalia.credential-presentation.v2"}
        private(Path(command[-1]), canonical(value))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(agent, "_run_adapter", run)

    class LostResponse:
        def request(self, method, url, **kwargs):
            transport_calls.append(url)
            if len(transport_calls) == 1 and not committed:
                raise TimeoutError("synthetic transport loss before delivery")
            response = server.request(method, url, **kwargs)
            assert response.status_code in {200, 201}, response.text
            if len(transport_calls) == 1:
                raise TimeoutError("synthetic lost response after commit")
            return response

    args = dict(request_file=request_file, idempotency_key_file=key_file,
                operation="create", request_domain=domain, transport=LostResponse())
    with pytest.raises(agent.LocalCredentialWorkAgentError, match="outcome_unknown"):
        agent.execute_local_credential_work(**args)
    assert len(receiver.audit_log.records) == int(committed)
    result = agent.execute_local_credential_work(**args, reconcile=True)
    if committed:
        assert result["state"] == "committed" and result["receipt"]["duplicate"]
    else:
        assert result == {"state": "unknown"}
    assert len(receiver.audit_log.records) == int(committed)
    assert len(adapter_calls) == 6
    assert transport_calls[0].endswith(f"/{domain}-operations/v2")
    assert transport_calls[1].endswith(f"/{domain}-operations/v2/status")
    assert len(storage.query("Issue" if domain == "work" else "Arena")) == int(committed)


@pytest.mark.parametrize("domain", ["work", "arena"])
def test_reconcile_flag_is_explicit_v2_only(monkeypatch, tmp_path, domain):
    calls = []
    monkeypatch.setattr(
        cli, "execute_local_credential_work", lambda **args: calls.append(args) or {}
    )
    monkeypatch.setattr(
        cli, "execute_local_named_work", lambda **args: pytest.fail("legacy invoked")
    )
    command = [domain, "create", "--request-file", str(tmp_path / "r"),
               "--idempotency-key-file", str(tmp_path / "k"), "--reconcile"]
    assert cli.main(command) == 2
    assert calls == []
    assert cli.main(command + ["--credential-v2"]) == 0
    assert calls[0]["reconcile"] is True
