"""Explicit pinned local composition for member-grant sessions, separate from owner flow."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from devgraph.auth.holder_grants import checksum
from devgraph.auth.holder_sessions import HolderSessionPolicy
from devgraph.disposable_sessions import _write
from devgraph.holder_session_provider import HolderSessionApplication
from devgraph.local_host import load_local_config
from devgraph.local_session_provider import checked_file, file_pin
from devgraph.signed_read_runtime import ReadRuntimeConfiguration, private_json


def configured_provider(runtime_file):
    config = ReadRuntimeConfiguration.model_validate(private_json(Path(runtime_file)))
    if (
        config.schema_ != "devgraph.signed-read-runtime.v11"
        or config.holder_grant_binary is None
        or config.holder_session_policy_file is None
        or not config.canonical_service_id
    ):
        raise ValueError("explicit_holder_session_runtime_required")
    selected = config.holder_grant_provider_runtime_file or config.owner_parent_runtime_file
    if selected is not None and selected.resolve() != Path(runtime_file).resolve():
        raise ValueError("canonical_holder_session_runtime_required")
    policy = HolderSessionPolicy.model_validate(private_json(config.holder_session_policy_file))
    if not policy.enabled:
        raise ValueError("holder_sessions_not_enabled")
    authority = private_json(config.authority_policy_file)
    provider = {k: authority[k] for k in ("issuer", "key_id", "public_key")}
    provider["service"] = config.canonical_service_id
    return config, provider, policy


def validate_profile(profile):
    if (
        not isinstance(profile, dict)
        or set(profile)
        != {"schema", "mode", "name", "provider", "context", "host_config", "runtime_config"}
        or profile["schema"] != "devgraph.holder-session-provider-profile.v1"
        or profile["mode"] != "production"
        or not isinstance(profile["name"], str)
        or not 1 <= len(profile["name"]) <= 80
        or not isinstance(profile["context"], dict)
        or set(profile["context"]) != {"origin", "audience"}
    ):
        raise ValueError("invalid_holder_session_profile")
    host = load_local_config(checked_file(profile["host_config"]))
    config, provider, policy = configured_provider(checked_file(profile["runtime_config"]))
    if (
        provider != profile["provider"]
        or profile["context"]["origin"] not in policy.allowed_origins
        or profile["context"]["audience"] != policy.audience
    ):
        raise ValueError("holder_session_provider_binding_changed")
    return host, config, policy


def prepare_registration(
    *,
    host_config,
    runtime_config,
    agent_binary,
    interpreter,
    source_root,
    directory,
    context,
    name="DevGraph",
):
    """Create review files exclusively; no service start, store open or grant issue."""
    config, provider, policy = configured_provider(runtime_config)
    profile = {
        "schema": "devgraph.holder-session-provider-profile.v1",
        "mode": "production",
        "name": name,
        "provider": provider,
        "context": context,
        "host_config": file_pin(host_config),
        "runtime_config": file_pin(runtime_config),
    }
    validate_profile(profile)
    root = Path(source_root).resolve(strict=True)
    sources = [
        file_pin(p)
        for p in sorted((root / "src/devgraph").rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    ]
    extra = [
        root / "uv.lock",
        Path(host_config),
        Path(runtime_config),
        config.holder_session_policy_file,
        config.authority_policy_file,
        config.holder_grant_binary,
    ]
    venv = Path(interpreter).absolute().parent.parent / "pyvenv.cfg"
    if venv.exists():
        extra.append(venv)
    # Shared paths can coincide, e.g. config and canonical provider references.
    sources = list({p["path"]: p for p in sources + [file_pin(p) for p in extra]}.values())
    if not 1 <= len(sources) < 1024:
        raise ValueError("holder_session_source_capacity")
    launch = {
        "interpreter": file_pin(interpreter),
        "script": file_pin(root / "scripts/holder_session_provider.py"),
        "sources": sources,
    }
    agent_pin = file_pin(agent_binary)
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)  # Never overwrite an installed registration.
    directory = directory.resolve(strict=True)
    _write(directory / "profile.json", profile)
    _write(
        directory / "agent.json",
        {
            "schema": "castalia.holder-session-agent-profile.v1",
            "provider": provider,
            "context": context,
            "maximum_session_seconds": policy.max_ttl_seconds,
        },
    )
    launch["profile"] = file_pin(directory / "profile.json")
    descriptor = {
        "schema": "zenith.holder-session-provider-registration.v1",
        "mode": "production",
        "provider": provider,
        "context": context,
        "profile_digest_sha256": checksum(profile),
        "launch": launch,
        "agent": {"binary": agent_pin, "profile": file_pin(directory / "agent.json")},
    }
    from devgraph.auth.delegated_contract import canonical_json

    if len(canonical_json(descriptor)) > 524288:
        raise ValueError("holder_session_registration_capacity")
    _write(directory / "holder-session-provider.json", descriptor)
    return directory / "holder-session-provider.json"


class LocalHolderSessionApplication(HolderSessionApplication):
    def __init__(self, profile_file):
        profile_file = Path(profile_file).absolute()
        profile = private_json(profile_file)
        info = profile_file.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("private_holder_session_directory_required")
        host, _, _ = validate_profile(profile)
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
        storage = app.state.service_storage
        try:
            holder = app.state.session_access_components.enrollment.holder_grants
            if holder.pin != profile["provider"]:
                raise ValueError("holder_session_provider_binding_changed")
            original = checksum(profile)

            def unchanged():
                current = private_json(profile_file)
                validate_profile(current)
                return checksum(current) == original

            super().__init__(
                sessions=holder.holder_sessions,
                storage=storage,
                audit_log=app.state.session_access_components.audit_log,
                name=profile["name"],
                context=profile["context"],
                check=unchanged,
            )
        except Exception:
            storage.close()
            raise
