"""Advisory service-instance discovery, separate from the graph identity record.

Publishing never launches a listener, transfers ownership, changes a graph's
selected API, or grants authority. Runtime probes remain necessary after restart.
"""

from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid5

from devgraph.local_resources import (
    LocalResourceError,
    _directory,
    _locked,
    _read,
    _require,
    _write,
    validate_connection,
    validate_record,
)

SCHEMA = "zenith.local-service.v1"
FIELDS = {
    "schema",
    "instance_id",
    "resource_id",
    "kind",
    "interface",
    "name",
    "endpoint",
    "connection",
    "updated_at",
}


def instance_id(resource_id: str, interface: str, endpoint: str) -> str:
    _require(interface in ("private-api", "wallet-api"))
    return str(uuid5(UUID(resource_id), interface + "\n" + endpoint))


def validate_service(value: dict) -> dict:
    _require(isinstance(value, dict) and value.keys() == FIELDS)
    # Reuse the exact closed resource/profile validation; IDs are additional,
    # not a replacement graph identity or an implication of Wallet authority.
    validate_record(
        {
            k: value[k]
            for k in ("resource_id", "kind", "name", "endpoint", "connection", "updated_at")
        }
        | {"schema": "zenith.local-resource.v1"}
    )
    _require(value["schema"] == SCHEMA)
    _require(
        value["instance_id"]
        == instance_id(value["resource_id"], value["interface"], value["endpoint"])
    )
    if value["interface"] == "private-api":
        origin = urlsplit(value["endpoint"])
        _require(
            value["connection"] is None
            and origin.scheme == "http"
            and origin.hostname == "127.0.0.1"
            and origin.port is not None
            and value["endpoint"] == f"http://127.0.0.1:{origin.port}"
        )
    else:
        _require(value["connection"] is not None)
        validate_connection(value["connection"])
    return value


def directory(home: Path) -> Path:
    path = home
    for part in (".zenith", "services", "v1"):
        path /= part
        _directory(path)
    return path


def publish_service(
    config_path: Path,
    *,
    interface: str,
    endpoint: str,
    connection: dict | None = None,
    home: Path | None = None,
    now: int,
    expected_registration_sha256: str | None = None,
) -> dict:
    import hashlib

    home = home or Path.home()
    with _locked(config_path):
        registration = config_path.with_name("resource-registration.json")
        graph = validate_record(_read(registration))
        if expected_registration_sha256 is not None:
            _require(
                hashlib.sha256(registration.read_bytes()).hexdigest()
                == expected_registration_sha256
            )
        record = validate_service(
            dict(
                schema=SCHEMA,
                instance_id=instance_id(graph["resource_id"], interface, endpoint),
                resource_id=graph["resource_id"],
                kind="devgraph",
                interface=interface,
                name=graph["name"],
                endpoint=endpoint,
                connection=connection,
                updated_at=now,
            )
        )
        root = directory(home)
        path = root / (record["instance_id"] + ".json")
        if path.exists() or path.is_symlink():
            old = validate_service(_read(path))
            _require(old["instance_id"] == record["instance_id"])
        else:
            _require(len(list(root.glob("*.json"))) < 100)
        _write(path, record)
        return dict(
            published=True,
            instance_id=record["instance_id"],
            resource_id=record["resource_id"],
            endpoint=record["endpoint"],
            authority_granted=False,
            graph_registration_changed=False,
        )


def refresh_private_service(config_path: Path, *, home: Path | None = None) -> dict:
    import time

    try:
        record = validate_record(_read(config_path.with_name("resource-registration.json")))
        # Configured private host's actual API address. Never reuse an unrelated
        # Wallet receiver which an older registry writer may have advertised.
        if record["connection"] is not None:
            return dict(published=False, reason="private_api_registration_requires_review")
        return publish_service(
            config_path,
            interface="private-api",
            endpoint=record["endpoint"],
            home=home,
            now=int(time.time()),
        )
    except (LocalResourceError, OSError, ValueError, TypeError):
        return dict(published=False, reason="local_service_invalid_or_changed")
