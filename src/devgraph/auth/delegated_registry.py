"""Digest-only registry and verifier for delegated Devgraph read credentials."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.delegated_contract import CredentialManifest, canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.model.base import utc_now
from devgraph.ops.local_path_integrity import (
    LocalPathIntegrityError,
    require_file_descriptor_without_acl,
    require_receiver_directory_path,
)

DELEGATED_REGISTRY_RELATIVE_PATH = Path(
    "secrets/devgraph.delegated-read.v1/credential-registry.json"
)
DELEGATED_REGISTRY_SCHEMA_V1 = "devgraph-delegated-read-credential-registry.v1"
DELEGATED_CREDENTIAL_PREFIX_V1 = "dgrd1_"
DELEGATED_REGISTRY_MAX_BYTES = 1_048_576

_TOKEN = re.compile(r"^dgrd1_[A-Za-z0-9_-]{43}$", re.ASCII)
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class DelegatedRegistryConfigurationError(RuntimeError):
    """Redaction-safe invalid delegated credential registry configuration."""


class DelegatedCredentialRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    credential_digest_sha256: str
    manifest: CredentialManifest
    manifest_digest_sha256: str
    status: Literal["active", "revoked", "superseded"]

    @field_validator("credential_digest_sha256", "manifest_digest_sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        if _LOWER_HEX_64.fullmatch(value) is None:
            raise ValueError("invalid digest")
        return value

    @model_validator(mode="after")
    def validate_manifest_digest(self) -> DelegatedCredentialRecord:
        actual = hashlib.sha256(canonical_json(self.manifest)).hexdigest()
        if not hmac.compare_digest(actual, self.manifest_digest_sha256):
            raise ValueError("manifest digest mismatch")
        return self


class DelegatedCredentialRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_: Literal["devgraph-delegated-read-credential-registry.v1"] = Field(alias="schema")
    schema_version: Literal[1]
    records: tuple[DelegatedCredentialRecord, ...]

    @model_validator(mode="after")
    def validate_records(self) -> DelegatedCredentialRegistry:
        if not self.records:
            raise ValueError("registry cannot be empty")
        ids = [record.manifest.credential_id for record in self.records]
        digests = [record.credential_digest_sha256 for record in self.records]
        if len(set(ids)) != len(ids) or len(set(digests)) != len(digests):
            raise ValueError("duplicate registry record")
        by_id = {record.manifest.credential_id: record for record in self.records}
        replacement_targets: set[str] = set()
        for record in self.records:
            predecessor_id = record.manifest.replaces_credential_id
            if predecessor_id is None:
                continue
            predecessor = by_id.get(predecessor_id)
            if (
                predecessor is None
                or predecessor.manifest.subject_public_key != record.manifest.subject_public_key
                or predecessor.manifest.request_id != record.manifest.request_id
                or predecessor.manifest.version >= record.manifest.version
                or predecessor_id in replacement_targets
            ):
                raise ValueError("replacement_chain_invalid")
            replacement_targets.add(predecessor_id)
        for record in self.records:
            if (
                record.status == "superseded"
                and record.manifest.credential_id not in replacement_targets
            ):
                raise ValueError("replacement_chain_invalid")
        return self


def _read_registry(data_root: Path) -> DelegatedCredentialRegistry:
    try:
        directory = require_receiver_directory_path(
            data_root,
            DELEGATED_REGISTRY_RELATIVE_PATH.parent,
            missing_ok=False,
        )
        if directory is None:
            raise DelegatedRegistryConfigurationError("delegated registry unavailable")
        path = directory / DELEGATED_REGISTRY_RELATIVE_PATH.name
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
                raise DelegatedRegistryConfigurationError("delegated registry unsafe")
            require_file_descriptor_without_acl(descriptor)
            raw = os.read(descriptor, DELEGATED_REGISTRY_MAX_BYTES + 1)
        finally:
            os.close(descriptor)
    except (
        FileNotFoundError,
        LocalPathIntegrityError,
        OSError,
        ValueError,
    ):
        raise DelegatedRegistryConfigurationError("delegated registry unavailable") from None
    if not raw or len(raw) > DELEGATED_REGISTRY_MAX_BYTES:
        raise DelegatedRegistryConfigurationError("delegated registry malformed")
    try:
        registry = DelegatedCredentialRegistry.model_validate_json(raw)
    except ValueError:
        raise DelegatedRegistryConfigurationError("delegated registry malformed") from None
    if raw != canonical_json(registry):
        raise DelegatedRegistryConfigurationError("delegated registry non-canonical")
    return registry


class DelegatedReadCredentialVerifier:
    """Verify one of several digest-registered delegated read bearers."""

    def __init__(self, *, data_root: Path, audience: str) -> None:
        if not isinstance(data_root, Path) or not data_root.is_absolute() or not audience:
            raise DelegatedRegistryConfigurationError("delegated registry configuration invalid")
        self._data_root = data_root
        self._audience = audience
        registry = _read_registry(data_root)
        if any(record.manifest.audience != audience for record in registry.records):
            raise DelegatedRegistryConfigurationError("delegated registry audience mismatch")

    def verify(
        self,
        credential: str | None,
        *,
        audience: str,
        now: datetime | None = None,
    ) -> AuthorityContext:
        if not credential or _TOKEN.fullmatch(credential) is None:
            raise UnauthenticatedError("invalid_credential")
        if audience != self._audience:
            raise UnauthenticatedError("audience_mismatch")
        current = now if now is not None else utc_now()
        if current.tzinfo is None or current.utcoffset() is None:
            raise UnauthenticatedError("invalid_credential")
        try:
            registry = _read_registry(self._data_root)
        except DelegatedRegistryConfigurationError:
            raise UnauthenticatedError("invalid_credential") from None
        digest = hashlib.sha256(credential.encode("ascii")).hexdigest()
        record = next(
            (
                candidate
                for candidate in registry.records
                if hmac.compare_digest(digest, candidate.credential_digest_sha256)
            ),
            None,
        )
        if record is None:
            raise UnauthenticatedError("invalid_credential")
        if record.status == "revoked":
            raise UnauthenticatedError("credential_revoked")
        if record.status == "superseded":
            raise UnauthenticatedError("credential_superseded")
        manifest = record.manifest
        if current < manifest.not_before:
            raise UnauthenticatedError("credential_not_yet_valid")
        if current >= manifest.expires_at:
            raise UnauthenticatedError("credential_expired")
        authority_digest = hashlib.sha256(manifest.credential_id.encode("utf-8")).hexdigest()
        envelope = CredentialEnvelope(
            actor_id=manifest.credential_id,
            session_id=f"dgrd:{authority_digest[:24]}",
            correlation_id=f"dgrd:{record.manifest_digest_sha256[:24]}",
            scopes=frozenset(manifest.granted_scopes),
            expires_at=manifest.expires_at.astimezone(timezone.utc),
            issuer=manifest.issuer_key_id,
            audience=manifest.audience,
            credential_id=manifest.credential_id,
            credential_version=manifest.version,
            read_grants=manifest.read_grants,
            lifecycle_status=record.status,
            resource=manifest.resource,
        )
        return AuthorityContext(envelope=envelope)
