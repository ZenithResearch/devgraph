"""Explicit local session composition, never auto-selected from discovery.

Profiles/registration are application configuration, not Wallet backup contents.
Preparing registration reads metadata only; launching the explicitly reviewed
profile opens the existing canonical provider composition without a listener.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.holder_grants import HolderGrantHost, checksum_bytes
from devgraph.auth.session_access import SessionAccessProvider
from devgraph.disposable_sessions import SessionApplication, _write
from devgraph.local_host import load_local_config
from devgraph.signed_read_runtime import ReadRuntimeConfiguration, private_json


def file_pin(path):
    # Retain the invoking venv path; hashing resolves its interpreter symlink.
    path = Path(path).absolute()
    return dict(path=str(path), sha256=checksum_bytes(path))


def checked_file(pin):
    if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
        raise ValueError("invalid_session_file_pin")
    path = Path(pin["path"])
    if not path.is_absolute() or checksum_bytes(path) != pin["sha256"]:
        raise ValueError("session_provider_file_changed")
    return path


def configured_provider(runtime_file):
    config = ReadRuntimeConfiguration.model_validate(private_json(Path(runtime_file)))
    if (
        config.schema_ != "devgraph.signed-read-runtime.v11"
        or config.holder_grant_schema not in ("secs.holder-grant.v1", "secs.holder-grant.v2")
        or config.holder_grant_binary is None
        or not config.canonical_service_id
        or config.membership_requirement != "optional"
    ):
        raise ValueError("unsupported_local_session_runtime")
    # Session assignments belong to the canonical registry, never a route alias.
    selected = config.holder_grant_provider_runtime_file or config.owner_parent_runtime_file
    if selected is not None and selected.resolve() != Path(runtime_file).resolve():
        raise ValueError("canonical_provider_runtime_required")
    authority = private_json(config.authority_policy_file)
    receiver = {k: authority[k] for k in ("issuer", "key_id", "public_key", "receiver", "audience")}
    provider = {k: authority[k] for k in ("issuer", "key_id", "public_key")}
    provider["service"] = config.canonical_service_id
    return config, provider, receiver


def validate_profile(profile):
    if (
        set(profile)
        != {
            "schema",
            "mode",
            "name",
            "provider",
            "receiver_pin",
            "maximum_seconds",
            "host_config",
            "runtime_config",
            "session_binary",
            "session_grant_schema",
        }
        or profile["schema"] != "devgraph.local-session-provider.v1"
        or profile["mode"] != "production"
        or profile["session_grant_schema"] != "secs.holder-grant.v2"
        or not isinstance(profile["name"], str)
        or not 1 <= len(profile["name"]) <= 80
        or type(profile["maximum_seconds"]) is not int
        or not 60 <= profile["maximum_seconds"] <= 3600
    ):
        raise ValueError("invalid_local_session_profile")
    host = load_local_config(checked_file(profile["host_config"]))
    runtime = checked_file(profile["runtime_config"])
    checked_file(profile["session_binary"])
    config, provider, receiver = configured_provider(runtime)
    if provider != profile["provider"] or receiver != profile["receiver_pin"]:
        raise ValueError("session_provider_binding_changed")
    return host, config


def require_scoped_authority(holder):
    """Read-only native feature probe on synthetic resources, not graph data."""
    ref = dict(kind="Arena", id="session-capability-probe")
    result = holder.invoke(
        "check_selector",
        selector=dict(
            schema="secs.resource-selector.v1",
            service=holder.service,
            kinds=["Arena"],
            selection=dict(mode="objects", refs=[ref]),
        ),
        targets=[ref],
        snapshot=dict(
            service=holder.service,
            relation="devgraph.containment.v1",
            resources=[dict(resource=ref, ancestors=[])],
        ),
    )
    if (
        not isinstance(result, dict)
        or set(result) != {"allowed"}
        or not isinstance(result["allowed"], list)
        or len(result["allowed"]) != 1
        or result["allowed"][0] is not True
    ):
        raise ValueError("scoped_native_authority_required")


def prepare_registration(
    *,
    host_config,
    runtime_config,
    session_binary,
    interpreter,
    source_root,
    directory,
    name="DevGraph",
    maximum_seconds=3600,
):
    """Write NEW review artifacts only. Never install, open stores or issue grants."""
    _, provider, receiver = configured_provider(runtime_config)
    profile = dict(
        schema="devgraph.local-session-provider.v1",
        mode="production",
        name=name,
        provider=provider,
        receiver_pin=receiver,
        maximum_seconds=maximum_seconds,
        host_config=file_pin(host_config),
        runtime_config=file_pin(runtime_config),
        session_binary=file_pin(session_binary),
        session_grant_schema="secs.holder-grant.v2",
    )
    validate_profile(profile)  # metadata only; not create_app/enrollment/Neo4j
    root = Path(source_root).resolve(strict=True)
    script = root / "scripts/session_agent_service.py"
    sources = [file_pin(p) for p in sorted((root / "src/devgraph").rglob("*.py"))]
    sources += [
        file_pin(p)
        for p in (root / "uv.lock", Path(host_config), Path(runtime_config), Path(session_binary))
    ]
    if not 1 <= len(sources) < 1024 or len({p["path"] for p in sources}) != len(sources):
        raise ValueError("invalid_session_source_inventory")
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)  # exclusive; never overwrite installed configuration
    directory = directory.resolve(strict=True)
    profile_file = directory / "profile.json"
    _write(profile_file, profile)
    registration = dict(
        schema="zenith.session-provider-registration.v1",
        mode="production",
        provider=provider,
        receiver_pin=receiver,
        profile_digest_sha256=hashlib.sha256(canonical_json(profile)).hexdigest(),
        launch=dict(
            interpreter=file_pin(interpreter),
            script=file_pin(script),
            profile=file_pin(profile_file),
            sources=sources,
        ),
    )
    if len(canonical_json(registration)) > 524288:
        raise ValueError("session_registration_capacity")
    _write(directory / "provider.json", registration)
    return directory / "provider.json"


class LocalSessionApplication(SessionApplication):
    """Trusted local provider with existing operator/registry/receiver enforcement."""

    def __init__(self, profile_file):
        profile_file = Path(profile_file).absolute()
        profile = private_json(profile_file)
        info = profile_file.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("private_session_profile_directory_required")
        host, _ = validate_profile(profile)
        # Fixed server composition settings from the reviewed local host, never
        # caller-selected database credentials or inherited environment secrets.
        os.environ.pop("NEO4J_PASSWORD", None)
        os.environ.update(
            DEVGRAPH_DATA_ROOT=str(host.data_root),
            NEO4J_URI="bolt://127.0.0.1:7687",
            NEO4J_USER="neo4j",
            NEO4J_DATABASE="neo4j",
            NEO4J_PASSWORD_FILE=str(host.data_root / "secrets/neo4j_password"),
        )
        from devgraph.signed_read_runtime import create_app

        app = create_app(config_path=profile["runtime_config"]["path"], canonical_routes=False)
        self.storage = app.state.service_storage
        try:
            components = app.state.session_access_components
            holder = components.enrollment.holder_grants
            if holder.pin != profile["provider"]:
                raise ValueError("session_provider_binding_changed")
            if holder.grant_schema != profile["session_grant_schema"]:
                # Private process only: retain the exact existing issuer, binary,
                # provider/route registries and identities. Public host defaults
                # and every stored v1/v2 grant remain untouched.
                holder = HolderGrantHost(
                    enrollment=holder.enrollment,
                    provider_enrollment=holder.provider,
                    binary=holder.binary,
                    binary_sha256=holder.binary_sha256,
                    service=holder.service,
                    grant_schema=profile["session_grant_schema"],
                )
            require_scoped_authority(holder)
            self.clients, self.directory = {}, profile_file.parent
            self.holder, self.operator = holder, components.operator
            self.inbox, self.audit = components.operator.inbox, components.audit_log
            self.receiver_pin = profile["receiver_pin"]
            self.provider = SessionAccessProvider(
                operator=self.operator,
                holder=holder,
                profile_digest=hashlib.sha256(canonical_json(profile)).hexdigest(),
                maximum_seconds=profile["maximum_seconds"],
            )
            # Common application wire: no key/secret fields are exposed.
            self.profile = dict(
                profile, service=holder.service, binaries={"session": profile["session_binary"]}
            )
        except Exception:
            self.storage.close()
            raise

    def close(self):
        try:
            super().close()
        finally:
            self.storage.close()
