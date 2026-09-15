"""Fixed local receiver composition for ``devgraph.monitor.view.read.v1``.

The bundle contains public verification and policy-binding material only.  It
lives at one receiver-owned path under the configured Devgraph data root and
cannot select another operation, audience, origin, route, or trust source.
Missing configuration leaves the HTTP path fail-closed; malformed or unsafe
configuration fails startup rather than silently weakening verification.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from devgraph.auth.enforcement import AuditLog
from devgraph.auth.secs_issue_create import SecSVerifierKeyRegistry
from devgraph.auth.secs_monitor_view_read import (
    DEVGRAPH_JSON_SAFE_INTEGER_MAX_V1,
    DEVGRAPH_MONITOR_VIEW_READ_AUDIENCE_V1,
    DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1,
    DEVGRAPH_MONITOR_VIEW_READ_ORIGIN_V1,
    SecSMonitorViewReadAdapter,
    SecSMonitorViewReadDenied,
    SecSMonitorViewReadPolicyBinding,
    SecSMonitorViewReadReplayStore,
    SecSMonitorViewReadVerifier,
    SecSMonitorViewReadVerifierConfig,
)
from devgraph.ops.local_path_integrity import (
    LocalPathIntegrityError,
    require_file_descriptor_without_acl,
    require_receiver_directory_path,
)
from devgraph.storage.base import GraphStorage

RECEIVER_BUNDLE_RELATIVE_PATH = Path(
    "secrets/secs-magik/devgraph.monitor.view.read.v1"
)
RECEIVER_MANIFEST_NAME = "receiver.json"
SECS_PUBLIC_KEY_REGISTRY_NAME = "secs-public-key-registry.json"
REPLAY_DIRECTORY_NAME = "replay"
REPLAY_STORE_NAME = "claims.json"
REPLAY_LOCK_NAME = "claims.lock"
RECEIVER_MANIFEST_SCHEMA = "devgraph-secs-monitor-view-read-receiver.v1"
REPLAY_STORE_SCHEMA = "devgraph-monitor-view-read-replay-store.v1"
RECEIVER_MANIFEST_MAX_BYTES = 16_384
SECS_PUBLIC_KEY_REGISTRY_MAX_BYTES = 262_144
REPLAY_STORE_MAX_BYTES = 1_048_576
REPLAY_STORE_MAXIMUM_ENTRIES = 4_096

_RECEIVER_MANIFEST_FIELDS = frozenset(
    {
        "audience",
        "operation",
        "origin",
        "policy_binding",
        "schema",
        "schema_version",
        "stable_issuer",
    }
)
_POLICY_BINDING_FIELDS = frozenset(
    {"policy_digest_sha256", "policy_id", "policy_version"}
)


class LocalSecSMonitorViewReadError(RuntimeError):
    """Safe local receiver configuration failure."""


_DURABLE_REPLAY_LOCK = RLock()


class DurableSecSMonitorViewReadReplayStore(SecSMonitorViewReadReplayStore):
    """Owner-private, process-safe exact-operation replay claims."""

    def __init__(
        self,
        path: Path,
        *,
        maximum_entries: int = REPLAY_STORE_MAXIMUM_ENTRIES,
        failure_hook: Callable[[str], None] | None = None,
    ) -> None:
        if (
            not isinstance(path, Path)
            or not path.is_absolute()
            or type(maximum_entries) is not int
            or not 1 <= maximum_entries <= 65_536
            or (failure_hook is not None and not callable(failure_hook))
        ):
            raise ValueError("invalid durable monitor replay store")
        self._path = path
        self._lock_path = path.with_name(REPLAY_LOCK_NAME)
        self._maximum_entries = maximum_entries
        self._failure_hook = failure_hook
        try:
            self._access_store(lambda _state: None)
        except (OSError, ValueError):
            raise LocalSecSMonitorViewReadError(
                "monitor replay store is unavailable"
            ) from None

    def claim(
        self,
        session_digest_sha256: str,
        nonce: str,
        *,
        now: int,
        expires_at: int,
    ) -> None:
        self._validate_claim(
            session_digest_sha256,
            nonce,
            now=now,
            expires_at=expires_at,
        )

        def apply_claim(state: dict[str, Any]) -> str | None:
            if now < state["last_seen_at"]:
                return "monitor_replay_clock_regressed"
            claims = [
                claim for claim in state["claims"] if claim["expires_at"] > now
            ]
            if any(
                claim["session_digest_sha256"] == session_digest_sha256
                and claim["nonce"] == nonce
                for claim in claims
            ):
                return "monitor_request_replayed"
            if len(claims) >= self._maximum_entries:
                return "monitor_replay_store_full"
            claims.append(
                {
                    "expires_at": expires_at,
                    "nonce": nonce,
                    "session_digest_sha256": session_digest_sha256,
                }
            )
            state["claims"] = claims
            state["last_seen_at"] = now
            return None

        try:
            denied_reason = self._access_store(
                apply_claim,
                persist_when=lambda result: result is None,
            )
        except (LocalSecSMonitorViewReadError, OSError, ValueError):
            raise SecSMonitorViewReadDenied(
                "monitor_replay_store_unavailable"
            ) from None
        if denied_reason is not None:
            raise SecSMonitorViewReadDenied(denied_reason)

    def _access_store(
        self,
        operation: Callable[[dict[str, Any]], Any],
        *,
        persist_when: Callable[[Any], bool] | None = None,
    ) -> Any:
        with _DURABLE_REPLAY_LOCK:
            lock_descriptor, lock_created = self._open_lock()
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
                if lock_created:
                    self._fsync_parent()
                state, state_missing = self._read_current_state()
                result = operation(state)
                if state_missing or (
                    persist_when is not None and persist_when(result)
                ):
                    self._replace_state(state)
                return result
            finally:
                try:
                    fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(lock_descriptor)

    def _open_lock(self) -> tuple[int, bool]:
        if not hasattr(os, "O_NOFOLLOW"):
            raise OSError("nofollow unavailable")
        flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
        created = False
        try:
            descriptor = os.open(
                self._lock_path,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(self._lock_path, flags)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_nlink != 1
        ):
            os.close(descriptor)
            raise OSError("unsafe replay lock")
        try:
            require_file_descriptor_without_acl(descriptor)
        except LocalPathIntegrityError:
            os.close(descriptor)
            raise OSError("unsafe replay lock ACL") from None
        return descriptor, created

    def _new_state(self) -> dict[str, Any]:
        return {
            "claims": [],
            "last_seen_at": 0,
            "maximum_entries": self._maximum_entries,
            "operation": DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1,
            "schema": REPLAY_STORE_SCHEMA,
            "schema_version": 1,
        }

    def _read_current_state(self) -> tuple[dict[str, Any], bool]:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            descriptor = os.open(self._path, flags)
        except FileNotFoundError:
            return self._new_state(), True
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_nlink != 1
                or info.st_size > REPLAY_STORE_MAX_BYTES
            ):
                raise OSError("unsafe replay store")
            try:
                require_file_descriptor_without_acl(descriptor)
            except LocalPathIntegrityError:
                raise OSError("unsafe replay store ACL") from None
            return self._read_state(descriptor), False
        finally:
            os.close(descriptor)

    def _read_state(self, descriptor: int) -> dict[str, Any]:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, REPLAY_STORE_MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > REPLAY_STORE_MAX_BYTES:
                raise ValueError("oversized replay store")
        raw = b"".join(chunks)
        state = _strict_json_object(raw, label="monitor replay store")
        if raw != _canonical_json(state):
            raise ValueError("non-canonical replay store")
        self._validate_state(state)
        return state

    def _validate_state(self, state: dict[str, Any]) -> None:
        if (
            set(state)
            != {
                "claims",
                "last_seen_at",
                "maximum_entries",
                "operation",
                "schema",
                "schema_version",
            }
            or state.get("schema") != REPLAY_STORE_SCHEMA
            or state.get("schema_version") != 1
            or state.get("operation") != DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1
            or state.get("maximum_entries") != self._maximum_entries
            or type(state.get("last_seen_at")) is not int
            or not 0 <= state["last_seen_at"] <= DEVGRAPH_JSON_SAFE_INTEGER_MAX_V1
            or not isinstance(state.get("claims"), list)
            or len(state["claims"]) > self._maximum_entries
        ):
            raise ValueError("invalid replay store")
        unique: set[tuple[str, str]] = set()
        for claim in state["claims"]:
            if not isinstance(claim, dict) or set(claim) != {
                "expires_at",
                "nonce",
                "session_digest_sha256",
            }:
                raise ValueError("invalid replay store claim")
            self._validate_claim(
                claim["session_digest_sha256"],
                claim["nonce"],
                now=0,
                expires_at=claim["expires_at"],
            )
            key = (claim["session_digest_sha256"], claim["nonce"])
            if key in unique:
                raise ValueError("duplicate replay store claim")
            unique.add(key)

    def _replace_state(self, state: dict[str, Any]) -> None:
        raw = _canonical_json(state)
        if len(raw) > REPLAY_STORE_MAX_BYTES:
            raise ValueError("oversized replay store")
        temporary_path: Path | None = None
        descriptor: int | None = None
        replaced = False
        flags = os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL
        try:
            for _attempt in range(16):
                candidate = self._path.with_name(
                    f".{REPLAY_STORE_NAME}.{os.getpid()}.{secrets.token_hex(16)}"
                )
                try:
                    descriptor = os.open(candidate, flags, 0o600)
                    temporary_path = candidate
                    break
                except FileExistsError:
                    continue
            if descriptor is None or temporary_path is None:
                raise OSError("replay temporary file unavailable")
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_nlink != 1
            ):
                raise OSError("unsafe replay temporary file")
            try:
                require_file_descriptor_without_acl(descriptor)
            except LocalPathIntegrityError:
                raise OSError("unsafe replay temporary ACL") from None
            written = 0
            while written < len(raw):
                count = os.write(descriptor, raw[written:])
                if count <= 0:
                    raise OSError("short replay store write")
                written += count
            os.fsync(descriptor)
            if self._failure_hook is not None:
                self._failure_hook("after_temp_fsync")
            os.close(descriptor)
            descriptor = None
            self._require_safe_current_destination()
            os.replace(temporary_path, self._path)
            replaced = True
            if self._failure_hook is not None:
                self._failure_hook("after_replace")
            self._fsync_parent()
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_path is not None and not replaced:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    def _require_safe_current_destination(self) -> None:
        try:
            info = self._path.lstat()
        except FileNotFoundError:
            return
        if (
            not stat.S_ISREG(info.st_mode)
            or self._path.is_symlink()
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_nlink != 1
        ):
            raise OSError("unsafe replay store destination")

    def _fsync_parent(self) -> None:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        flags |= getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(self._path.parent, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


@dataclass(frozen=True)
class _ReceiverManifest:
    stable_issuer: str
    policy_binding: SecSMonitorViewReadPolicyBinding


def _fixed_replay_store_path(data_root: Path, bundle: Path) -> Path:
    replay_directory = bundle / REPLAY_DIRECTORY_NAME
    created = False
    try:
        os.mkdir(replay_directory, mode=0o700)
        created = True
    except FileExistsError:
        pass
    try:
        info = replay_directory.lstat()
    except FileNotFoundError as error:
        raise LocalSecSMonitorViewReadError(
            "monitor replay directory is unavailable"
        ) from error
    if (
        not stat.S_ISDIR(info.st_mode)
        or replay_directory.is_symlink()
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise LocalSecSMonitorViewReadError(
            "monitor replay directory is not owner-private"
        )
    try:
        validated = require_receiver_directory_path(
            data_root,
            RECEIVER_BUNDLE_RELATIVE_PATH / REPLAY_DIRECTORY_NAME,
            missing_ok=False,
        )
    except LocalPathIntegrityError as error:
        raise LocalSecSMonitorViewReadError(str(error)) from None
    if validated != replay_directory:
        raise LocalSecSMonitorViewReadError(
            "monitor replay directory is not owner-private"
        )
    if created:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        flags |= getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(bundle, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return replay_directory / REPLAY_STORE_NAME


def _read_public_bounded_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, OSError) as error:
        raise LocalSecSMonitorViewReadError(
            f"{label} is not an available regular file"
        ) from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise LocalSecSMonitorViewReadError(
                f"{label} is not an available regular file"
            )
        if (
            info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
            or info.st_nlink != 1
        ):
            raise LocalSecSMonitorViewReadError(f"{label} is not receiver-owned")
        try:
            require_file_descriptor_without_acl(descriptor)
        except LocalPathIntegrityError:
            raise LocalSecSMonitorViewReadError(
                f"{label} has an extended ACL"
            ) from None
        if info.st_size > maximum_bytes:
            raise LocalSecSMonitorViewReadError(f"{label} exceeds its size limit")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(maximum_bytes + 1)
        if len(raw) > maximum_bytes:
            raise LocalSecSMonitorViewReadError(f"{label} exceeds its size limit")
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
        raise LocalSecSMonitorViewReadError(f"{label} is malformed") from None
    if not isinstance(value, dict):
        raise LocalSecSMonitorViewReadError(f"{label} is malformed")
    return value


def _canonical_json(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise ValueError("invalid canonical replay store") from None


def _parse_receiver_manifest(raw: bytes) -> _ReceiverManifest:
    value = _strict_json_object(raw, label="monitor receiver manifest")
    if (
        set(value) != _RECEIVER_MANIFEST_FIELDS
        or value.get("schema") != RECEIVER_MANIFEST_SCHEMA
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or value.get("operation") != DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1
        or value.get("audience") != DEVGRAPH_MONITOR_VIEW_READ_AUDIENCE_V1
        or value.get("origin") != DEVGRAPH_MONITOR_VIEW_READ_ORIGIN_V1
        or not isinstance(value.get("stable_issuer"), str)
        or not isinstance(value.get("policy_binding"), dict)
        or set(value["policy_binding"]) != _POLICY_BINDING_FIELDS
    ):
        raise LocalSecSMonitorViewReadError("monitor receiver manifest is malformed")
    binding = value["policy_binding"]
    try:
        policy_binding = SecSMonitorViewReadPolicyBinding(
            policy_id=binding["policy_id"],
            policy_version=binding["policy_version"],
            policy_digest_sha256=binding["policy_digest_sha256"],
        )
    except (KeyError, TypeError, ValueError):
        raise LocalSecSMonitorViewReadError(
            "monitor receiver manifest is malformed"
        ) from None
    return _ReceiverManifest(
        stable_issuer=value["stable_issuer"],
        policy_binding=policy_binding,
    )


def load_local_secs_monitor_view_read_adapter(
    *,
    data_root: Path,
    storage: GraphStorage,
    audit_log: AuditLog,
    clock: Callable[[], int] | None = None,
) -> SecSMonitorViewReadAdapter | None:
    """Load the fixed public receiver bundle, or leave the route closed."""

    if not isinstance(data_root, Path) or not data_root.is_absolute():
        raise LocalSecSMonitorViewReadError("configured data root is invalid")
    try:
        bundle = require_receiver_directory_path(
            data_root,
            RECEIVER_BUNDLE_RELATIVE_PATH,
            missing_ok=True,
        )
    except LocalPathIntegrityError as error:
        raise LocalSecSMonitorViewReadError(str(error)) from None
    if bundle is None:
        return None
    manifest_raw = _read_public_bounded_file(
        bundle / RECEIVER_MANIFEST_NAME,
        label="monitor receiver manifest",
        maximum_bytes=RECEIVER_MANIFEST_MAX_BYTES,
    )
    registry_raw = _read_public_bounded_file(
        bundle / SECS_PUBLIC_KEY_REGISTRY_NAME,
        label="secS public key registry",
        maximum_bytes=SECS_PUBLIC_KEY_REGISTRY_MAX_BYTES,
    )
    manifest = _parse_receiver_manifest(manifest_raw)
    active_clock = clock or (lambda: int(time.time()))
    replay_store = DurableSecSMonitorViewReadReplayStore(
        _fixed_replay_store_path(data_root, bundle)
    )
    try:
        registry = SecSVerifierKeyRegistry.from_json(registry_raw)
        verifier = SecSMonitorViewReadVerifier(
            SecSMonitorViewReadVerifierConfig(
                audience=DEVGRAPH_MONITOR_VIEW_READ_AUDIENCE_V1,
                origin=DEVGRAPH_MONITOR_VIEW_READ_ORIGIN_V1,
                stable_issuer=manifest.stable_issuer,
                policy_binding=manifest.policy_binding,
                key_registry=registry,
                replay_cache=replay_store,
                clock=active_clock,
            )
        )
    except (TypeError, ValueError):
        raise LocalSecSMonitorViewReadError(
            "monitor receiver trust configuration is malformed"
        ) from None
    return SecSMonitorViewReadAdapter(
        verifier=verifier,
        storage=storage,
        audit_log=audit_log,
    )
