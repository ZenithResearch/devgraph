"""Bounded, allowlisted diagnostics from noninteractive native adapters.

Native output is untrusted. Admit at most 512 bytes, using one extra byte to
detect overflow. Never write it to disk; return only one recognized public
error code. No stderr paths or payloads reach the caller or an exception.
"""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import time
from collections.abc import Mapping, Sequence

MAX_DIAGNOSTIC_BYTES = 512
PUBLIC_CODES = frozenset({
    "invalid_work_request", "invalid_arena_request", "request_too_large",
    "invalid_arguments", "invalid_work_input",
    "signing_key_reference_required", "expected_public_key_required",
    "invalid_expected_public_key", "unsafe_signing_key_file",
    "invalid_signing_key_file", "signing_identity_mismatch",
    "unsafe_request_file", "unsafe_input", "unsafe_idempotency_file",
    "invalid_idempotency_file", "unsafe_output", "output_failed",
    "work_permission_denied", "work_authority_expired_before_output",
    "invalid_work_presentation", "invalid_wallet_signature", "replay_conflict",
    "unsafe_work_authority", "invalid_work_manifest", "invalid_work_policy",
    "work_manifest_binding_mismatch", "invalid_secs_registry",
    "untrusted_secs_verifier_key", "unsafe_verifier_key", "invalid_secs_identity",
    "unsafe_replay_database", "replay_storage_failed", "invalid_clock",
})


class NativeAdapterError(RuntimeError):
    """A fixed public diagnostic, never an underlying OS/subprocess exception."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate diagnostic key")
        result[key] = value
    return result


def diagnostic_code(raw: bytes | None) -> str | None:
    if not isinstance(raw, bytes) or len(raw) > MAX_DIAGNOSTIC_BYTES:
        return None
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return None
    if isinstance(value, dict) and set(value) == {"error"}:
        code = value["error"]
        if isinstance(code, str) and code in PUBLIC_CODES:
            return code
    return None


def run_adapter(
    command: Sequence[str], *, env: Mapping[str, str], timeout: float = 30,
) -> subprocess.CompletedProcess[bytes]:
    """Run a fixed noninteractive adapter with time and stderr memory budgets."""
    deadline = time.monotonic() + timeout
    try:
        with subprocess.Popen(
            command, env=dict(env), cwd="/", stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        ) as process:
            try:
                raw = bytearray()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stderr, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise subprocess.TimeoutExpired(command, timeout)
                        chunk = os.read(
                            process.stderr.fileno(), MAX_DIAGNOSTIC_BYTES + 1 - len(raw)
                        )
                        if not chunk:
                            break
                        raw.extend(chunk)
                        if len(raw) > MAX_DIAGNOSTIC_BYTES:
                            raise NativeAdapterError("native_adapter_output_invalid")
                returncode = process.wait(timeout=max(0, deadline - time.monotonic()))
                code = diagnostic_code(bytes(raw)) if returncode else None
                raw.clear()
                return subprocess.CompletedProcess(
                    command, returncode,
                    stderr=json.dumps({"error": code}).encode() if code else None,
                )
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
    except subprocess.TimeoutExpired:
        raise NativeAdapterError("native_adapter_timed_out") from None
    except OSError:
        raise NativeAdapterError("native_adapter_unavailable") from None
