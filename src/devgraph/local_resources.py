"""Advisory public discovery records; never credentials or proof of ownership."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from devgraph.local_host import LocalHostError

SCHEMA = "zenith.local-resource.v1"
MAX_BYTES = 32_768
MAX_RECORDS = 100
REGISTRATION_NAME = "resource-registration.json"


class LocalResourceError(LocalHostError):
    def __init__(self) -> None:
        super().__init__("local_resource_invalid_or_changed")


def _require(condition: bool) -> None:
    if not condition:
        raise LocalResourceError()


def _text(value, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and 0 < len(value) <= maximum
        and all(ord(c) >= 32 and ord(c) != 127 for c in value)
    )


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _decode(raw: bytes) -> dict:
    _require(0 < len(raw) <= MAX_BYTES)
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique,
            parse_float=lambda _: (_ for _ in ()).throw(LocalResourceError()),
            parse_constant=lambda _: (_ for _ in ()).throw(LocalResourceError()),
        )
        _require(isinstance(value, dict))
        return value
    except (ValueError, UnicodeError, TypeError) as error:
        raise LocalResourceError() from error


def _origin(value, *, connection: bool = False) -> str:
    _require(
        _text(value, 2048)
        and value.isascii()
        and not any(c.isspace() for c in value)
        and "\\" not in value
    )
    try:
        url = urlsplit(value)
        _require(
            url.hostname is not None
            and url.username is None
            and url.password is None
            and not url.query
            and not url.fragment
            and url.path == ""
            and (url.port is None or 1 <= url.port <= 65535)
            and (
                url.scheme == "https"
                or (
                    not connection
                    and url.scheme == "http"
                    and url.hostname in {"127.0.0.1", "::1", "localhost"}
                )
            )
        )
    except ValueError as error:
        raise LocalResourceError() from error
    return value


def validate_connection(value: dict) -> dict:
    _require(
        isinstance(value, dict)
        and {"name", "receiver", "issuer", "authority"} <= value.keys()
        and value.keys()
        <= {"name", "receiver", "issuer", "authority", "network", "tls_certificate_sha256"}
    )
    _require(_text(value["name"], 80) and _text(value["issuer"], 256))
    origin = _origin(value["receiver"], connection=True)
    pin = value["authority"]
    _require(
        isinstance(pin, dict)
        and pin.keys() == {"issuer", "key_id", "public_key", "receiver", "audience"}
    )
    _require(
        pin["issuer"] == value["issuer"]
        and pin["receiver"] == origin
        and pin["audience"] == "devgraph"
        and _text(pin["key_id"], 256)
        and isinstance(pin["public_key"], str)
        and re.fullmatch("[a-f0-9]{64}", pin["public_key"]) is not None
    )
    _require(value.get("network") in (None, "lan", "tailscale", "local"))
    fingerprint = value.get("tls_certificate_sha256")
    _require(
        (isinstance(fingerprint, str) and re.fullmatch("[a-f0-9]{64}", fingerprint) is not None)
        if value.get("network") in ("lan", "local")
        else fingerprint is None
    )
    if value.get("network") == "local":
        _require(urlsplit(origin).hostname == "127.0.0.1")
    return value


def validate_record(value: dict) -> dict:
    _require(
        value.keys()
        == {"schema", "resource_id", "kind", "name", "endpoint", "connection", "updated_at"}
    )
    _require(value["schema"] == SCHEMA and value["kind"] == "devgraph" and _text(value["name"], 80))
    try:
        _require(str(UUID(value["resource_id"])) == value["resource_id"])
    except (ValueError, TypeError, AttributeError) as error:
        raise LocalResourceError() from error
    _origin(value["endpoint"])
    _require(type(value["updated_at"]) is int and 0 <= value["updated_at"] <= 9_007_199_254_740_991)
    if value["connection"] is not None:
        connection = validate_connection(value["connection"])
        _require(
            connection["receiver"] == value["endpoint"] and connection["name"] == value["name"]
        )
    return value


def _read(path: Path, *, owned: bool = True) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        _require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_BYTES)
        if owned:
            _require(info.st_uid == os.getuid() and info.st_mode & 0o077 == 0)
        return _decode(os.read(fd, MAX_BYTES + 1))
    finally:
        os.close(fd)


def _directory(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o700)
    info = path.lstat()
    _require(
        stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0
    )


def _registry(home: Path) -> Path:
    current = home
    for part in (".zenith", "resources", "v1"):
        current = current / part
        _directory(current)
    return current


def _write(path: Path, value: dict) -> None:
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _require(len(raw) <= MAX_BYTES)
    temporary = path.with_name("." + uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def _locked(config_path: Path):
    # This is public discovery state beside the existing instance configuration.
    # It neither edits that configuration nor reads its secrets.
    _require(config_path.is_absolute() and config_path.parent.is_dir())
    parent = config_path.parent.lstat()
    _require(
        stat.S_ISDIR(parent.st_mode)
        and parent.st_uid == os.getuid()
        and parent.st_mode & 0o022 == 0
    )
    fd = os.open(
        config_path.with_name(".resource-registration.lock"),
        os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    try:
        info = os.fstat(fd)
        _require(
            stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_mode & 0o077 == 0
        )
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _publish(record: dict, home: Path) -> Path:
    directory = _registry(home)
    target = directory / (record["resource_id"] + ".json")
    if target.exists() or target.is_symlink():
        old = validate_record(_read(target))
        _require(old["resource_id"] == record["resource_id"])
    else:
        _require(len(list(directory.glob("*.json"))) < MAX_RECORDS)
    _write(target, record)
    return target


def register_resource(
    config_path: Path,
    *,
    connection_file: Path | None = None,
    name: str = "DevGraph on this Mac",
    replace: bool = False,
    home: Path | None = None,
    expected_registration_sha256: str | None = None,
) -> dict:
    home = home or Path.home()
    try:
        with _locked(config_path):
            state = config_path.with_name(REGISTRATION_NAME)
            old = validate_record(_read(state)) if state.exists() or state.is_symlink() else None
            if expected_registration_sha256 is not None:
                import hashlib

                _require(re.fullmatch("[a-f0-9]{64}", expected_registration_sha256) is not None)
                _require(
                    old is not None
                    and hashlib.sha256(state.read_bytes()).hexdigest()
                    == expected_registration_sha256
                )
            connection = (
                validate_connection(_read(connection_file, owned=False))
                if connection_file
                else None
            )
            record = dict(
                schema=SCHEMA,
                resource_id=old["resource_id"] if old else str(uuid4()),
                kind="devgraph",
                name=connection["name"] if connection else name,
                endpoint=connection["receiver"] if connection else "http://127.0.0.1:8080",
                connection=connection,
                updated_at=int(time.time()),
            )
            validate_record(record)
            _require(
                old is None
                or replace
                or all(old[key] == record[key] for key in record if key != "updated_at")
            )
            _write(state, record)  # Persist the identifier before publication; retry reuses it.
            target = _publish(record, home)
            return dict(
                published=True,
                resource_id=record["resource_id"],
                record_path=str(target),
                authority_granted=False,
            )
    except (OSError, ValueError, TypeError) as error:
        raise LocalResourceError() from error


def refresh_resource(config_path: Path, *, home: Path | None = None) -> dict:
    try:
        with _locked(config_path):
            state = config_path.with_name(REGISTRATION_NAME)
            if not state.exists() and not state.is_symlink():
                return dict(published=False, reason="not_registered")
            record = validate_record(_read(state))
            record["updated_at"] = int(time.time())
            target = _publish(record, home or Path.home())
            return dict(
                published=True,
                resource_id=record["resource_id"],
                record_path=str(target),
                authority_granted=False,
            )
    except (LocalResourceError, OSError, ValueError, TypeError):
        # A discovery cache failure never changes whether the server started.
        return dict(published=False, reason="local_resource_invalid_or_changed")
