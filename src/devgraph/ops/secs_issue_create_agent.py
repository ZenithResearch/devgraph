"""Existing Dregg identity → Wallet presentation → secS → exact Issue receiver.

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

from devgraph.ops.secs_issue_create_receiver import (
    IDEMPOTENCY_KEY_FILE_MAX_BYTES,
    REQUEST_FILE_MAX_BYTES,
    _read_private_bounded_file,
    execute_local_secs_issue_create_v1,
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
WALLET_BINARY = Path("CastaliaWallet/bin/castalia-wallet-devgraph-issue-create-v1")
SECS_BINARY = Path("secS/bin/secs-devgraph-issue-create-v1")
SIGNER_ENVIRONMENT = ("DEVGRAPH_SIGNING_KEY_FILE", "DEVGRAPH_SIGNING_PUBLIC_KEY")


class LocalAgentIssueCreateError(RuntimeError):
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
        raise LocalAgentIssueCreateError("the fixed local adapter could not complete") from None


def _snapshot(path: Path, raw: bytes) -> None:
    with path.open("xb") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(raw)


def execute_local_agent_secs_issue_create_v1(
    *,
    request_file: Path,
    idempotency_key_file: Path,
) -> dict[str, Any]:
    """Sign and authorize one exact Issue create with an existing agent identity."""

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
                raise LocalAgentIssueCreateError(
                    "the fixed Wallet signer or secS producer installation is unavailable or unsafe"
                ) from None
            stack.callback(_close_wallet_binary_descriptors, directories, binary)

        request = _read_private_bounded_file(
            request_file,
            label="Issue request",
            maximum_bytes=REQUEST_FILE_MAX_BYTES,
        )
        idempotency = _read_private_bounded_file(
            idempotency_key_file,
            label="idempotency key",
            maximum_bytes=IDEMPOTENCY_KEY_FILE_MAX_BYTES,
        )
        raw_root = stack.enter_context(
            tempfile.TemporaryDirectory(prefix="devgraph-agent-issue-v1-")
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
            raise LocalAgentIssueCreateError(
                "Wallet signing failed; check the existing key-file reference, "
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
            raise LocalAgentIssueCreateError(
                "secS did not authorize the Issue; check its installed identity, "
                "exact resource grant, and validity window"
            )
        return execute_local_secs_issue_create_v1(
            request_file=snapshot_request,
            signed_projection_file=projection,
            idempotency_key_file=snapshot_key,
        )
