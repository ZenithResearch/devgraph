from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from devgraph.auth.delegated_contract import CredentialManifest, canonical_json
from devgraph.auth.delegated_registry import (
    DELEGATED_REGISTRY_RELATIVE_PATH,
    DelegatedReadCredentialVerifier,
    DelegatedRegistryConfigurationError,
)
from devgraph.auth.errors import UnauthenticatedError

TOKEN = "dgrd1_" + "A" * 43
NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
CONTRACT = Path(__file__).parents[2] / "docs" / "contracts" / "devgraph-delegated-read" / "v1"


def registry_value(*, status: str = "active", digest: str | None = None) -> dict:
    manifest = CredentialManifest.model_validate_json(
        (CONTRACT / "examples/credential-manifest-castalia.json").read_bytes()
    )
    records = [
        {
            "credential_digest_sha256": digest or hashlib.sha256(TOKEN.encode("ascii")).hexdigest(),
            "manifest": manifest.model_dump(mode="json", by_alias=True, exclude_defaults=True),
            "manifest_digest_sha256": hashlib.sha256(canonical_json(manifest)).hexdigest(),
            "status": status,
        }
    ]
    if status == "superseded":
        replacement = CredentialManifest.model_validate(
            {
                **manifest.model_dump(mode="json", by_alias=True, exclude_defaults=True),
                "credential_id": "dgc_castalia_read_v2",
                "version": 2,
                "replaces_credential_id": manifest.credential_id,
            }
        )
        records.append(
            {
                "credential_digest_sha256": "b" * 64,
                "manifest": replacement.model_dump(
                    mode="json", by_alias=True, exclude_defaults=True
                ),
                "manifest_digest_sha256": hashlib.sha256(canonical_json(replacement)).hexdigest(),
                "status": "active",
            }
        )
    return {
        "schema": "devgraph-delegated-read-credential-registry.v1",
        "schema_version": 1,
        "records": records,
    }


def write_registry(root: Path, value: dict) -> None:
    path = root / DELEGATED_REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_bytes(canonical_json(value))
    path.chmod(0o600)


def test_active_digest_verifies_to_granular_context(tmp_path: Path) -> None:
    write_registry(tmp_path, registry_value())
    verifier = DelegatedReadCredentialVerifier(data_root=tmp_path, audience="devgraph")

    context = verifier.verify(TOKEN, audience="devgraph", now=NOW)

    assert context.credential_id == "dgc_castalia_read_v1"
    assert context.credential_version == 1
    assert context.lifecycle_status == "active"
    assert context.resource == "https://work.zenith-research.ca/devgraph"
    assert context.scopes == frozenset(
        {
            "devgraph.work.read",
            "devgraph.arena.read",
            "devgraph.material.read",
            "devgraph.document.read",
        }
    )
    assert tuple(grant.scope for grant in context.read_grants) == (
        "devgraph.work.read",
        "devgraph.arena.read",
        "devgraph.material.read",
        "devgraph.document.read",
    )


@pytest.mark.parametrize(
    ("status", "reason"),
    (("revoked", "credential_revoked"), ("superseded", "credential_superseded")),
)
def test_non_active_lifecycle_fails_closed(tmp_path: Path, status: str, reason: str) -> None:
    write_registry(tmp_path, registry_value(status=status))
    verifier = DelegatedReadCredentialVerifier(data_root=tmp_path, audience="devgraph")

    with pytest.raises(UnauthenticatedError, match=reason):
        verifier.verify(TOKEN, audience="devgraph", now=NOW)


def test_unknown_owner_local_or_malformed_bearer_fails_closed(tmp_path: Path) -> None:
    write_registry(tmp_path, registry_value())
    verifier = DelegatedReadCredentialVerifier(data_root=tmp_path, audience="devgraph")

    for token in (None, "dgread1_" + "A" * 43, "dgrd1_short", "dgrd1_" + "B" * 43):
        with pytest.raises(UnauthenticatedError, match="invalid_credential"):
            verifier.verify(token, audience="devgraph", now=NOW)


def test_registry_rejects_noncanonical_or_unsafe_files(tmp_path: Path) -> None:
    value = registry_value()
    path = tmp_path / DELEGATED_REGISTRY_RELATIVE_PATH
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text(json.dumps(value, indent=2))
    path.chmod(0o600)
    with pytest.raises(DelegatedRegistryConfigurationError):
        DelegatedReadCredentialVerifier(data_root=tmp_path, audience="devgraph")

    path.write_bytes(canonical_json(value))
    path.chmod(0o644)
    with pytest.raises(DelegatedRegistryConfigurationError):
        DelegatedReadCredentialVerifier(data_root=tmp_path, audience="devgraph")


def test_registry_rejects_manifest_digest_mismatch(tmp_path: Path) -> None:
    value = registry_value()
    value["records"][0]["manifest_digest_sha256"] = "0" * 64
    write_registry(tmp_path, value)

    with pytest.raises(DelegatedRegistryConfigurationError):
        DelegatedReadCredentialVerifier(data_root=tmp_path, audience="devgraph")
