from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from devgraph.auth import (
    CATEGORY_WRITE,
    SCOPE_READ,
    ForbiddenError,
    LocalReadCredentialConfigurationError,
    LocalReadCredentialVerifier,
    UnauthenticatedError,
    require_scope,
)
from devgraph.local_host import (
    LocalHostConfig,
    ProvisioningError,
    local_read_credential_paths,
    local_read_credential_status,
    provision_local_read_credential,
    read_local_read_credential,
)


def _config(tmp_path: Path) -> LocalHostConfig:
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    (data_root / "secrets").mkdir(mode=0o700)
    return LocalHostConfig.build(
        data_root=data_root,
        host_root=tmp_path / "host",
        validate_data_root=False,
    )


def test_local_read_credential_is_digest_registered_and_read_only(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    assert provision_local_read_credential(
        config,
        actor_id="pubkey:sha256:" + "ab" * 32,
        ttl_seconds=3_600,
        now=1_800_000_000,
    ) == "created"
    credential = read_local_read_credential(config)
    credential_path, registry_path = local_read_credential_paths(config)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    assert credential.startswith("dgread1_")
    assert credential not in registry_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(credential_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(registry_path.stat().st_mode) == 0o600

    context = LocalReadCredentialVerifier(
        data_root=config.data_root,
        audience="devgraph",
        now=datetime.fromtimestamp(1_800_000_001, tz=timezone.utc),
    ).verify(
        credential,
        audience="devgraph",
        now=datetime.fromtimestamp(1_800_000_001, tz=timezone.utc),
    )
    assert context.actor_id == "pubkey:sha256:" + "ab" * 32
    assert context.scopes == frozenset({SCOPE_READ})
    with pytest.raises(ForbiddenError):
        require_scope(context, CATEGORY_WRITE)
    assert registry["credential_digest_sha256"] not in credential


def test_wrong_expired_or_wrong_audience_credential_fails_closed(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    provision_local_read_credential(
        config,
        ttl_seconds=300,
        now=1_800_000_000,
    )
    verifier = LocalReadCredentialVerifier(
        data_root=config.data_root,
        audience="devgraph",
        now=datetime.fromtimestamp(1_800_000_001, tz=timezone.utc),
    )
    credential = read_local_read_credential(config)

    with pytest.raises(UnauthenticatedError, match="not recognized"):
        verifier.verify(
            "dgread1_" + "A" * 43,
            audience="devgraph",
            now=datetime.fromtimestamp(1_800_000_001, tz=timezone.utc),
        )
    with pytest.raises(UnauthenticatedError, match="expired"):
        verifier.verify(
            credential,
            audience="devgraph",
            now=datetime.fromtimestamp(1_800_000_300, tz=timezone.utc),
        )
    with pytest.raises(UnauthenticatedError, match="audience mismatch"):
        verifier.verify(
            credential,
            audience="another-audience",
            now=datetime.fromtimestamp(1_800_000_001, tz=timezone.utc),
        )


def test_rotation_invalidates_the_previous_value_without_exposing_the_new_one(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    provision_local_read_credential(config, ttl_seconds=3_600)
    old = read_local_read_credential(config)

    assert provision_local_read_credential(
        config,
        actor_id="local-codex-reader",
        ttl_seconds=7_200,
        replace=True,
    ) == "rotated"
    new = read_local_read_credential(config)
    status = local_read_credential_status(config)

    assert old != new
    assert new not in json.dumps(status)
    assert status["actor_id"] == "local-codex-reader"
    verifier = LocalReadCredentialVerifier(data_root=config.data_root, audience="devgraph")
    with pytest.raises(UnauthenticatedError, match="not recognized"):
        verifier.verify(old, audience="devgraph")
    assert verifier.verify(new, audience="devgraph").scopes == frozenset({SCOPE_READ})


def test_credential_survives_a_commit_addressed_release_change(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provision_local_read_credential(config, ttl_seconds=3_600)
    credential = read_local_read_credential(config)
    upgraded = LocalHostConfig.build(
        data_root=config.data_root,
        host_root=tmp_path / "next-release",
        validate_data_root=False,
    )

    assert provision_local_read_credential(upgraded) == "existing"
    assert read_local_read_credential(upgraded) == credential


def test_registry_permissions_are_a_startup_boundary(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provision_local_read_credential(config, ttl_seconds=3_600)
    _, registry_path = local_read_credential_paths(config)
    registry_path.chmod(0o644)

    with pytest.raises(LocalReadCredentialConfigurationError, match="registry"):
        LocalReadCredentialVerifier(data_root=config.data_root, audience="devgraph")


def test_client_credential_permissions_are_a_read_boundary(tmp_path: Path) -> None:
    config = _config(tmp_path)
    provision_local_read_credential(config, ttl_seconds=3_600)
    credential_path, _ = local_read_credential_paths(config)
    credential_path.chmod(0o644)

    with pytest.raises(ProvisioningError, match="credential is unsafe"):
        read_local_read_credential(config)
