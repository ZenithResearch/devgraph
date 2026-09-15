"""Configurable local-only Devgraph host installation primitives.

The persisted configuration in this module is shared by the CLI, storage
provisioner, and launchd renderer. It deliberately contains no cloud-provider
or remote-deployment behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import secrets
import stat
import subprocess
import sys
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import uuid4

from devgraph.auth.credentials import CredentialEnvelope
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.local_read import (
    AUTH_MODE_LOCAL_READ,
    LOCAL_READ_CREDENTIAL_PREFIX_V1,
    LOCAL_READ_MAX_LIFETIME_SECONDS,
    LOCAL_READ_REGISTRY_RELATIVE_PATH,
    LOCAL_READ_REGISTRY_SCHEMA_V1,
    LocalReadCredentialConfigurationError,
    LocalReadCredentialVerifier,
)
from devgraph.auth.scopes import SCOPE_READ
from devgraph.ops.local_path_integrity import (
    LocalPathIntegrityError,
    require_file_descriptor_without_acl,
    require_receiver_directory_path,
)
from devgraph.ops.migrate import ManifestError, apply_migrations, load_manifest
from devgraph.storage.base import StorageUnavailable
from devgraph.storage.neo4j import Neo4jConfig, Neo4jGraphStorage, Neo4jMigrationStore

CONFIG_SCHEMA_VERSION = 1
NEO4J_VERSION = "5.26.29"
LEGACY_DATA_ROOT = Path("/Volumes/Devgraph-Data")
DEFAULT_HOST_ROOT = Path.home() / "Library" / "Application Support" / "Zenith" / "Devgraph"
DEFAULT_LOG_ROOT = Path.home() / "Library" / "Logs" / "Zenith" / "Devgraph"
DEFAULT_LAUNCH_AGENT_ROOT = Path.home() / "Library" / "LaunchAgents"
DEFAULT_CONFIG_PATH = DEFAULT_HOST_ROOT / "local.json"
DEFAULT_JAVA_HOME = Path("/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home")
DEFAULT_LOCAL_READ_CREDENTIAL_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_LOCAL_READ_ACTOR_ID = "local-devgraph-operator"
LOCAL_READ_CREDENTIAL_FILE_RELATIVE_PATH = Path(
    "devgraph/credentials/devgraph.read"
)
LOCAL_READ_MAX_CREDENTIAL_BYTES = 128
LOCAL_READ_AUDIENCE = "devgraph"
LOCAL_READ_ISSUER = "devgraph-local-operator"
MIGRATION_MANIFEST_PATH = Path(
    str(files("devgraph").joinpath("resources/migrations/manifest.json"))
)

SERVICE_LABELS = {
    "neo4j": "ca.zenith.devgraph.neo4j",
    "api": "ca.zenith.devgraph.api",
}


class LocalHostError(RuntimeError):
    """Safe base error for local host configuration and provisioning."""


class ConfigurationError(LocalHostError):
    """The persisted local host configuration is invalid or unsafe."""


class ProvisioningError(LocalHostError):
    """Local storage provisioning failed closed."""


class RenderError(LocalHostError):
    """Local launch-agent rendering or installation failed closed."""


def _absolute(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return expanded.resolve(strict=False)


def _lexical_absolute(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return Path(os.path.abspath(expanded))


def _containing_mount(path: Path) -> Path:
    candidate = path
    while candidate != candidate.parent and not candidate.is_mount():
        candidate = candidate.parent
    return candidate


def _validate_data_root(data_root: Path, *, require_mounted_volume: bool) -> Path:
    if not data_root.exists():
        raise ConfigurationError(f"data root does not exist: {data_root}")
    if not data_root.is_dir():
        raise ConfigurationError(f"data root is not a directory: {data_root}")
    broad_roots = {Path("/"), Path("/Volumes"), Path.home().resolve(strict=False)}
    if data_root in broad_roots:
        raise ConfigurationError("data root must be a dedicated directory")
    mount_root = _containing_mount(data_root) if require_mounted_volume else data_root
    if require_mounted_volume and mount_root == Path("/"):
        raise ConfigurationError("data root is not on a separately mounted volume")
    if not os.access(data_root, os.X_OK):
        raise ConfigurationError(f"data root is not accessible: {data_root}")
    if os.access(data_root, os.W_OK):
        return mount_root
    info = data_root.lstat()
    immutable_mount_root = (
        require_mounted_volume
        and data_root == mount_root
        and stat.S_ISDIR(info.st_mode)
        and info.st_uid == 0
        and info.st_gid == 0
        and stat.S_IMODE(info.st_mode) & 0o002 == 0
    )
    if not immutable_mount_root:
        raise ConfigurationError(f"data root is not writable: {data_root}")
    return mount_root


def _path_field(
    mapping: dict[str, Any],
    name: str,
    *,
    preserve_symlink: bool = False,
) -> Path:
    value = mapping.get(name)
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"local configuration field is invalid: {name}")
    path = Path(value)
    if not path.is_absolute():
        raise ConfigurationError(f"local configuration path is not absolute: {name}")
    return _lexical_absolute(path) if preserve_symlink else _absolute(path)


@dataclass(frozen=True)
class LocalHostConfig:
    """Versioned local runtime paths with no credential material."""

    data_root: Path
    availability_path: Path
    storage_mode: str
    host_root: Path
    log_root: Path
    launch_agent_root: Path
    neo4j_home: Path
    java_home: Path
    python_executable: Path
    schema_version: int = CONFIG_SCHEMA_VERSION

    @classmethod
    def build(
        cls,
        *,
        data_root: Path,
        host_root: Path = DEFAULT_HOST_ROOT,
        log_root: Path = DEFAULT_LOG_ROOT,
        launch_agent_root: Path = DEFAULT_LAUNCH_AGENT_ROOT,
        neo4j_home: Path | None = None,
        java_home: Path = DEFAULT_JAVA_HOME,
        python_executable: Path | None = None,
        require_mounted_volume: bool = False,
        validate_data_root: bool = True,
    ) -> LocalHostConfig:
        normalized_data_root = _absolute(data_root)
        if validate_data_root:
            availability_path = _validate_data_root(
                normalized_data_root,
                require_mounted_volume=require_mounted_volume,
            )
        elif require_mounted_volume:
            availability_path = _containing_mount(normalized_data_root)
        else:
            availability_path = normalized_data_root
        normalized_host_root = _absolute(host_root)
        default_neo4j_home = (
            normalized_host_root / "runtime" / "neo4j" / f"neo4j-community-{NEO4J_VERSION}"
        )
        return cls(
            data_root=normalized_data_root,
            availability_path=availability_path,
            storage_mode="mounted_volume" if require_mounted_volume else "directory",
            host_root=normalized_host_root,
            log_root=_absolute(log_root),
            launch_agent_root=_absolute(launch_agent_root),
            neo4j_home=_absolute(neo4j_home or default_neo4j_home),
            java_home=_absolute(java_home),
            python_executable=_lexical_absolute(python_executable or Path(sys.executable)),
        )

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> LocalHostConfig:
        if type(mapping.get("schema_version")) is not int:
            raise ConfigurationError("local configuration schema is invalid")
        if mapping["schema_version"] != CONFIG_SCHEMA_VERSION:
            raise ConfigurationError("unsupported local configuration schema")
        storage_mode = mapping.get("storage_mode")
        if storage_mode not in {"directory", "mounted_volume"}:
            raise ConfigurationError("local configuration storage mode is invalid")
        data_root = _path_field(mapping, "data_root")
        availability_path = _path_field(mapping, "availability_path")
        if storage_mode == "directory" and availability_path != data_root:
            raise ConfigurationError("directory availability path must equal data root")
        if storage_mode == "mounted_volume" and (
            availability_path == Path("/")
            or availability_path not in {data_root, *data_root.parents}
        ):
            raise ConfigurationError("mounted availability path must contain data root")
        return cls(
            data_root=data_root,
            availability_path=availability_path,
            storage_mode=storage_mode,
            host_root=_path_field(mapping, "host_root"),
            log_root=_path_field(mapping, "log_root"),
            launch_agent_root=_path_field(mapping, "launch_agent_root"),
            neo4j_home=_path_field(mapping, "neo4j_home"),
            java_home=_path_field(mapping, "java_home"),
            python_executable=_path_field(
                mapping,
                "python_executable",
                preserve_symlink=True,
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "storage_mode": self.storage_mode,
            "data_root": str(self.data_root),
            "availability_path": str(self.availability_path),
            "host_root": str(self.host_root),
            "log_root": str(self.log_root),
            "launch_agent_root": str(self.launch_agent_root),
            "neo4j_home": str(self.neo4j_home),
            "java_home": str(self.java_home),
            "python_executable": str(self.python_executable),
        }


def _read_regular_file(path: Path, *, description: str) -> bytes:
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise ConfigurationError(f"{description} is unavailable: {path}") from error
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ConfigurationError(f"{description} is not a regular file: {path}")
    if info.st_uid != os.getuid():
        raise ConfigurationError(f"{description} is not owned by the current user: {path}")
    return path.read_bytes()


def load_local_config(
    path: Path = DEFAULT_CONFIG_PATH,
    *,
    required: bool = True,
) -> LocalHostConfig | None:
    path = _absolute(path)
    try:
        path.lstat()
    except FileNotFoundError:
        if not required:
            return None
    raw = _read_regular_file(path, description="local configuration")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ConfigurationError(f"local configuration permissions are too broad: {path}")
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigurationError("local configuration is not valid JSON") from error
    if not isinstance(mapping, dict):
        raise ConfigurationError("local configuration root is invalid")
    return LocalHostConfig.from_mapping(mapping)


def _config_bytes(config: LocalHostConfig) -> bytes:
    return (json.dumps(config.to_mapping(), indent=2, sort_keys=True) + "\n").encode()


def _validate_replaceable_file(path: Path, content: bytes, *, replace: bool) -> str:
    try:
        path.lstat()
    except FileNotFoundError:
        return "created"
    existing = _read_regular_file(path, description="local configuration")
    info = path.stat()
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ConfigurationError(f"local configuration permissions are too broad: {path}")
    if existing == content:
        return "existing"
    if not replace:
        raise ConfigurationError(f"refusing to replace different local configuration: {path}")
    return "updated"


def write_local_config(
    config: LocalHostConfig,
    path: Path = DEFAULT_CONFIG_PATH,
    *,
    replace: bool = False,
) -> str:
    path = _absolute(path)
    content = _config_bytes(config)
    state = _validate_replaceable_file(path, content, replace=replace)
    if state == "existing":
        return state
    parent_existed = path.parent.exists()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not parent_existed:
        os.chmod(path.parent, 0o700)
    if state == "created":
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return state
    temporary = path.with_suffix(path.suffix + ".new")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return state


def _write_secret_once(path: Path) -> str:
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise ProvisioningError(f"secret path is not a regular file: {path}")
        if info.st_uid != os.getuid():
            raise ProvisioningError(f"secret is not owned by the current user: {path}")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise ProvisioningError(f"secret permissions are too broad: {path}")
        if not path.read_text(encoding="utf-8").strip():
            raise ProvisioningError(f"secret file is empty: {path}")
        return "existing"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_hex(32))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return "created"


def local_read_credential_paths(config: LocalHostConfig) -> tuple[Path, Path]:
    """Return the client secret and receiver registry paths without values."""

    return (
        config.data_root / LOCAL_READ_CREDENTIAL_FILE_RELATIVE_PATH,
        config.data_root / LOCAL_READ_REGISTRY_RELATIVE_PATH,
    )


def _read_local_read_credential_file(config: LocalHostConfig) -> str:
    try:
        directory = require_receiver_directory_path(
            config.data_root,
            LOCAL_READ_CREDENTIAL_FILE_RELATIVE_PATH.parent,
            missing_ok=False,
        )
        if directory is None:
            raise ProvisioningError("local read credential is unavailable")
        path = directory / LOCAL_READ_CREDENTIAL_FILE_RELATIVE_PATH.name
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
                raise ProvisioningError("local read credential is unsafe")
            require_file_descriptor_without_acl(descriptor)
            raw = os.read(descriptor, LOCAL_READ_MAX_CREDENTIAL_BYTES + 1)
        finally:
            os.close(descriptor)
    except (FileNotFoundError, LocalPathIntegrityError, OSError):
        raise ProvisioningError("local read credential is unavailable") from None
    if not raw or len(raw) > LOCAL_READ_MAX_CREDENTIAL_BYTES:
        raise ProvisioningError("local read credential is malformed")
    try:
        credential = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        raise ProvisioningError("local read credential is malformed") from None
    expected_length = len(LOCAL_READ_CREDENTIAL_PREFIX_V1) + 43
    if (
        len(credential) != expected_length
        or not credential.startswith(LOCAL_READ_CREDENTIAL_PREFIX_V1)
    ):
        raise ProvisioningError("local read credential is malformed")
    return credential


def read_local_read_credential(config: LocalHostConfig) -> str:
    """Explicitly reveal the local read capability for a caller/UI handoff."""

    return _read_local_read_credential_file(config)


def _new_local_read_credential_pair(
    *,
    actor_id: str,
    ttl_seconds: int,
    issued_at: int,
) -> tuple[bytes, bytes]:
    if (
        type(ttl_seconds) is not int
        or ttl_seconds < 300
        or ttl_seconds > LOCAL_READ_MAX_LIFETIME_SECONDS
        or type(issued_at) is not int
        or issued_at < 0
    ):
        raise ProvisioningError("local read credential lifetime is invalid")
    session_id = f"local-read-{secrets.token_hex(16)}"
    correlation_id = f"dg:read:{secrets.token_hex(16)}"
    expires_at = issued_at + ttl_seconds
    try:
        CredentialEnvelope(
            actor_id=actor_id,
            session_id=session_id,
            correlation_id=correlation_id,
            scopes=frozenset({SCOPE_READ}),
            expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc),
            issuer=LOCAL_READ_ISSUER,
            audience=LOCAL_READ_AUDIENCE,
        )
    except (OSError, OverflowError, TypeError, ValueError) as error:
        raise ProvisioningError("local read credential authority is invalid") from error
    token = LOCAL_READ_CREDENTIAL_PREFIX_V1 + urlsafe_b64encode(
        secrets.token_bytes(32)
    ).decode("ascii").rstrip("=")
    registry = {
        "actor_id": actor_id,
        "audience": LOCAL_READ_AUDIENCE,
        "correlation_id": correlation_id,
        "credential_digest_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
        "expires_at": expires_at,
        "issued_at": issued_at,
        "issuer": LOCAL_READ_ISSUER,
        "schema": LOCAL_READ_REGISTRY_SCHEMA_V1,
        "schema_version": 1,
        "session_id": session_id,
    }
    registry_bytes = json.dumps(
        registry,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return token.encode("ascii") + b"\n", registry_bytes


def provision_local_read_credential(
    config: LocalHostConfig,
    *,
    actor_id: str = DEFAULT_LOCAL_READ_ACTOR_ID,
    ttl_seconds: int = DEFAULT_LOCAL_READ_CREDENTIAL_TTL_SECONDS,
    replace: bool = False,
    now: int | None = None,
) -> str:
    """Create or rotate one read-only local capability and digest registry."""

    credential_path, registry_path = local_read_credential_paths(config)
    credential_exists = credential_path.exists()
    registry_exists = registry_path.exists()
    if credential_exists and registry_exists and not replace:
        credential = _read_local_read_credential_file(config)
        try:
            LocalReadCredentialVerifier(
                data_root=config.data_root,
                audience=LOCAL_READ_AUDIENCE,
            ).verify(
                credential,
                audience=LOCAL_READ_AUDIENCE,
            )
        except (LocalReadCredentialConfigurationError, UnauthenticatedError):
            raise ProvisioningError(
                "existing local read credential is invalid or expired"
            ) from None
        return "existing"
    if credential_exists != registry_exists and not replace:
        raise ProvisioningError("local read credential state is incomplete")

    credential_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    registry_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(credential_path.parent, 0o700)
    os.chmod(registry_path.parent, 0o700)
    token_bytes, registry_bytes = _new_local_read_credential_pair(
        actor_id=actor_id,
        ttl_seconds=ttl_seconds,
        issued_at=int(time.time()) if now is None else now,
    )
    registry_state = "updated" if registry_exists else "created"
    credential_state = "updated" if credential_exists else "created"
    _atomic_write(registry_path, registry_bytes, mode=0o600, state=registry_state)
    _atomic_write(credential_path, token_bytes, mode=0o600, state=credential_state)
    return "rotated" if replace and (credential_exists or registry_exists) else "created"


def local_read_credential_status(config: LocalHostConfig) -> dict[str, Any]:
    """Return a secret-free validity projection for the local capability."""

    credential_path, registry_path = local_read_credential_paths(config)
    credential = _read_local_read_credential_file(config)
    try:
        context = LocalReadCredentialVerifier(
            data_root=config.data_root,
            audience=LOCAL_READ_AUDIENCE,
        ).verify(
            credential,
            audience=LOCAL_READ_AUDIENCE,
        )
    except (LocalReadCredentialConfigurationError, UnauthenticatedError):
        raise ProvisioningError("local read credential is invalid or expired") from None
    envelope = context.envelope
    return {
        "actor_id": envelope.actor_id,
        "audience": envelope.audience,
        "credential_path": str(credential_path),
        "expires_at": envelope.expires_at.isoformat(),
        "registry_path": str(registry_path),
        "scope": SCOPE_READ,
        "valid": True,
    }


def _render_neo4j_config(data_root: Path) -> str:
    template = files("devgraph").joinpath("resources/neo4j.conf.in").read_text(encoding="utf-8")
    return template.replace("__DATA_ROOT__", str(data_root))


def _write_config_once(path: Path, content: str) -> str:
    if path.exists():
        if not path.is_file() or path.is_symlink():
            raise ProvisioningError(f"configuration path is not a regular file: {path}")
        if path.read_text(encoding="utf-8") != content:
            raise ProvisioningError(f"existing Neo4j configuration differs: {path}")
        return "existing"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    return "created"


def _directory_has_entries(path: Path) -> bool:
    return path.is_dir() and next(path.iterdir(), None) is not None


def provision_storage(
    data_root: Path,
    *,
    require_mounted_volume: bool = False,
) -> tuple[str, str]:
    data_root = _absolute(data_root)
    _validate_data_root(data_root, require_mounted_volume=require_mounted_volume)
    data_directory = data_root / "neo4j" / "data"
    secret_path = data_root / "secrets" / "neo4j_password"
    if not secret_path.exists() and _directory_has_entries(data_directory):
        raise ProvisioningError("existing Neo4j data has no local credential file")
    private_directories = (
        data_root / "secrets",
        data_root / "backups",
        data_root / "devgraph" / "logs",
        data_root / "neo4j" / "certificates",
        data_root / "neo4j" / "conf",
        data_directory,
        data_root / "neo4j" / "import",
        data_root / "neo4j" / "logs",
        data_root / "neo4j" / "plugins",
        data_root / "neo4j" / "run",
        data_root / "neo4j" / "transactions",
    )
    for directory in private_directories:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    secret_state = _write_secret_once(secret_path)
    config_state = _write_config_once(
        data_root / "neo4j" / "conf" / "neo4j.conf",
        _render_neo4j_config(data_root),
    )
    return secret_state, config_state


def runtime_checks(config: LocalHostConfig) -> dict[str, bool]:
    """Return bounded readiness checks for local runtime artifacts."""

    def executable(path: Path) -> bool:
        return path.is_file() and os.access(path, os.X_OK)

    return {
        "availability_path": config.availability_path.exists(),
        "data_root": config.data_root.is_dir(),
        "java": executable(config.java_home / "bin" / "java"),
        "neo4j": executable(config.neo4j_home / "bin" / "neo4j"),
        "neo4j_admin": executable(config.neo4j_home / "bin" / "neo4j-admin"),
        "python": executable(config.python_executable),
    }


def _require_runtime(config: LocalHostConfig) -> None:
    checks = runtime_checks(config)
    missing = [name for name, available in checks.items() if not available]
    if missing:
        raise ProvisioningError(
            "required local runtime artifacts are unavailable: " + ", ".join(missing)
        )


def initialize_initial_password(
    config: LocalHostConfig,
    *,
    runner=subprocess.run,
) -> str:
    """Install the generated Neo4j password before the first database start."""

    data_directory = config.data_root / "neo4j" / "data"
    if _directory_has_entries(data_directory):
        return "existing"
    secret_path = config.data_root / "secrets" / "neo4j_password"
    try:
        password = (
            _read_regular_file(
                secret_path,
                description="Neo4j credential",
            )
            .decode("utf-8")
            .strip()
        )
    except UnicodeDecodeError as error:
        raise ProvisioningError("Neo4j credential is not valid text") from error
    if not password:
        raise ProvisioningError("Neo4j credential file is empty")
    environment = os.environ.copy()
    environment["JAVA_HOME"] = str(config.java_home)
    environment["NEO4J_CONF"] = str(config.data_root / "neo4j" / "conf")
    try:
        completed = runner(
            [
                str(config.neo4j_home / "bin" / "neo4j-admin"),
                "dbms",
                "set-initial-password",
                "--require-password-change=false",
                password,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    except OSError as error:
        raise ProvisioningError("initial Neo4j credential setup could not run") from error
    if completed.returncode != 0:
        raise ProvisioningError(
            "initial Neo4j credential setup failed; credential value was not displayed"
        )
    return "initialized"


def _agent(
    *,
    label: str,
    arguments: list[str],
    environment: dict[str, str],
    working_directory: Path,
    availability_path: Path,
    log_prefix: Path,
) -> dict[str, object]:
    return {
        "Label": label,
        "ProgramArguments": arguments,
        "EnvironmentVariables": environment,
        "WorkingDirectory": str(working_directory),
        "RunAtLoad": True,
        "KeepAlive": {"PathState": {str(availability_path): True}},
        "ThrottleInterval": 15,
        "ProcessType": "Background",
        "StandardOutPath": str(log_prefix.with_suffix(".out.log")),
        "StandardErrorPath": str(log_prefix.with_suffix(".err.log")),
    }


def render_agents(config: LocalHostConfig) -> dict[str, bytes]:
    """Render package-installed launch agents from one local configuration."""

    neo4j = _agent(
        label=SERVICE_LABELS["neo4j"],
        arguments=[str(config.neo4j_home / "bin" / "neo4j"), "console"],
        environment={
            "JAVA_HOME": str(config.java_home),
            "NEO4J_CONF": str(config.data_root / "neo4j" / "conf"),
        },
        working_directory=config.neo4j_home,
        availability_path=config.availability_path,
        log_prefix=config.log_root / "launchd-neo4j",
    )
    api = _agent(
        label=SERVICE_LABELS["api"],
        arguments=[
            str(config.python_executable),
            "-m",
            "uvicorn",
            "devgraph.runtime:create_production_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8080",
        ],
        environment={
            "DEVGRAPH_AUDIENCE": "devgraph",
            "DEVGRAPH_AUTH_MODE": AUTH_MODE_LOCAL_READ,
            "DEVGRAPH_DATA_ROOT": str(config.data_root),
            "DEVGRAPH_ENVIRONMENT": "production",
            "NEO4J_DATABASE": "neo4j",
            "NEO4J_PASSWORD_FILE": str(config.data_root / "secrets" / "neo4j_password"),
            "NEO4J_URI": "bolt://127.0.0.1:7687",
            "NEO4J_USER": "neo4j",
            "PYTHONUNBUFFERED": "1",
        },
        working_directory=config.host_root,
        availability_path=config.availability_path,
        log_prefix=config.log_root / "launchd-api",
    )
    return {
        f"{SERVICE_LABELS['neo4j']}.plist": plistlib.dumps(neo4j, sort_keys=True),
        f"{SERVICE_LABELS['api']}.plist": plistlib.dumps(api, sort_keys=True),
    }


def _validate_agent_target(path: Path, content: bytes, *, replace: bool) -> str:
    try:
        existing_bytes = path.read_bytes()
    except FileNotFoundError:
        return "created"
    if not path.is_file() or path.is_symlink():
        raise RenderError(f"launch agent is not a regular file: {path}")
    if existing_bytes == content:
        return "existing"
    if not replace:
        raise RenderError(f"refusing to replace different launch agent: {path}")
    try:
        existing = plistlib.loads(existing_bytes)
    except plistlib.InvalidFileException as error:
        raise RenderError(f"existing launch agent is not a plist: {path}") from error
    if existing.get("Label") != path.stem:
        raise RenderError(f"existing launch agent label differs: {path}")
    return "updated"


def _atomic_write(path: Path, content: bytes, *, mode: int, state: str) -> None:
    if state == "existing":
        return
    if state == "created":
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        return
    temporary = path.with_suffix(path.suffix + ".new")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def install_agents(
    config: LocalHostConfig,
    agents: dict[str, bytes],
    *,
    replace: bool = False,
) -> dict[str, str]:
    """Install only launch agents with the expected stable labels."""

    expected_names = {f"{label}.plist" for label in SERVICE_LABELS.values()}
    if set(agents) != expected_names:
        raise RenderError("rendered launch agent set is invalid")
    config.launch_agent_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    states = {
        name: _validate_agent_target(
            config.launch_agent_root / name,
            content,
            replace=replace,
        )
        for name, content in agents.items()
    }
    for name, content in agents.items():
        _atomic_write(
            config.launch_agent_root / name,
            content,
            mode=0o644,
            state=states[name],
        )
    return states


def config_snapshot(
    config: LocalHostConfig,
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, Any]:
    """Return a credential-free local configuration projection."""

    return {
        "config_path": str(_absolute(config_path)),
        "configuration": config.to_mapping(),
        "runtime": runtime_checks(config),
    }


def configure_local_host(
    config: LocalHostConfig,
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    replace: bool = False,
    password_runner=subprocess.run,
) -> dict[str, Any]:
    """Provision a complete local host without loading or starting services."""

    config_path = _absolute(config_path)
    config_state = _validate_replaceable_file(
        config_path,
        _config_bytes(config),
        replace=replace,
    )
    _validate_data_root(
        config.data_root,
        require_mounted_volume=config.storage_mode == "mounted_volume",
    )
    _require_runtime(config)
    agents = render_agents(config)
    for name, content in agents.items():
        _validate_agent_target(
            config.launch_agent_root / name,
            content,
            replace=replace,
        )

    config.host_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_root_existed = config.log_root.exists()
    config.log_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not log_root_existed:
        os.chmod(config.log_root, 0o700)
    storage_secret_state, storage_config_state = provision_storage(
        config.data_root,
        require_mounted_volume=config.storage_mode == "mounted_volume",
    )
    read_credential_state = provision_local_read_credential(config)
    password_state = initialize_initial_password(config, runner=password_runner)
    agent_states = install_agents(config, agents, replace=replace)
    if config_state != "existing":
        config_state = write_local_config(
            config,
            config_path,
            replace=replace,
        )
    return {
        "config": config_state,
        "config_path": str(config_path),
        "launch_agents": agent_states,
        "neo4j_configuration": storage_config_state,
        "neo4j_credential": storage_secret_state,
        "neo4j_initial_password": password_state,
        "read_credential": read_credential_state,
        "read_credential_path": str(local_read_credential_paths(config)[0]),
        "ready_to_start": True,
    }


def apply_local_migrations(
    config: LocalHostConfig,
    *,
    timeout_seconds: float = 120.0,
    clock=time.monotonic,
    sleeper=time.sleep,
) -> dict[str, Any]:
    """Wait for the loopback database and apply the bundled forward migrations."""

    secret_path = config.data_root / "secrets" / "neo4j_password"
    try:
        password = (
            _read_regular_file(
                secret_path,
                description="Neo4j credential",
            )
            .decode("utf-8")
            .strip()
        )
    except UnicodeDecodeError as error:
        raise ProvisioningError("Neo4j credential is not valid text") from error
    if not password:
        raise ProvisioningError("Neo4j credential file is empty")
    try:
        manifest = load_manifest(MIGRATION_MANIFEST_PATH)
        storage = Neo4jGraphStorage(
            Neo4jConfig(
                uri="bolt://127.0.0.1:7687",
                user="neo4j",
                password=password,
                database="neo4j",
            )
        )
    except (ManifestError, StorageUnavailable) as error:
        raise ProvisioningError("local migration runtime is unavailable") from error
    try:
        deadline = clock() + timeout_seconds
        while not storage.health().ready:
            if clock() >= deadline:
                raise ProvisioningError("Neo4j did not become ready before the timeout")
            sleeper(0.5)
        status = apply_migrations(
            manifest,
            Neo4jMigrationStore(storage),
            attempt_id=uuid4().hex,
        )
        if not status.ready:
            raise ProvisioningError(f"local migrations stopped safely: {status.reason}")
        return status.safe_output()
    finally:
        storage.close()
