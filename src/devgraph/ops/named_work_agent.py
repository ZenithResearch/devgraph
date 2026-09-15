"""Existing Dregg identity → Wallet presentation → secS → named Work HTTP receiver.

Only the Wallet child receives a key-file reference. Devgraph never opens or
copies that key; secS continues to apply its own installed policy and service
identity. This is a separate owner-invoked headless path, not browser approval.
"""

from __future__ import annotations

import os
import pwd
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx

from devgraph.ops.secs_issue_create_receiver import (
    IDEMPOTENCY_KEY_FILE_MAX_BYTES,
    REQUEST_FILE_MAX_BYTES,
    _parse_idempotency_key,
    _read_private_bounded_file,
)
from devgraph.ops.secs_issue_create_wallet import (
    LocalSecSWalletIssueCreateError,
    _close_wallet_binary_descriptors,
    _require_fixed_executable,
)
from devgraph.ops.signer_profile import signer_environment

INSTALL_ROOT = (
    Path(pwd.getpwuid(os.geteuid()).pw_dir) / "Library" / "Application Support" / "Zenith"
)
WALLET_BINARY = Path("CastaliaWallet/bin/castalia-wallet-devgraph-work-v1")
SECS_BINARY = Path("secS/bin/secs-devgraph-work-v1")
SIGNER_ENVIRONMENT = ("DEVGRAPH_SIGNING_KEY_FILE", "DEVGRAPH_SIGNING_PUBLIC_KEY")


class LocalNamedWorkAgentError(RuntimeError):
    """Redaction-safe headless composition failure."""


def _run_adapter(
    command: Sequence[str], *, env: Mapping[str, str]
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            check=False,
            env=dict(env),
            cwd="/",
            timeout=30,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise LocalNamedWorkAgentError("the fixed local adapter could not complete") from None


def _snapshot(path: Path, raw: bytes) -> None:
    with path.open("xb") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(raw)


def execute_local_named_work(
    *,
    request_file: Path,
    idempotency_key_file: Path,
    operation: str,
    request_domain: str = "work",
    transport=None,
) -> dict[str, Any]:
    """Sign one closed command and submit it over the fixed private HTTP service."""
    from devgraph.arena_requests import ArenaRequest
    from devgraph.cli import DEFAULT_BASE_URL
    from devgraph.client.http import DevgraphHttpClient, DevgraphWorkContext
    from devgraph.work_requests import WorkRequest

    if request_domain not in ("work", "arena"):
        raise LocalNamedWorkAgentError("unsupported signed request domain")

    with ExitStack() as stack:
        for relative in (WALLET_BINARY, SECS_BINARY):
            path = INSTALL_ROOT / relative
            try:
                directories, binary = _require_fixed_executable(
                    path,
                    expected_path=path,
                    install_root=INSTALL_ROOT,
                    relative_path=relative,
                )
            except LocalSecSWalletIssueCreateError:
                raise LocalNamedWorkAgentError(
                    "the fixed Wallet signer or secS producer installation is unavailable or unsafe"
                ) from None
            stack.callback(_close_wallet_binary_descriptors, directories, binary)

        request = _read_private_bounded_file(
            request_file,
            label="named Work request",
            maximum_bytes=REQUEST_FILE_MAX_BYTES,
        )
        parsed = (ArenaRequest if request_domain == "arena" else WorkRequest).from_json(request)
        if parsed.operation != operation:
            raise LocalNamedWorkAgentError("command and signed request operation differ")
        request = parsed.canonical
        idempotency = _read_private_bounded_file(
            idempotency_key_file,
            label="idempotency key",
            maximum_bytes=IDEMPOTENCY_KEY_FILE_MAX_BYTES,
        )
        idempotency = (_parse_idempotency_key(idempotency) + "\n").encode("ascii")
        raw_root = stack.enter_context(
            tempfile.TemporaryDirectory(prefix="devgraph-agent-work-v1-")
        )
        root = Path(raw_root).resolve()
        root.chmod(0o700)
        snapshot_request = root / "request.json"
        snapshot_key = root / "idempotency-key.txt"
        envelope = root / "producer-input.json"
        projection = root / "signed-projection.json"
        _snapshot(snapshot_request, request)
        _snapshot(snapshot_key, idempotency)

        # No inherited loader, proxy, shell, unrelated secret, or authority env.
        signer_env = signer_environment()
        signed = _run_adapter(
            [
                str(INSTALL_ROOT / WALLET_BINARY),
                "--request-file",
                str(snapshot_request),
                "--idempotency-key-file",
                str(snapshot_key),
                "--producer-input-output",
                str(envelope),
            ],
            env=signer_env,
        )
        if signed.returncode != 0:
            raise LocalNamedWorkAgentError(
                "Wallet signing failed; run devgraph auth setup or check the key-file reference, "
                "public-key pin, and private file permissions"
            )
        authorized = _run_adapter(
            [
                str(INSTALL_ROOT / SECS_BINARY),
                "--request-file",
                str(envelope),
                "--idempotency-key-file",
                str(snapshot_key),
                "--signed-projection-output",
                str(projection),
            ],
            env={},
        )
        if authorized.returncode != 0:
            raise LocalNamedWorkAgentError(
                "secS did not authorize the Work request; check its installed identity, "
                "operation and resource grants, and validity window"
            )
        proof = _read_private_bounded_file(
            projection, label="signed Work authority", maximum_bytes=16_384
        )
        owns_transport = transport is None
        active = transport or httpx.Client(trust_env=False, follow_redirects=False)
        try:
            client = DevgraphHttpClient(transport=active, base_url=DEFAULT_BASE_URL, timeout=10.0)
            execute = (client.execute_arena if request_domain == "arena"
                       else client.execute_named_work)
            return execute(
                DevgraphWorkContext(projection_json=proof),
                request_json=request,
                idempotency_key=_parse_idempotency_key(idempotency),
            ).model_dump(mode="json")
        finally:
            if owns_transport:
                active.close()
