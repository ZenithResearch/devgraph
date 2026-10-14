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

from devgraph.ops.native_adapter_diagnostics import (
    NativeAdapterError,
    diagnostic_code,
    run_adapter,
)
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
        return run_adapter(command, env=env)
    except NativeAdapterError as error:
        raise LocalNamedWorkAgentError(str(error) + ": no Work request was submitted") from None


def _adapter_failure(component: str, stderr: bytes | None) -> str:
    code = diagnostic_code(stderr)
    if code in {"invalid_work_request", "invalid_arena_request", "request_too_large"}:
        return (
            f"{component}_request_incompatible: the installed {component} rejected a request "
            "validated by Devgraph; check matched companion versions and operation support. "
            "A grant renewal or key reset does not repair request compatibility"
        )
    if code in {"invalid_arguments", "invalid_work_input"}:
        return (
            f"{component}_installation_incompatible: check the matched native companion "
            "interface; do not substitute another signer automatically"
        )
    if component == "wallet" and code in {
        "signing_key_reference_required", "expected_public_key_required",
        "invalid_expected_public_key", "unsafe_signing_key_file",
        "invalid_signing_key_file", "signing_identity_mismatch",
    }:
        return (
            "wallet_custody_unavailable: check the existing key-file reference, public-key "
            "pin and private file permissions with devgraph auth status --check"
        )
    if component == "secs" and code in {
        "work_permission_denied", "work_authority_expired_before_output",
        "invalid_work_presentation", "invalid_wallet_signature", "replay_conflict",
    }:
        return (
            "secs_authority_denied: check current operation/resource grants, validity and "
            "presentation bindings; retain the original request and idempotency key"
        )
    if component == "secs" and code in {
        "unsafe_work_authority", "invalid_work_manifest", "invalid_work_policy",
        "work_manifest_binding_mismatch", "invalid_secs_registry",
        "untrusted_secs_verifier_key", "unsafe_verifier_key", "invalid_secs_identity",
        "unsafe_replay_database", "replay_storage_failed", "invalid_clock",
    }:
        return (
            "secs_authority_unavailable: check the installed authority, receiver bindings "
            "and replay storage with devgraph auth work status; do not reset authority"
        )
    if code in {
        "unsafe_request_file", "unsafe_input", "unsafe_idempotency_file",
        "invalid_idempotency_file", "unsafe_output", "output_failed",
    }:
        return (
            f"{component}_private_files_unavailable: the native adapter could not use its "
            "private request/output files; check installation and file permissions"
        )
    return (
        f"{component}_signing_failed: the native adapter did not complete; "
        "no recognized public diagnostic is available. Check companion compatibility "
        "and setup before retrying"
    )


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
                _adapter_failure("wallet", signed.stderr) + "; no Work request was submitted"
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
                _adapter_failure("secs", authorized.stderr) + "; no Work request was submitted"
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
