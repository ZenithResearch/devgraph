"""Explicit terminal migration: Devgraph → secS credential → generic Wallet → secS.

Only Wallet opens the existing signing seed. No application operations, routing
or secS trust are added to Wallet. The legacy installed path remains available
until browser and terminal qualification permit its retirement.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

import httpx

from devgraph.auth.secs_issue_create import _canonical_json
from devgraph.client import DevgraphHttpClient, DevgraphWorkV2Context
from devgraph.credential_requests import credential_request_binding
from devgraph.named_requests import parse_named_request
from devgraph.ops.named_work_agent import INSTALL_ROOT, LocalNamedWorkAgentError, _snapshot
from devgraph.ops.secs_issue_create_receiver import (
    IDEMPOTENCY_KEY_FILE_MAX_BYTES,
    REQUEST_FILE_MAX_BYTES,
    _parse_idempotency_key,
    _read_private_bounded_file,
    _strict_json_object,
)
from devgraph.ops.secs_issue_create_wallet import (
    LocalSecSWalletIssueCreateError,
    _close_wallet_binary_descriptors,
    _require_fixed_executable,
)
from devgraph.ops.signer_profile import signer_environment

WALLET_BINARY = Path("CastaliaWallet/bin/castalia-wallet-present-credential-v2")
SECS_BINARY = Path("secS/bin/secs-devgraph-work-v2")
TRUST_CONFIG = Path("CastaliaWallet/trust/credential-presentation-v2.json")


class LocalCredentialWorkAgentError(LocalNamedWorkAgentError):
    """Redaction-safe failure; no credential or request material in errors."""


def _terminal_available():
    return sys.stdin.isatty() and sys.stderr.isatty()


def _run_adapter(command, *, env, interactive=False):
    try:
        return subprocess.run(
            command,
            check=False,
            env=dict(env),
            cwd="/",
            timeout=130 if interactive else 30,
            stdin=None if interactive else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=None if interactive else subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise LocalCredentialWorkAgentError("credential_adapter_unavailable") from None


def _json_file(path, label, cap=262_144):
    return _strict_json_object(
        _read_private_bounded_file(path, label=label, maximum_bytes=cap), label=label
    )


def execute_local_credential_work(
    *, request_file, idempotency_key_file, operation, request_domain="work", transport=None,
    reconcile=False,
):
    from devgraph.arena_requests import ArenaRequest
    from devgraph.cli import DEFAULT_BASE_URL

    if not _terminal_available():
        raise LocalCredentialWorkAgentError("approval_required")
    if request_domain not in {"work", "arena"}:
        raise LocalCredentialWorkAgentError("unsupported_request_domain")
    raw = _read_private_bounded_file(
        request_file, label="named request", maximum_bytes=REQUEST_FILE_MAX_BYTES
    )
    request = parse_named_request(raw)
    if request.operation != operation or isinstance(request, ArenaRequest) != (
        request_domain == "arena"
    ):
        raise LocalCredentialWorkAgentError("request_operation_mismatch")
    key = _parse_idempotency_key(
        _read_private_bounded_file(
            idempotency_key_file,
            label="idempotency key",
            maximum_bytes=IDEMPOTENCY_KEY_FILE_MAX_BYTES,
        )
    )
    binding = credential_request_binding(request.canonical, key)
    signer = signer_environment()
    if set(signer) != {"DEVGRAPH_SIGNING_KEY_FILE", "DEVGRAPH_SIGNING_PUBLIC_KEY"}:
        raise LocalCredentialWorkAgentError("signer_unavailable")
    # Reuse the existing owner-selected seed reference, not a new identity or recovery format.
    wallet_env = {
        "CASTALIA_SIGNING_KEY_FILE": signer["DEVGRAPH_SIGNING_KEY_FILE"],
        "CASTALIA_SIGNING_PUBLIC_KEY": signer["DEVGRAPH_SIGNING_PUBLIC_KEY"],
    }
    trust_path = INSTALL_ROOT / TRUST_CONFIG
    trust = _json_file(trust_path, "Wallet presentation trust", 65_536)
    if (
        trust.get("schema") != "castalia.wallet-presentation-trust.v1"
        or not isinstance(trust.get("caller"), dict)
        or trust["caller"].get("kind") != "terminal"
    ):
        raise LocalCredentialWorkAgentError("terminal_trust_unavailable")
    caller = trust["caller"]
    with ExitStack() as stack:
        for relative in (WALLET_BINARY, SECS_BINARY):
            path = INSTALL_ROOT / relative
            try:
                directories, binary = _require_fixed_executable(
                    path, expected_path=path, install_root=INSTALL_ROOT, relative_path=relative
                )
            except LocalSecSWalletIssueCreateError:
                raise LocalCredentialWorkAgentError("credential_installation_unavailable") from None
            stack.callback(_close_wallet_binary_descriptors, directories, binary)
        directory = Path(
            stack.enter_context(tempfile.TemporaryDirectory(prefix="devgraph-work-v2-"))
        )
        directory.chmod(0o700)
        preflight, keyfile = directory / "preflight.json", directory / "key.txt"
        prepared, presentation = (
            directory / "presentation-request.json",
            directory / "presentation.json",
        )
        authorization, projection = directory / "authorization.json", directory / "projection.json"
        _snapshot(keyfile, (key + "\n").encode())
        _snapshot(
            preflight,
            _canonical_json(
                {
                    "schema": "secs-devgraph-credential-input.v2",
                    "schema_version": 2,
                    "request": json.loads(request.canonical),
                    "holder_public_key": wallet_env["CASTALIA_SIGNING_PUBLIC_KEY"],
                    "caller": caller,
                }
            ),
        )
        issued = _run_adapter(
            [
                str(INSTALL_ROOT / SECS_BINARY),
                "issue-credential",
                "--request-file",
                str(preflight),
                "--idempotency-key-file",
                str(keyfile),
                "--presentation-request-output",
                str(prepared),
            ],
            env={},
        )
        if issued.returncode != 0:
            raise LocalCredentialWorkAgentError("credential_issuance_denied")
        presentation_request = _json_file(prepared, "credential presentation request")
        if (
            set(presentation_request)
            != {"schema", "request_bytes_base64", "credential", "disclosure"}
            or presentation_request["schema"] != "castalia.credential-presentation-request.v2"
            or presentation_request["request_bytes_base64"]
            != base64.b64encode(binding.request_bytes).decode()
            or presentation_request["disclosure"] != binding.disclosure
        ):
            raise LocalCredentialWorkAgentError("credential_binding_mismatch")
        approved = _run_adapter(
            [
                str(INSTALL_ROOT / WALLET_BINARY),
                "--request-file",
                str(prepared),
                "--trust-config-file",
                str(trust_path),
                "--presentation-output",
                str(presentation),
            ],
            env=wallet_env,
            interactive=True,
        )
        if approved.returncode != 0:
            raise LocalCredentialWorkAgentError("wallet_approval_denied")
        signed = _json_file(presentation, "generic Wallet presentation", 16_384)
        _snapshot(
            authorization,
            _canonical_json(
                {
                    "schema": "secs-devgraph-work-producer-input.v2",
                    "schema_version": 2,
                    "request": json.loads(request.canonical),
                    "credential": presentation_request["credential"],
                    "disclosure": binding.disclosure,
                    "presentation": signed,
                }
            ),
        )
        authorized = _run_adapter(
            [
                str(INSTALL_ROOT / SECS_BINARY),
                "authorize",
                "--request-file",
                str(authorization),
                "--idempotency-key-file",
                str(keyfile),
                "--signed-projection-output",
                str(projection),
            ],
            env={},
        )
        if authorized.returncode != 0:
            raise LocalCredentialWorkAgentError("current_authority_denied")
        proof = _read_private_bounded_file(projection, label="v2 authority", maximum_bytes=16_384)
        active = transport or httpx.Client(trust_env=False, follow_redirects=False)
        try:
            client = DevgraphHttpClient(transport=active, base_url=DEFAULT_BASE_URL, timeout=10.0)
            execute = (
                client.reconcile_credential_work if reconcile else
                client.execute_arena if request_domain == "arena" else client.execute_named_work
            )
            try:
                return execute(
                    DevgraphWorkV2Context(projection_json=proof),
                    request_json=request.canonical,
                    idempotency_key=key,
                ).model_dump(mode="json", exclude_none=reconcile)
            except Exception:
                # Once submission starts, never assert nonexecution or automatically
                # repeat a mutation. Keep the user's immutable request/key files.
                raise LocalCredentialWorkAgentError(
                    "status_unavailable: retry --credential-v2 --reconcile with the original "
                    "request and idempotency key" if reconcile else
                    "outcome_unknown: use --credential-v2 --reconcile with the original "
                    "request and idempotency key"
                ) from None
        finally:
            if transport is None:
                active.close()
