"""Private recovery sets across the two configured local storage failure domains.

This module never reads actor/verifier seeds or activates/restores authority.
Wallet and secS own export and verification of their respective custody files.
Database restoration remains an explicit isolated operator drill.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from itertools import chain
from pathlib import Path

from devgraph.local_host import load_local_config
from devgraph.ops import local_maintenance as maintenance
from devgraph.ops.local_path_integrity import (
    _darwin_mount_info,
    _require_no_unsafe_acl,
    require_file_descriptor_without_acl,
    require_receiver_directory_path,
)
from devgraph.ops.named_work_agent import INSTALL_ROOT, SECS_BINARY, WALLET_BINARY
from devgraph.ops.secs_issue_create_receiver import _strict_json_object
from devgraph.ops.secs_issue_create_wallet import (
    _close_wallet_binary_descriptors,
    _require_fixed_executable,
)
from devgraph.ops.signer_profile import signer_environment, wallet_key_operation

SCHEMA = "devgraph.local-recovery.v1"
INTERNAL_RELATIVE = Path("Devgraph/recovery")
EXTERNAL_RELATIVE = Path("devgraph/recovery")
INACTIVE = "inactive_pending_current_authority_review"
KEEP_RECOVERY_PAIRS = 7
_MANIFEST_FIELDS = {
    "schema",
    "artifact_id",
    "complete",
    "created_at",
    "source_backup_id",
    "database_manifest_sha256",
    "signer_public_key",
    "authority_binding",
    "authority_file_hashes",
    "receiver_admission",
    "authority_restore_state",
    "application_encrypted",
    "destination_storage",
    "source_storage",
}
_BINDING = (
    "policy_id",
    "policy_version",
    "policy_digest_sha256",
    "secs_verifier_key_id",
    "registry_sha256",
)
_AUTHORITY_FILES = {
    "receiver-policy.json",
    "producer-manifest.json",
    "secs-public-key-registry.json",
    "verifier.key",
    "replay.sqlite3",
}
_DATABASE_FILES = {
    "neo4j.dump",
    "system.dump",
    "local-config.json",
    "audit.sqlite3",
    "support/neo4j/conf/neo4j.conf",
    "support/secrets/neo4j_password",
    "support/devgraph/credentials/devgraph.read",
    "support/secrets/devgraph.read.v1/credential-registry.json",
}
_DATABASE_FILES.update(
    f"support/secrets/secs-magik/{contract}/{name}"
    for contract in (
        "devgraph.issue.create.v1",
        "devgraph.monitor.view.read.v1",
        "devgraph.work.v1",
    )
    for name in ("receiver.json", "secs-public-key-registry.json")
)


class RecoveryError(RuntimeError):
    """Fixed, redaction-safe recovery failure."""


def recovery_roots(config):
    return {
        "internal": INSTALL_ROOT / INTERNAL_RELATIVE,
        "external": config.data_root / EXTERNAL_RELATIVE,
    }


def _roots(config, *, create=False):
    roots = recovery_roots(config)
    for label, root in roots.items():
        base = INSTALL_ROOT if label == "internal" else config.data_root
        relative = INTERNAL_RELATIVE if label == "internal" else EXTERNAL_RELATIVE
        if create:
            maintenance._private_directory(base, relative)
        else:
            require_receiver_directory_path(base, relative, missing_ok=False)
        if root.stat().st_mode & 0o077:
            raise RecoveryError("recovery_directory_not_private")
    return roots


def _json(path):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise RecoveryError("duplicate_recovery_metadata_field")
            value[key] = item
        return value

    def constant(_):
        raise RecoveryError("non_finite_recovery_metadata")

    value = json.loads(
        maintenance._private_file(path), object_pairs_hook=pairs, parse_constant=constant
    )
    if not isinstance(value, dict):
        raise RecoveryError("invalid_recovery_metadata")
    return value


def _native(relative, args, env=None):
    path = INSTALL_ROOT / relative
    directories, binary = _require_fixed_executable(
        path, expected_path=path, install_root=INSTALL_ROOT, relative_path=relative
    )
    try:
        result = subprocess.run(
            [str(path), *args],
            env=dict(env or {}),
            cwd="/",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=90,
            check=False,
        )
        if result.returncode or len(result.stdout) > 64 * 1024:
            raise RecoveryError("native_recovery_operation_failed")
        return _strict_json_object(result.stdout, label="native recovery descriptor")
    finally:
        _close_wallet_binary_descriptors(directories, binary)


def _disk_identity(path):
    if sys.platform != "darwin":
        raise RecoveryError("physical_storage_identity_unavailable")
    mount = _darwin_mount_info(path).mounted_on
    result = subprocess.run(
        ["/usr/sbin/diskutil", "info", "-plist", mount],
        env={"PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    if result.returncode or len(result.stdout) > 128 * 1024:
        raise RecoveryError("physical_storage_identity_unavailable")
    value = plistlib.loads(result.stdout)
    stores = [item.get("APFSPhysicalStore") for item in value.get("APFSPhysicalStores", [])]
    disks = []
    for store in stores or [value.get("ParentWholeDisk")]:
        match = re.fullmatch(r"(disk[0-9]+)(?:s[0-9]+)?", store or "")
        if match is None:
            raise RecoveryError("physical_storage_identity_unavailable")
        disks.append(match.group(1))
    volume = value.get("VolumeUUID")
    if not isinstance(volume, str) or len(volume) > 64 or not disks:
        raise RecoveryError("physical_storage_identity_unavailable")
    return {
        "device": path.stat().st_dev,
        "physical_disks": sorted(set(disks)),
        "volume_uuid": volume,
    }


def _independent(left, right):
    return left["device"] != right["device"] and set(left["physical_disks"]).isdisjoint(
        right["physical_disks"]
    )


def _private_tree(root, *, private_directories=True):
    """Inspect metadata only; never read actor/verifier custody bytes."""
    count = 0
    for path in chain([root], root.rglob("*")):
        count += 1
        info = path.lstat()
        if (
            count > 256
            or info.st_uid != os.geteuid()
            or info.st_mode
            & (
                0o7022
                if stat.S_ISDIR(info.st_mode) and path != root and not private_directories
                else 0o7077
            )
            or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
            or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
        ):
            raise RecoveryError("unsafe_recovery_tree")
        _require_no_unsafe_acl(path)


def _copy_database(artifact, target, manifest):
    if not set(manifest["files"]) <= _DATABASE_FILES:
        raise RecoveryError("unexpected_managed_backup_component")
    maintenance._private_directory(target.parent.parent, Path("database") / target.name)
    for relative, expected in manifest["files"].items():
        source = artifact / relative
        destination = target / relative
        if destination.parent != target:
            maintenance._private_directory(target, destination.parent.relative_to(target))
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o7077
                or info.st_nlink != 1
            ):
                raise RecoveryError("unsafe_managed_backup_component")
            require_file_descriptor_without_acl(fd)
            digest = hashlib.sha256()
            size = 0
            with destination.open("xb") as output:
                os.fchmod(output.fileno(), 0o600)
                while chunk := os.read(fd, 1024 * 1024):
                    size += len(chunk)
                    if size > expected["bytes"]:
                        raise RecoveryError("managed_backup_changed")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if expected != {"bytes": size, "sha256": digest.hexdigest()}:
                raise RecoveryError("managed_backup_changed")
        finally:
            os.close(fd)
    maintenance._json_write(target / "manifest.json", manifest)
    maintenance.verify_backup(target)


def _binding(descriptor):
    if descriptor.get("schema") != "secs-devgraph-work-admin.v1":
        raise RecoveryError("invalid_authority_recovery_descriptor")
    try:
        value = {key: descriptor[key] for key in _BINDING}
    except KeyError:
        raise RecoveryError("invalid_authority_recovery_descriptor") from None
    if (
        type(value["policy_version"]) is not int
        or value["policy_version"] < 1
        or any(
            not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{64}", value[key])
            for key in ("policy_digest_sha256", "registry_sha256")
        )
        or any(
            not isinstance(value[key], str)
            or not re.fullmatch(r"[A-Za-z0-9:._-]{1,128}", value[key])
            for key in ("policy_id", "secs_verifier_key_id")
        )
    ):
        raise RecoveryError("invalid_authority_recovery_descriptor")
    return value


def _authority_files(descriptor):
    metadata = descriptor.get("file_hashes")
    if (
        not isinstance(metadata, dict)
        or set(metadata) != _AUTHORITY_FILES
        or set(descriptor.get("files", [])) != _AUTHORITY_FILES
    ):
        raise RecoveryError("invalid_authority_recovery_files")
    for value in metadata.values():
        if (
            not isinstance(value, dict)
            or set(value) != {"sha256", "size_bytes"}
            or not isinstance(value["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
            or type(value["size_bytes"]) is not int
            or not 1 <= value["size_bytes"] <= 256 * 1024**2
        ):
            raise RecoveryError("invalid_authority_recovery_files")
    return metadata


def _match_signer(descriptor, public):
    actor = "pubkey:sha256:" + hashlib.sha256(bytes.fromhex(public)).hexdigest()
    rules = descriptor.get("policy", {}).get("rules", [])
    if not any(rule.get("actor_id") == actor for rule in rules):
        raise RecoveryError("authority_recovery_signer_mismatch")


def _match_receiver(database, descriptor):
    authority = database / "support/secrets/secs-magik/devgraph.work.v1"
    binding = _binding(descriptor)
    try:
        receiver = _json(authority / "receiver.json")
    except FileNotFoundError:
        rules = descriptor.get("policy", {}).get("rules", [])
        if (
            descriptor.get("current_authority_valid") is not False
            or not rules
            or not all(rule.get("status") == "revoked" for rule in rules)
        ):
            raise RecoveryError("active_authority_receiver_admission_missing") from None
        admission = "absent_revoked"
    else:
        if receiver.get("policy_binding") != {key: binding[key] for key in _BINDING[:3]}:
            raise RecoveryError("authority_backup_binding_mismatch")
        admission = "present"
    if (
        hashlib.sha256(
            maintenance._private_file(authority / "secs-public-key-registry.json")
        ).hexdigest()
        != binding["registry_sha256"]
    ):
        raise RecoveryError("authority_backup_registry_mismatch")
    return admission


def verify_recovery_set(path):
    """Verify archived components without admitting a signer or receiver."""
    _private_tree(path)
    manifest = _json(path / "manifest.json")
    if (
        set(manifest) != _MANIFEST_FIELDS
        or manifest.get("schema") != SCHEMA
        or manifest.get("complete") is not True
        or manifest.get("artifact_id") != path.name
        or not maintenance._ARTIFACT.fullmatch(path.name)
        or manifest.get("authority_restore_state") != INACTIVE
        or manifest.get("application_encrypted") is not False
    ):
        raise RecoveryError("invalid_recovery_manifest")
    if (
        type(manifest.get("created_at")) not in {int, float}
        or not math.isfinite(manifest["created_at"])
        or not 0 <= manifest["created_at"] <= 2**53
    ):
        raise RecoveryError("invalid_recovery_timestamp")
    sources = manifest.get("source_storage")
    if not isinstance(sources, dict) or set(sources) != {"database", "identity", "authority"}:
        raise RecoveryError("invalid_recovery_storage_metadata")
    for storage in [manifest.get("destination_storage"), *sources.values()]:
        if (
            not isinstance(storage, dict)
            or set(storage) != {"device", "physical_disks", "volume_uuid"}
            or type(storage["device"]) is not int
            or storage["device"] < 0
            or not isinstance(storage["physical_disks"], list)
            or not storage["physical_disks"]
            or not all(
                isinstance(disk, str) and re.fullmatch(r"disk[0-9]+", disk)
                for disk in storage["physical_disks"]
            )
            or not isinstance(storage["volume_uuid"], str)
            or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", storage["volume_uuid"])
        ):
            raise RecoveryError("invalid_recovery_storage_metadata")
    backup_id = manifest.get("source_backup_id", "")
    if not isinstance(backup_id, str) or not maintenance._ARTIFACT.fullmatch(backup_id):
        raise RecoveryError("invalid_recovery_backup_reference")
    database = path / "database" / backup_id
    database_manifest = maintenance.verify_backup(database)
    if (
        hashlib.sha256(maintenance._private_file(database / "manifest.json")).hexdigest()
        != manifest["database_manifest_sha256"]
    ):
        raise RecoveryError("recovery_database_manifest_mismatch")
    public = wallet_key_operation("--inspect-identity", path / "identity/devgraph-dregg.key")
    if public != manifest.get("signer_public_key"):
        raise RecoveryError("recovery_signer_identity_mismatch")
    authority = _native(
        SECS_BINARY, ["admin", "verify-snapshot", "--input-directory", str(path / "authority")]
    )
    if _binding(authority) != manifest.get("authority_binding"):
        raise RecoveryError("recovery_authority_binding_mismatch")
    if _match_receiver(database, authority) != manifest.get("receiver_admission"):
        raise RecoveryError("recovery_receiver_admission_changed")
    _match_signer(authority, public)
    if _authority_files(authority) != manifest.get("authority_file_hashes"):
        raise RecoveryError("recovery_authority_files_changed")
    expected = {"manifest.json", "identity/devgraph-dregg.key"}
    expected.update("authority/" + name for name in _AUTHORITY_FILES)
    expected.update(
        "database/" + backup_id + "/" + name
        for name in [*database_manifest["files"], "manifest.json"]
    )
    if {
        entry.relative_to(path).as_posix() for entry in path.rglob("*") if entry.is_file()
    } != expected:
        raise RecoveryError("unexpected_recovery_component")
    expected_directories = {
        parent.as_posix()
        for name in expected
        for parent in Path(name).parents
        if parent != Path(".")
    }
    if {
        entry.relative_to(path).as_posix() for entry in path.rglob("*") if entry.is_dir()
    } != expected_directories:
        raise RecoveryError("unexpected_recovery_directory")
    return manifest


def create_recovery(config, artifact, *, lock_held=False):
    """Preserve both components before publishing either destination as latest."""
    with nullcontext() if lock_held else maintenance._backup_lock(config):
        manifest = maintenance.verify_backup(artifact)
        _private_tree(artifact, private_directories=False)
        roots = _roots(config, create=True)
        identities = {label: _disk_identity(root) for label, root in roots.items()}
        source_disk = _disk_identity(config.data_root)
        signer = signer_environment()
        public = signer.get("DEVGRAPH_SIGNING_PUBLIC_KEY")
        if not public or not re.fullmatch(r"[0-9a-f]{64}", public):
            raise RecoveryError("recovery_signer_not_configured")
        key_disk = _disk_identity(Path(signer["DEVGRAPH_SIGNING_KEY_FILE"]).parent)
        authority_disk = _disk_identity(INSTALL_ROOT)
        if (
            not _independent(identities["internal"], source_disk)
            or not _independent(identities["external"], key_disk)
            or not _independent(identities["external"], authority_disk)
            or not _independent(identities["internal"], identities["external"])
        ):
            raise RecoveryError("independent_recovery_storage_required")
        required = sum(item["bytes"] for item in manifest["files"].values()) + 512 * 1024**2
        if any(
            shutil.disk_usage(root).free < max(maintenance.MIN_FREE_BYTES, required * 2)
            for root in roots.values()
        ):
            raise RecoveryError("recovery_capacity_insufficient")
        recovery_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
        destinations = []
        binding = None
        for label, root in roots.items():
            target = root / recovery_id
            target.mkdir(mode=0o700)
            database = target / "database" / artifact.name
            _copy_database(artifact, database, manifest)
            for name in ("identity", "authority"):
                (target / name).mkdir(mode=0o700)
            exported = _native(
                WALLET_BINARY,
                ["--export-identity", "--output-file", str(target / "identity/devgraph-dregg.key")],
                signer,
            )
            if exported != {
                "schema": "devgraph.wallet-recovery-export.v1",
                "public_key": public,
                "exported": True,
                "application_encrypted": False,
            }:
                raise RecoveryError("invalid_wallet_recovery_descriptor")
            authority = _native(
                SECS_BINARY, ["admin", "snapshot", "--output-directory", str(target / "authority")]
            )
            current = _binding(authority)
            _match_signer(authority, public)
            if binding is not None and binding != current:
                raise RecoveryError("authority_changed_during_recovery")
            binding = current
            admission = _match_receiver(database, authority)
            value = {
                "schema": SCHEMA,
                "artifact_id": recovery_id,
                "complete": True,
                "created_at": time.time(),
                "source_backup_id": artifact.name,
                "database_manifest_sha256": hashlib.sha256(
                    maintenance._private_file(database / "manifest.json")
                ).hexdigest(),
                "signer_public_key": public,
                "authority_binding": binding,
                "authority_file_hashes": _authority_files(authority),
                "receiver_admission": admission,
                "authority_restore_state": INACTIVE,
                "application_encrypted": False,
                "destination_storage": identities[label],
                "source_storage": {
                    "database": source_disk,
                    "identity": key_disk,
                    "authority": authority_disk,
                },
            }
            maintenance._json_write(target / "manifest.json", value)
            verify_recovery_set(target)
            destinations.append({"location": label, "path": str(target), "verified": True})
        # A changed signer profile or authority generation cannot become a mixed latest pair.
        if signer_environment() != signer:
            raise RecoveryError("signer_changed_during_recovery")
        for root in roots.values():
            maintenance._json_write(
                root / "latest.json",
                {"schema": SCHEMA, "artifact_id": recovery_id, "created_at": time.time()},
            )
        result = {
            "successful": True,
            "artifact_id": recovery_id,
            "destinations": destinations,
            "application_encrypted": False,
            "authority_restore_state": INACTIVE,
        }
        try:
            result["retention"] = retain_recovery_pairs(config, newest_id=recovery_id)
        except Exception:
            result["retention"] = {"successful": False, "reason": "recovery_retention_incomplete"}
        return result


def retain_recovery_pairs(config, *, newest_id):
    """Remove only old pairs fully verified under this exact schema and layout."""
    roots = _roots(config)
    sets = [
        {entry.name for entry in root.iterdir() if maintenance._ARTIFACT.fullmatch(entry.name)}
        for root in roots.values()
    ]
    verified = []
    for artifact_id in set.intersection(*sets):
        try:
            manifests = [verify_recovery_set(root / artifact_id) for root in roots.values()]
            bindings = {
                (
                    value["source_backup_id"],
                    value["database_manifest_sha256"],
                    value["receiver_admission"],
                    value["signer_public_key"],
                    json.dumps(value["authority_binding"], sort_keys=True),
                )
                for value in manifests
            }
            if len(bindings) != 1:
                continue
            timestamps = [value["created_at"] for value in manifests]
            if any(type(value) not in {int, float} for value in timestamps):
                continue
            verified.append((max(timestamps), artifact_id))
        except Exception:
            # Unknown, partial, unpaired or damaged sets require operator review.
            continue
    protected = {newest_id}
    for _, artifact_id in sorted(verified, reverse=True):
        if len(protected) < KEEP_RECOVERY_PAIRS:
            protected.add(artifact_id)
    removed = []
    for _, artifact_id in verified:
        if artifact_id in protected:
            continue
        for root in roots.values():
            # Verify again immediately before deletion; never follow a replaced link.
            verify_recovery_set(root / artifact_id)
            shutil.rmtree(root / artifact_id)
        removed.append(artifact_id)
    return {
        "successful": True,
        "keep_verified_pairs": KEEP_RECOVERY_PAIRS,
        "removed_pairs": sorted(removed),
    }


def recovery_status(config, *, verify=False):
    results = []
    for label, root in recovery_roots(config).items():
        result = {"location": label, "path": str(root), "available": False, "verified": False}
        try:
            base = INSTALL_ROOT if label == "internal" else config.data_root
            require_receiver_directory_path(
                base,
                INTERNAL_RELATIVE if label == "internal" else EXTERNAL_RELATIVE,
                missing_ok=False,
            )
            latest = _json(root / "latest.json")
            artifact_id = latest.get("artifact_id", "")
            if (
                latest.get("schema") != SCHEMA
                or not isinstance(artifact_id, str)
                or not maintenance._ARTIFACT.fullmatch(artifact_id)
            ):
                raise RecoveryError("invalid_recovery_reference")
            path = root / artifact_id
            value = verify_recovery_set(path) if verify else _json(path / "manifest.json")
            if (
                value.get("schema") != SCHEMA
                or value.get("complete") is not True
                or value.get("artifact_id") != artifact_id
            ):
                raise RecoveryError("invalid_recovery_manifest")
            result.update(
                available=True,
                verified=verify,
                artifact_id=artifact_id,
                created_at=value["created_at"],
                source_backup_id=value["source_backup_id"],
                authority_restore_state=INACTIVE,
                application_encrypted=False,
            )
        except Exception:
            result["reason"] = "recovery_unavailable_or_unverified"
        results.append(result)
    successful = all(item["available"] for item in results)
    pair_matches = len({item.get("artifact_id") for item in results}) == 1
    independent = None
    if verify and successful:
        try:
            live = {label: _disk_identity(root) for label, root in recovery_roots(config).items()}
            independent = _independent(live["internal"], live["external"])
            for result in results:
                result["current_storage"] = live[result["location"]]
        except Exception:
            independent = False
    return {
        "successful": successful and pair_matches and independent is not False,
        "destinations": results,
        "verification_performed": verify,
        "matching_recovery_pair": successful and pair_matches,
        "independent_storage_verified": independent,
    }


def run_recovery(action, *, artifact_id=None):
    config = load_local_config()
    if config is None:
        raise RecoveryError("local_configuration_required")
    if action in {"status", "verify"}:
        return recovery_status(config, verify=action == "verify")
    if action != "create":
        raise RecoveryError("invalid_recovery_action")
    if artifact_id is None:
        try:
            selected = _json(config.log_root / "maintenance/last-managed-backup.json")
        except FileNotFoundError:
            selected = _json(config.log_root / "maintenance/last-backup.json")
            if selected.get("successful") is not True:
                raise RecoveryError("verified_backup_required") from None
        else:
            if selected.get("verified") is not True:
                raise RecoveryError("verified_backup_required")
        artifact_id = selected.get("artifact_id")
    if not isinstance(artifact_id, str) or not maintenance._ARTIFACT.fullmatch(artifact_id):
        raise RecoveryError("verified_backup_required")
    return create_recovery(config, config.data_root / maintenance.BACKUP_RELATIVE / artifact_id)
