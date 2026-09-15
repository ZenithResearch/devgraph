"""CLI authentication diagnostics and read-capability provisioning."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from devgraph.local_host import (
    DEFAULT_CONFIG_PATH,
    LocalHostError,
    load_local_config,
    local_read_credential_status,
    provision_local_read_credential,
)
from devgraph.ops import signer_profile
from devgraph.ops.local_path_integrity import (
    LocalPathIntegrityError,
    require_receiver_directory_path,
)


def _bundle_presence(root: Path, relative: Path, names: tuple[str, ...]) -> str:
    """Presence is not proof of a current grant or authorization for any request."""
    try:
        directory = require_receiver_directory_path(root, relative, missing_ok=True)
        if directory is None:
            return "missing"
        for name in names:
            info = (directory / name).lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o7077
            ):
                return "unavailable_or_unsafe"
        return "present_unverified"
    except FileNotFoundError:
        return "missing"
    except (OSError, LocalPathIntegrityError):
        return "unavailable_or_unsafe"


def auth_status(*, config_path=DEFAULT_CONFIG_PATH, check=False) -> dict:
    result = {"signer": signer_profile.signer_status(check=check)}
    producer = _bundle_presence(
        signer_profile.INSTALL_ROOT,
        Path("secS/authority/devgraph.work.v1"),
        (
            "producer-manifest.json",
            "receiver-policy.json",
            "secs-public-key-registry.json",
            "verifier.key",
        ),
    )
    result["named_work_authority"] = {
        "producer_bundle": producer,
        "receiver_bundle": "unavailable",
        "grant_validity": "not_evaluated",
        "authorization": "secS and Devgraph evaluate each exact request",
    }
    try:
        config = load_local_config(config_path)
        result["storage_available"] = (
            config.availability_path.exists() and config.data_root.is_dir()
        )
        result["named_work_authority"]["receiver_bundle"] = _bundle_presence(
            config.data_root,
            Path("secrets/secs-magik/devgraph.work.v1"),
            ("receiver.json", "secs-public-key-registry.json"),
        )
        result["read_credential"] = local_read_credential_status(config)
    except (LocalHostError, OSError):
        result["read_credential"] = {"valid": False, "state": "missing_invalid_or_unavailable"}
    return result


def provision_read(
    *,
    config_path=DEFAULT_CONFIG_PATH,
    actor_id="local-devgraph-operator",
    ttl_hours=24 * 30,
    replace=False,
) -> dict:
    config = load_local_config(config_path)
    if not config.availability_path.exists() or not config.data_root.is_dir():
        raise signer_profile.SignerProfileError("configured storage is unavailable")
    # Refuse unsafe/missing roots before the existing provisioner can create files.
    try:
        require_receiver_directory_path(config.data_root, Path("devgraph"), missing_ok=False)
        require_receiver_directory_path(config.data_root, Path("secrets"), missing_ok=False)
        for relative in (Path("devgraph/credentials"), Path("secrets/devgraph.read.v1")):
            require_receiver_directory_path(config.data_root, relative, missing_ok=True)
    except LocalPathIntegrityError:
        raise signer_profile.SignerProfileError(
            "configured credential storage is unavailable or unsafe"
        ) from None
    state = provision_local_read_credential(
        config,
        actor_id=actor_id,
        ttl_seconds=ttl_hours * 60 * 60,
        replace=replace,
    )
    return {**local_read_credential_status(config), "state": state}
