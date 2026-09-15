"""Owner-provisioned read-only credential verifier for a private local host.

The credential is an opaque random bearer value.  Its receiver-owned registry
stores only a SHA-256 digest plus fixed authority claims; it can never grant a
scope other than ``devgraph.read``.  This is a local-host access capability,
not a canonical identity credential or a replacement for secS issuance.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.scopes import SCOPE_READ
from devgraph.model.base import utc_now
from devgraph.ops.local_path_integrity import (
    LocalPathIntegrityError,
    require_file_descriptor_without_acl,
    require_receiver_directory_path,
)

AUTH_MODE_LOCAL_READ = "local-read"
LOCAL_READ_REGISTRY_RELATIVE_PATH = Path(
    "secrets/devgraph.read.v1/credential-registry.json"
)
LOCAL_READ_REGISTRY_SCHEMA_V1 = "devgraph-local-read-credential-registry.v1"
LOCAL_READ_CREDENTIAL_PREFIX_V1 = "dgread1_"
LOCAL_READ_MAX_REGISTRY_BYTES = 4_096
LOCAL_READ_MAX_LIFETIME_SECONDS = 31 * 24 * 60 * 60

_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_TOKEN = re.compile(r"^dgread1_[A-Za-z0-9_-]{43}$", re.ASCII)
_FIELDS = frozenset(
    {
        "actor_id",
        "audience",
        "correlation_id",
        "credential_digest_sha256",
        "expires_at",
        "issued_at",
        "issuer",
        "schema",
        "schema_version",
        "session_id",
    }
)


class LocalReadCredentialConfigurationError(RuntimeError):
    """Redaction-safe invalid local read-credential configuration."""


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_registry(data_root: Path) -> dict[str, Any]:
    try:
        directory = require_receiver_directory_path(
            data_root,
            LOCAL_READ_REGISTRY_RELATIVE_PATH.parent,
            missing_ok=False,
        )
        if directory is None:
            raise LocalReadCredentialConfigurationError(
                "local read credential registry is unavailable"
            )
        path = directory / LOCAL_READ_REGISTRY_RELATIVE_PATH.name
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_nlink != 1
            ):
                raise LocalReadCredentialConfigurationError(
                    "local read credential registry is unsafe"
                )
            require_file_descriptor_without_acl(descriptor)
            raw = os.read(descriptor, LOCAL_READ_MAX_REGISTRY_BYTES + 1)
        finally:
            os.close(descriptor)
    except (
        FileNotFoundError,
        LocalPathIntegrityError,
        OSError,
        ValueError,
    ):
        raise LocalReadCredentialConfigurationError(
            "local read credential registry is unavailable"
        ) from None
    if not raw or len(raw) > LOCAL_READ_MAX_REGISTRY_BYTES:
        raise LocalReadCredentialConfigurationError(
            "local read credential registry is malformed"
        )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LocalReadCredentialConfigurationError(
            "local read credential registry is malformed"
        ) from None
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise LocalReadCredentialConfigurationError(
            "local read credential registry is malformed"
        )
    if raw != _canonical_json(value):
        raise LocalReadCredentialConfigurationError(
            "local read credential registry is non-canonical"
        )
    return value


def _validated_envelope(
    registry: dict[str, Any],
    *,
    audience: str,
    now: datetime,
) -> CredentialEnvelope:
    if (
        registry.get("schema") != LOCAL_READ_REGISTRY_SCHEMA_V1
        or registry.get("schema_version") != 1
        or registry.get("audience") != audience
        or not isinstance(registry.get("issued_at"), int)
        or isinstance(registry.get("issued_at"), bool)
        or not isinstance(registry.get("expires_at"), int)
        or isinstance(registry.get("expires_at"), bool)
        or registry["issued_at"] < 0
        or registry["expires_at"] <= registry["issued_at"]
        or registry["expires_at"] - registry["issued_at"]
        > LOCAL_READ_MAX_LIFETIME_SECONDS
        or not isinstance(registry.get("credential_digest_sha256"), str)
        or _LOWER_HEX_64.fullmatch(registry["credential_digest_sha256"]) is None
    ):
        raise LocalReadCredentialConfigurationError(
            "local read credential registry is malformed"
        )
    try:
        issued_at = datetime.fromtimestamp(registry["issued_at"], tz=timezone.utc)
        envelope = CredentialEnvelope(
            actor_id=registry["actor_id"],
            session_id=registry["session_id"],
            correlation_id=registry["correlation_id"],
            scopes=frozenset({SCOPE_READ}),
            expires_at=datetime.fromtimestamp(registry["expires_at"], tz=timezone.utc),
            issuer=registry["issuer"],
            audience=registry["audience"],
        )
    except (KeyError, OSError, OverflowError, TypeError, ValueError):
        raise LocalReadCredentialConfigurationError(
            "local read credential registry is malformed"
        ) from None
    if now < issued_at:
        raise UnauthenticatedError("credential not yet valid")
    return envelope


class LocalReadCredentialVerifier:
    """Verify one rotatable, file-registered, read-only local capability."""

    def __init__(
        self,
        *,
        data_root: Path,
        audience: str,
        now: datetime | None = None,
    ) -> None:
        if (
            not isinstance(data_root, Path)
            or not data_root.is_absolute()
            or not isinstance(audience, str)
            or not audience
        ):
            raise LocalReadCredentialConfigurationError(
                "local read credential data root is invalid"
            )
        self._data_root = data_root
        self._audience = audience
        # Fail startup closed instead of discovering malformed authority on the
        # first protected request.
        registry = _read_registry(self._data_root)
        current = now if now is not None else utc_now()
        try:
            envelope = _validated_envelope(
                registry,
                audience=self._audience,
                now=current,
            )
        except UnauthenticatedError:
            raise LocalReadCredentialConfigurationError(
                "local read credential registry is not current"
            ) from None
        if envelope.is_expired(current):
            raise LocalReadCredentialConfigurationError(
                "local read credential registry is expired"
            )

    def verify(
        self,
        credential: str | None,
        *,
        audience: str,
        now: datetime | None = None,
    ) -> AuthorityContext:
        if not credential or _TOKEN.fullmatch(credential) is None:
            raise UnauthenticatedError("credential required")
        if audience != self._audience:
            raise UnauthenticatedError("credential audience mismatch")
        current = now if now is not None else utc_now()
        if current.tzinfo is None or current.utcoffset() is None:
            raise UnauthenticatedError("credential clock invalid")
        try:
            registry = _read_registry(self._data_root)
            envelope = _validated_envelope(
                registry,
                audience=self._audience,
                now=current,
            )
        except LocalReadCredentialConfigurationError:
            raise UnauthenticatedError("credential verification unavailable") from None
        digest = hashlib.sha256(credential.encode("ascii")).hexdigest()
        if not hmac.compare_digest(digest, registry["credential_digest_sha256"]):
            raise UnauthenticatedError("credential not recognized")
        if envelope.is_expired(current):
            raise UnauthenticatedError("credential expired")
        return AuthorityContext(envelope=envelope)
