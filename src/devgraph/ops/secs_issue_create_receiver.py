"""Fixed local CLI receiver for ``devgraph.issue.create.v1``.

This composition layer deliberately has no transport, generic authority,
caller-selected receiver policy, or caller-selected database connection. It
loads one owner-controlled local receiver bundle, constructs the configured
loopback Neo4j storage, and delegates the full verification and mutation to
the exact secS Issue-create adapter.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devgraph.auth.enforcement import AuditLog
from devgraph.auth.secs_issue_create import (
    DEVGRAPH_ISSUE_CREATE_OPERATION_V1,
    SecSIssueCreateAdapter,
    SecSIssueCreatePolicyBinding,
    SecSIssueCreatePolicyRegistry,
    SecSIssueCreateVerifier,
    SecSIssueCreateVerifierConfig,
    SecSVerifierKeyRegistry,
)
from devgraph.local_host import (
    DEFAULT_CONFIG_PATH,
    MIGRATION_MANIFEST_PATH,
    load_local_config,
)
from devgraph.model.validation import validate_work_object_id
from devgraph.ops.local_path_integrity import (
    LocalPathIntegrityError,
    require_file_descriptor_without_acl,
    require_receiver_directory_path,
)
from devgraph.ops.migrate import ManifestError, load_manifest, readiness_status
from devgraph.storage.neo4j import Neo4jConfig, Neo4jGraphStorage, Neo4jMigrationStore

RECEIVER_BUNDLE_RELATIVE_PATH = Path(
    "secrets/secs-magik/devgraph.issue.create.v1"
)
RECEIVER_MANIFEST_NAME = "receiver.json"
SECS_PUBLIC_KEY_REGISTRY_NAME = "secs-public-key-registry.json"
RECEIVER_MANIFEST_SCHEMA = "devgraph-secs-issue-create-receiver.v1"
RECEIVER_MANIFEST_MAX_BYTES = 16_384
SECS_PUBLIC_KEY_REGISTRY_MAX_BYTES = 262_144
REQUEST_FILE_MAX_BYTES = 131_072
SIGNED_PROJECTION_FILE_MAX_BYTES = 16_384
IDEMPOTENCY_KEY_FILE_MAX_BYTES = 129
NEO4J_PASSWORD_FILE_MAX_BYTES = 4_096
LOCAL_NEO4J_URI = "bolt://127.0.0.1:7687"
LOCAL_NEO4J_USER = "neo4j"
LOCAL_NEO4J_DATABASE = "neo4j"

_RECEIVER_MANIFEST_FIELDS = frozenset(
    {
        "audience",
        "operation",
        "policy_binding",
        "schema",
        "schema_version",
        "stable_issuer",
    }
)
_POLICY_BINDING_FIELDS = frozenset(
    {"policy_digest_sha256", "policy_id", "policy_version"}
)
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._~-]{16,128}$", re.ASCII)
_CORRELATION_ID = re.compile(r"^dg:sha256:[0-9a-f]{64}$", re.ASCII)


class LocalSecSIssueCreateError(RuntimeError):
    """Redaction-safe local receiver configuration or file failure."""


@dataclass(frozen=True)
class _ReceiverManifest:
    audience: str
    stable_issuer: str
    policy_binding: SecSIssueCreatePolicyBinding


def _read_private_bounded_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> bytes:
    """Read once through an owner-only, no-follow regular-file descriptor."""

    if not isinstance(path, Path) or not isinstance(label, str) or not label:
        raise TypeError("invalid private file request")
    if type(maximum_bytes) is not int or maximum_bytes < 1:
        raise TypeError("invalid private file size limit")
    # Nonblocking open is inert for regular files and prevents an owner-made
    # FIFO or device path from stalling before fstat can reject it.
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, OSError) as error:
        raise LocalSecSIssueCreateError(f"{label} is not an available regular file") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise LocalSecSIssueCreateError(f"{label} is not an available regular file")
        if info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise LocalSecSIssueCreateError(f"{label} is not owned by the current user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise LocalSecSIssueCreateError(f"{label} permissions are too broad")
        try:
            require_file_descriptor_without_acl(descriptor)
        except LocalPathIntegrityError:
            raise LocalSecSIssueCreateError(f"{label} has an extended ACL") from None
        if info.st_size > maximum_bytes:
            raise LocalSecSIssueCreateError(f"{label} exceeds its size limit")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(maximum_bytes + 1)
        if len(raw) > maximum_bytes:
            raise LocalSecSIssueCreateError(f"{label} exceeds its size limit")
        return raw
    finally:
        os.close(descriptor)


def _strict_json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    def reject_number(_value: str) -> Any:
        raise ValueError("non-integer JSON number")

    def object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    try:
        text = raw.decode("utf-8", errors="strict")
        if text.startswith("\ufeff"):
            raise ValueError("BOM")
        value = json.loads(
            text,
            object_pairs_hook=object_from_pairs,
            parse_float=reject_number,
            parse_constant=reject_number,
        )
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise LocalSecSIssueCreateError(f"{label} is malformed") from None
    if not isinstance(value, dict):
        raise LocalSecSIssueCreateError(f"{label} is malformed")
    return value


def _parse_receiver_manifest(raw: bytes) -> _ReceiverManifest:
    value = _strict_json_object(raw, label="secS receiver manifest")
    if (
        set(value) != _RECEIVER_MANIFEST_FIELDS
        or value.get("schema") != RECEIVER_MANIFEST_SCHEMA
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("operation") != DEVGRAPH_ISSUE_CREATE_OPERATION_V1
        or not isinstance(value.get("policy_binding"), dict)
        or set(value["policy_binding"]) != _POLICY_BINDING_FIELDS
    ):
        raise LocalSecSIssueCreateError("secS receiver manifest is malformed")
    binding = value["policy_binding"]
    try:
        policy_binding = SecSIssueCreatePolicyBinding(
            policy_id=binding["policy_id"],
            policy_version=binding["policy_version"],
            policy_digest_sha256=binding["policy_digest_sha256"],
        )
    except (KeyError, TypeError, ValueError):
        raise LocalSecSIssueCreateError("secS receiver manifest is malformed") from None
    return _ReceiverManifest(
        audience=value["audience"],
        stable_issuer=value["stable_issuer"],
        policy_binding=policy_binding,
    )


def _parse_idempotency_key(raw: bytes) -> str:
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if not raw or b"\n" in raw or b"\r" in raw:
        raise LocalSecSIssueCreateError("idempotency key file is malformed")
    try:
        value = raw.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        raise LocalSecSIssueCreateError("idempotency key file is malformed") from None
    if _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise LocalSecSIssueCreateError("idempotency key file is malformed")
    return value


def _parse_neo4j_password(raw: bytes) -> str:
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if not raw or b"\n" in raw or b"\r" in raw or b"\x00" in raw:
        raise LocalSecSIssueCreateError("local Neo4j credential is malformed")
    try:
        password = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise LocalSecSIssueCreateError("local Neo4j credential is malformed") from None
    if not password or password != password.strip():
        raise LocalSecSIssueCreateError("local Neo4j credential is malformed")
    return password


def _safe_result(result: Any, audit_log: AuditLog) -> dict[str, Any]:
    try:
        issue = result.issue
        receipt = result.receipt
        receipt_id = validate_work_object_id(receipt.id)
        subject_id = validate_work_object_id(receipt.subject_id)
        issue_id = None if issue is None else validate_work_object_id(issue.id)
        correlation_id = receipt.correlation_id
    except (AttributeError, TypeError, ValueError):
        raise LocalSecSIssueCreateError(
            "exact Issue-create result is malformed"
        ) from None
    if (
        receipt.operation != DEVGRAPH_ISSUE_CREATE_OPERATION_V1
        or receipt.subject_label != "Issue"
        or not isinstance(correlation_id, str)
        or _CORRELATION_ID.fullmatch(correlation_id) is None
        or (issue_id is not None and issue_id != subject_id)
        or not isinstance(result.duplicate, bool)
        or result.duplicate != (issue is None)
        or len(audit_log.records) != 1
    ):
        raise LocalSecSIssueCreateError("exact Issue-create result is malformed")
    return {
        "audit": {"published_records": len(audit_log.records)},
        "duplicate": result.duplicate,
        "issue": (
            None
            if issue is None
            else {
                "id": issue_id,
                "kind": issue.kind,
                "status": issue.status.value,
                "version": issue.version,
            }
        ),
        "operation": DEVGRAPH_ISSUE_CREATE_OPERATION_V1,
        "receipt": {
            "correlation_id": correlation_id,
            "id": receipt_id,
            "status": receipt.status.value,
            "subject_id": subject_id,
            "subject_label": receipt.subject_label,
        },
    }


def _require_canonical_readiness(storage: Neo4jGraphStorage) -> None:
    try:
        status = readiness_status(
            load_manifest(MIGRATION_MANIFEST_PATH),
            Neo4jMigrationStore(storage),
            storage,
        )
    except ManifestError:
        raise LocalSecSIssueCreateError(
            "canonical migration manifest is invalid"
        ) from None
    if not status.ready:
        raise LocalSecSIssueCreateError(
            f"canonical local Devgraph is not ready: {status.reason}"
        )


def execute_local_secs_issue_create_v1(
    *,
    request_file: Path,
    signed_projection_file: Path,
    idempotency_key_file: Path,
) -> dict[str, Any]:
    """Execute exactly one verified local ``devgraph.issue.create.v1`` call."""

    request_json = _read_private_bounded_file(
        request_file,
        label="Issue-create request file",
        maximum_bytes=REQUEST_FILE_MAX_BYTES,
    )
    signed_projection_json = _read_private_bounded_file(
        signed_projection_file,
        label="signed secS projection file",
        maximum_bytes=SIGNED_PROJECTION_FILE_MAX_BYTES,
    )
    idempotency_key = _parse_idempotency_key(
        _read_private_bounded_file(
            idempotency_key_file,
            label="idempotency key file",
            maximum_bytes=IDEMPOTENCY_KEY_FILE_MAX_BYTES,
        )
    )

    config = load_local_config(DEFAULT_CONFIG_PATH)
    assert config is not None
    try:
        bundle = require_receiver_directory_path(
            config.data_root,
            RECEIVER_BUNDLE_RELATIVE_PATH,
            missing_ok=False,
        )
    except LocalPathIntegrityError as error:
        raise LocalSecSIssueCreateError(str(error)) from None
    assert bundle is not None
    manifest = _parse_receiver_manifest(
        _read_private_bounded_file(
            bundle / RECEIVER_MANIFEST_NAME,
            label="secS receiver manifest",
            maximum_bytes=RECEIVER_MANIFEST_MAX_BYTES,
        )
    )
    try:
        key_registry = SecSVerifierKeyRegistry.from_json(
            _read_private_bounded_file(
                bundle / SECS_PUBLIC_KEY_REGISTRY_NAME,
                label="secS public verifier key registry",
                maximum_bytes=SECS_PUBLIC_KEY_REGISTRY_MAX_BYTES,
            )
        )
        verifier = SecSIssueCreateVerifier(
            SecSIssueCreateVerifierConfig(
                audience=manifest.audience,
                stable_issuer=manifest.stable_issuer,
                policy_registry=SecSIssueCreatePolicyRegistry(
                    [manifest.policy_binding]
                ),
                key_registry=key_registry,
                clock=lambda: int(time.time()),
            )
        )
    except (TypeError, ValueError):
        raise LocalSecSIssueCreateError(
            "secS receiver trust configuration is malformed"
        ) from None
    password = _parse_neo4j_password(
        _read_private_bounded_file(
            config.data_root / "secrets" / "neo4j_password",
            label="local Neo4j credential",
            maximum_bytes=NEO4J_PASSWORD_FILE_MAX_BYTES,
        )
    )
    storage = Neo4jGraphStorage(
        Neo4jConfig(
            uri=LOCAL_NEO4J_URI,
            user=LOCAL_NEO4J_USER,
            password=password,
            database=LOCAL_NEO4J_DATABASE,
        )
    )
    audit_log = AuditLog()
    try:
        _require_canonical_readiness(storage)
        result = SecSIssueCreateAdapter(
            verifier=verifier,
            storage=storage,
            audit_log=audit_log,
        ).execute(
            request_json=request_json,
            idempotency_key=idempotency_key,
            signed_projection_json=signed_projection_json,
        )
        return _safe_result(result, audit_log)
    finally:
        close = getattr(storage, "close", None)
        if callable(close):
            close()
