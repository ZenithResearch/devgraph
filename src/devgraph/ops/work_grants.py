"""Owner-local named Work grants. Wallet and secS retain their private keys."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from devgraph.auth.secs_issue_create import SecSVerifierKeyRegistry
from devgraph.local_host import DEFAULT_CONFIG_PATH, LocalHostError, load_local_config
from devgraph.ops import signer_profile
from devgraph.ops.local_path_integrity import (
    LocalPathIntegrityError,
    require_file_descriptor_without_acl,
    require_receiver_directory_path,
)
from devgraph.ops.secs_issue_create_receiver import (
    LocalSecSIssueCreateError,
    _read_private_bounded_file,
    _strict_json_object,
)
from devgraph.ops.secs_issue_create_wallet import (
    LocalSecSWalletIssueCreateError,
    _close_wallet_binary_descriptors,
    _require_fixed_executable,
    _require_safe_directory_descriptor,
)

SECS_BINARY = Path("secS/bin/secs-devgraph-work-v1")
POLICY_ID = "devgraph-local-work"
STABLE_ISSUER = "secs-local-devgraph-work"
PLAN_SCHEMA = "devgraph.work-grant-plan.v1"
KINDS = ("Proposal", "Initiative", "Project", "Issue", "Task")
SCOPES = {
    **{operation: KINDS for operation in ("create", "patch", "status", "archive")},
    "accept": ("Proposal", "Decision"),
    "convert": ("Proposal", "Issue", "Decision"),
    "parent.set": ("Initiative", "Project", "Issue", "Task"),
    "dependency.add": KINDS,
    "dependency.remove": KINDS,
    "blocker.add": ("Task",),
    "blocker.remove": ("Task",),
}
ARENA_SCOPES = {
    "create": ("Arena",),
    "patch": ("Arena",),
    "archive": ("Arena",),
    "member.set": ("Arena", "Initiative", "Task"),
}
MAX_BYTES = 65536


class WorkGrantError(RuntimeError):
    """Public, redaction-safe operator failure."""


_STATUS_ERRORS = (
    WorkGrantError,
    LocalPathIntegrityError,
    LocalHostError,
    LocalSecSWalletIssueCreateError,
    LocalSecSIssueCreateError,
    signer_profile.SignerProfileError,
    OSError,
    ValueError,
)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(policy):
    return hashlib.sha256(b"secs-devgraph-work-policy.v1\0" + _json(policy)).hexdigest()


def _actor(public):
    if not isinstance(public, str) or not re.fullmatch(r"[0-9a-f]{64}", public):
        raise WorkGrantError("invalid signer public key")
    return "pubkey:sha256:" + hashlib.sha256(bytes.fromhex(public)).hexdigest()


def _signer():
    if any(name in os.environ for name in signer_profile.SIGNER_ENVIRONMENT):
        raise WorkGrantError("unset signer overrides before administering the saved identity")
    state = signer_profile.signer_status(check=True)
    if not state.get("identity_verified") or state.get("source") != "saved_profile":
        raise WorkGrantError("a verified saved signer is required; run auth setup first")
    return state["public_key"]


def _native(*arguments):
    root = signer_profile.INSTALL_ROOT
    path = root / SECS_BINARY
    try:
        directories, binary = _require_fixed_executable(
            path, expected_path=path, install_root=root, relative_path=SECS_BINARY
        )
    except LocalSecSWalletIssueCreateError:
        raise WorkGrantError(
            "the fixed secS administration binary is unavailable or unsafe"
        ) from None
    try:
        result = subprocess.run(
            [str(path), "admin", *map(str, arguments)],
            env={},
            cwd="/",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
        if result.returncode != 0 or len(result.stdout) > MAX_BYTES:
            raise WorkGrantError(
                "secS administration failed; authority may be busy, unsafe, or stale"
            )
        value = _strict_json_object(result.stdout, label="secS administration")
        if (
            value.get("schema") != "secs-devgraph-work-admin.v1"
            or type(value.get("ready")) is not bool
        ):
            raise WorkGrantError("invalid secS administration descriptor")
        return value
    except (OSError, subprocess.TimeoutExpired, LocalSecSIssueCreateError):
        raise WorkGrantError(
            "secS administration could not complete; inspect status before retrying"
        ) from None
    finally:
        _close_wallet_binary_descriptors(directories, binary)


@contextmanager
def _operator_lock():
    with signer_profile._directory(create=True) as directory:
        fd = os.open(
            "work-grants.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=directory,
        )
        try:
            signer_profile._private_descriptor(fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WorkGrantError("another Work grant administration is in progress") from None
            with signer_profile._locked(directory):
                yield
        finally:
            os.close(fd)


def _policy(public, version, start, end, *, revoked=False, include_arenas=False):
    scopes = [(f"devgraph.work.{operation}.v1", kinds) for operation, kinds in SCOPES.items()]
    if include_arenas:
        scopes.extend((f"devgraph.arena.{op}.v1", kinds) for op, kinds in ARENA_SCOPES.items())
        scopes.append(("devgraph.work.parent.set.v1", ("Arena",)))
    return {
        "schema": "secs-devgraph-work-policy.v1",
        "schema_version": 1,
        "audience": "devgraph://receiver-local",
        "policy_id": POLICY_ID,
        "policy_version": version,
        "rules": [
            {
                "actor_id": _actor(public),
                "effect": "allow",
                "not_before": start,
                "not_after": end,
                "operation": operation,
                "resource": f"{kind}/",
                "resource_match": "prefix",
                "status": "revoked" if revoked else "active",
            }
            for operation, kinds in scopes
            for kind in kinds
        ],
    }


def _validate_policy(policy, public=None):
    try:
        if set(policy) != {
            "schema",
            "schema_version",
            "audience",
            "policy_id",
            "policy_version",
            "rules",
        }:
            raise ValueError()
        rules = policy["rules"]
        first = rules[0]
        start, end, version = first["not_before"], first["not_after"], policy["policy_version"]
        if (
            any(type(v) is not int for v in (start, end, version))
            or not (0 <= start < end <= 9007199254740991)
            or not 1 <= version <= 9007199254740991
        ):
            raise ValueError()
        if public is None:
            actor = first["actor_id"]
            if not re.fullmatch(r"pubkey:sha256:[0-9a-f]{64}", actor):
                raise ValueError()
            expected = _policy(
                "0" * 64,
                version,
                start,
                end,
                revoked=first["status"] == "revoked",
                include_arenas=len(rules) == 48,
            )
            for rule in expected["rules"]:
                rule["actor_id"] = actor
        else:
            expected = _policy(
                public,
                version,
                start,
                end,
                revoked=first["status"] == "revoked",
                include_arenas=len(rules) == 48,
            )
        if type(policy["schema_version"]) is not int or _json(policy) != _json(expected):
            raise ValueError()
        return start, end, version
    except (KeyError, TypeError, IndexError, ValueError):
        raise WorkGrantError(
            "named Work policy is malformed or outside the managed scope"
        ) from None


def _validate_producer(state):
    if type(state.get("ready")) is not bool:
        raise WorkGrantError("invalid secS administration readiness")
    if state.get("state") == "missing":
        if state != {
            "schema": "secs-devgraph-work-admin.v1",
            "action": "status",
            "ready": False,
            "state": "missing",
        }:
            raise WorkGrantError("invalid missing-authority descriptor")
        return state
    try:
        if set(state) != {
            "schema",
            "action",
            "ready",
            "key_current",
            "policy_id",
            "policy_version",
            "policy_digest_sha256",
            "secs_verifier_key_id",
            "registry_sha256",
            "policy",
            "registry",
            "files",
        }:
            raise ValueError()
        if (
            state["schema"] != "secs-devgraph-work-admin.v1"
            or state["action"] not in {"status", "provision"}
            or type(state["policy_version"]) is not int
        ):
            raise ValueError()
        policy = state["policy"]
        _validate_policy(policy)
        if (
            state["policy_digest_sha256"] != _digest(policy)
            or state["policy_id"] != policy["policy_id"]
            or state["policy_version"] != policy["policy_version"]
        ):
            raise ValueError()
        registry = state["registry"]
        SecSVerifierKeyRegistry.from_json(_json(registry))
        if state["registry_sha256"] != hashlib.sha256(_json(registry)).hexdigest():
            raise ValueError()
        (key,) = registry["keys"]
        if (
            key["key_id"] != state["secs_verifier_key_id"]
            or key["production_authority"] is not True
            or key["algorithm"] != "ed25519"
        ):
            raise ValueError()
        now = int(time.time())
        current = key["status"] == "active" and key["not_before"] <= now < key["not_after"]
        if type(state["key_current"]) is not bool or state["key_current"] != current:
            raise ValueError()
        start, end, _ = _validate_policy(policy)
        ready = current and start <= now < end and policy["rules"][0]["status"] == "active"
        if state["ready"] is not ready:
            raise ValueError()
        return state
    except (KeyError, TypeError, ValueError):
        raise WorkGrantError("secS authority metadata is invalid or inconsistent") from None


def _receiver_manifest(producer):
    return {
        "schema": "devgraph-secs-work-receiver.v1",
        "schema_version": 1,
        "audience": "devgraph://receiver-local",
        "stable_issuer": STABLE_ISSUER,
        "policy_binding": {
            "policy_id": producer["policy_id"],
            "policy_version": producer["policy_version"],
            "policy_digest_sha256": producer["policy_digest_sha256"],
        },
    }


@contextmanager
def _receiver_directory(*, create=False):
    config = load_local_config(DEFAULT_CONFIG_PATH)
    if not config.availability_path.exists():
        raise WorkGrantError("configured storage is unavailable")
    root = config.data_root
    relative = Path("secrets/secs-magik/devgraph.work.v1")
    require_receiver_directory_path(root, Path("secrets"), missing_ok=False)
    if not create and require_receiver_directory_path(root, relative, missing_ok=True) is None:
        yield None
        return
    descriptors = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(root, flags)
        descriptors.append(fd)
        for part in relative.parts:
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    yield None
                    return
                os.mkdir(part, 0o700, dir_fd=fd)
                child = os.open(part, flags, dir_fd=fd)
            descriptors.append(child)
            fd = child
            _require_safe_directory_descriptor(fd)
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise WorkGrantError("Work receiver directories must be owner-private")
            require_file_descriptor_without_acl(fd)
        yield fd
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _read_at(directory, name):
    if directory is None:
        return None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None
    try:
        signer_profile._private_descriptor(fd)
        with os.fdopen(fd, "rb", closefd=False) as file:
            data = file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise WorkGrantError("Work receiver metadata exceeds its limit")
        return _strict_json_object(data, label="Work receiver metadata")
    finally:
        os.close(fd)


def _replace_at(directory, name, value):
    temporary = ".work-grant-" + secrets.token_hex(16)
    fd = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
    )
    try:
        with os.fdopen(fd, "wb") as file:
            signer_profile._private_descriptor(file.fileno())
            file.write(_json(value))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _receiver_matches(producer):
    with _receiver_directory() as directory:
        return _json(_read_at(directory, "receiver.json")) == _json(
            _receiver_manifest(producer)
        ) and _json(_read_at(directory, "secs-public-key-registry.json")) == _json(
            producer["registry"]
        )


def grant_status():
    try:
        producer = _validate_producer(_native("status"))
        if producer.get("state") == "missing":
            return {"ready": False, "state": "missing", "operations_authorized": 0}
        matches = _receiver_matches(producer)
        public = signer_profile.signer_status(check=False).get("public_key")
        actor_matches = (
            public is not None and _actor(public) == producer["policy"]["rules"][0]["actor_id"]
        )
        ready = producer["ready"] and matches and actor_matches
        return {
            "ready": ready,
            "state": "active" if ready else "inactive_or_mismatched",
            "operations_authorized": (15 if len(producer["policy"]["rules"]) == 48 else 11)
            if ready
            else 0,
            "arena_operations_authorized": 4
            if ready and len(producer["policy"]["rules"]) == 48
            else 0,
            "receiver_matches": matches,
            "signer_matches": actor_matches,
            "key_current": producer["key_current"],
            "policy_id": producer["policy_id"],
            "policy_version": producer["policy_version"],
            "policy_digest_sha256": producer["policy_digest_sha256"],
            "secs_verifier_key_id": producer["secs_verifier_key_id"],
            "actor_id": producer["policy"]["rules"][0]["actor_id"],
            "expires_at": producer["policy"]["rules"][0]["not_after"],
        }
    except _STATUS_ERRORS:
        return {"ready": False, "state": "unavailable_or_unsafe", "operations_authorized": 0}


def _plan(ttl_hours, *, renew=False, rotate=False, revoke=False, include_arenas=None):
    if type(ttl_hours) is not int or not 1 <= ttl_hours <= 8760:
        raise WorkGrantError("grant lifetime must be 1 through 8760 hours")
    public = _signer()
    previous = _validate_producer(_native("status"))
    exists = previous.get("state") != "missing"
    if exists is not renew:
        raise WorkGrantError(
            "use explicit renew for existing authority, or plan for initial provisioning"
        )
    if exists and previous["policy"]["rules"][0]["actor_id"] != _actor(public):
        raise WorkGrantError("saved signer differs from the granted identity")
    now = int(time.time())
    version = previous["policy_version"] + 1 if exists else 1
    if include_arenas is None:
        include_arenas = exists and len(previous["policy"]["rules"]) == 48
    if type(include_arenas) is not bool:
        raise WorkGrantError("Arena grant selection must be a boolean")
    policy = _policy(
        public,
        version,
        now - 1,
        now + ttl_hours * 3600,
        revoked=revoke,
        include_arenas=include_arenas,
    )
    return {
        "schema": PLAN_SCHEMA,
        "public_key": public,
        "stable_issuer": STABLE_ISSUER,
        "created_at": now,
        "expires_at": now + 900,
        "previous_policy_digest_sha256": previous["policy_digest_sha256"] if exists else None,
        "rotate_verifier": rotate,
        "policy": policy,
        "policy_digest_sha256": _digest(policy),
    }


def plan_grant(ttl_hours=720, *, renew=False, include_arenas=None):
    return _plan(ttl_hours, renew=renew, include_arenas=include_arenas)


def write_plan(plan: dict, path: Path):
    """Create a new private reviewable plan without replacing existing content."""
    _validate_plan(plan)
    if not path.is_absolute() or any(part in {".", ".."} for part in str(path).split("/")):
        raise WorkGrantError("plan output must be an absolute path")
    descriptors = []
    try:
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(fd)
        for part in path.parent.parts[1:]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            descriptors.append(fd)
            _require_safe_directory_descriptor(fd)
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise WorkGrantError("plan output directory must be owner-private")
        require_file_descriptor_without_acl(fd)
        temporary = ".grant-plan-" + secrets.token_hex(16)
        output = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        try:
            with os.fdopen(output, "wb") as file:
                signer_profile._private_descriptor(file.fileno())
                file.write(_json(plan))
                file.flush()
                os.fsync(file.fileno())
            os.link(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        finally:
            os.unlink(temporary, dir_fd=fd)
        os.fsync(fd)
        return {"plan_file": str(path), "policy_digest_sha256": plan["policy_digest_sha256"]}
    except (OSError, LocalSecSWalletIssueCreateError, LocalPathIntegrityError):
        raise WorkGrantError("plan output is unavailable, unsafe, or already exists") from None
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _validate_plan(plan):
    try:
        if set(plan) != {
            "schema",
            "public_key",
            "stable_issuer",
            "created_at",
            "expires_at",
            "previous_policy_digest_sha256",
            "rotate_verifier",
            "policy",
            "policy_digest_sha256",
        }:
            raise ValueError()
        start, end, _ = _validate_policy(plan["policy"], plan["public_key"])
        created, expires = plan["created_at"], plan["expires_at"]
        if (
            plan["schema"] != PLAN_SCHEMA
            or plan["stable_issuer"] != STABLE_ISSUER
            or type(plan["rotate_verifier"]) is not bool
        ):
            raise ValueError()
        if (
            any(type(v) is not int for v in (created, expires))
            or expires != created + 900
            or start != created - 1
            or not 3600 <= end - created <= 8760 * 3600
        ):
            raise ValueError()
        previous = plan["previous_policy_digest_sha256"]
        if previous is not None and (
            not isinstance(previous, str) or not re.fullmatch(r"[0-9a-f]{64}", previous)
        ):
            raise ValueError()
        if (
            plan["policy_digest_sha256"] != _digest(plan["policy"])
            or not created <= int(time.time()) < expires
        ):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise WorkGrantError(
            "Work grant plan is malformed, stale, or outside the managed scope"
        ) from None


def _apply(plan):
    _validate_plan(plan)
    if plan["public_key"] != _signer():
        raise WorkGrantError("grant plan does not match the verified saved identity")
    previous = _validate_producer(_native("status"))
    current_digest = previous.get("policy_digest_sha256")
    finishing = current_digest == plan["policy_digest_sha256"]
    if finishing and _receiver_matches(previous):
        raise WorkGrantError("grant plan has already been applied; renew requires a fresh plan")
    if not finishing and current_digest != plan["previous_policy_digest_sha256"]:
        raise WorkGrantError("grant plan is stale; prepare a new plan")
    # Validate/create private receiver storage before native producer mutation.
    with _receiver_directory(create=True) as directory:
        _read_at(directory, "receiver.json")
        _read_at(directory, "secs-public-key-registry.json")
        if plan["policy"]["rules"][0]["status"] == "revoked":
            # Receiver-first revocation invalidates even previously issued projections.
            if _read_at(directory, "receiver.json") is not None:
                os.rename(
                    "receiver.json",
                    ".revoked-" + secrets.token_hex(16),
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                )
                os.fsync(directory)
        if not finishing:
            with tempfile.TemporaryDirectory(prefix="devgraph-work-policy-") as temporary:
                root = Path(temporary).resolve()
                os.chmod(root, 0o700)
                policy_file = root / "policy.json"
                with policy_file.open("xb") as file:
                    os.fchmod(file.fileno(), 0o600)
                    file.write(_json(plan["policy"]))
                args = ["provision", "--policy-file", str(policy_file)]
                if current_digest:
                    args += ["--expected-policy-digest", current_digest]
                if plan["rotate_verifier"]:
                    args += ["--rotate-verifier"]
                previous = _validate_producer(_native(*args))
        if previous["policy_digest_sha256"] != plan["policy_digest_sha256"]:
            raise WorkGrantError("native authority differs from the reviewed grant plan")
        _replace_at(directory, "secs-public-key-registry.json", previous["registry"])
        if plan["policy"]["rules"][0]["status"] != "revoked":
            _replace_at(directory, "receiver.json", _receiver_manifest(previous))
    return grant_status()


def apply_grant(plan_file: Path):
    try:
        raw = _read_private_bounded_file(
            plan_file, label="Work grant plan", maximum_bytes=MAX_BYTES
        )
        plan = _strict_json_object(raw, label="Work grant plan")
        with _operator_lock():
            return _apply(plan)
    except (
        LocalSecSIssueCreateError,
        LocalSecSWalletIssueCreateError,
        LocalHostError,
        signer_profile.SignerProfileError,
        LocalPathIntegrityError,
        OSError,
    ):
        raise WorkGrantError(
            "Work grant update could not complete; inspect status before retrying"
        ) from None


def _renew(ttl_hours, *, rotate=False, revoke=False, include_arenas=None):
    try:
        with _operator_lock():
            return _apply(
                _plan(
                    ttl_hours,
                    renew=True,
                    rotate=rotate,
                    revoke=revoke,
                    include_arenas=include_arenas,
                )
            )
    except (
        signer_profile.SignerProfileError,
        LocalPathIntegrityError,
        LocalHostError,
        LocalSecSWalletIssueCreateError,
        OSError,
    ):
        raise WorkGrantError(
            "Work grant update could not complete; inspect status before retrying"
        ) from None


def renew_grant(ttl_hours=720, *, include_arenas=None):
    return _renew(ttl_hours, include_arenas=include_arenas)


def rotate_verifier(ttl_hours=720):
    return _renew(ttl_hours, rotate=True)


def revoke_grant():
    """Emergency owner revocation never requires the actor key or saved profile."""
    try:
        with _operator_lock():
            with _receiver_directory(create=True) as directory:
                # Rename the entry without reading it: malformed metadata or a
                # missing actor key must never prevent removal of admission.
                try:
                    os.rename(
                        "receiver.json",
                        ".revoked-" + secrets.token_hex(16),
                        src_dir_fd=directory,
                        dst_dir_fd=directory,
                    )
                    os.fsync(directory)
                except FileNotFoundError:
                    pass
            result = {
                "ready": False,
                "state": "revoked",
                "operations_authorized": 0,
                "receiver_revoked": True,
                "producer_revoked": False,
            }
            try:
                previous = _validate_producer(_native("status"))
                if previous.get("state") == "missing":
                    result["producer_revoked"] = True
                    return result
                policy = json.loads(_json(previous["policy"]))
                policy["policy_version"] += 1
                for rule in policy["rules"]:
                    rule["status"] = "revoked"
                with tempfile.TemporaryDirectory(prefix="devgraph-revoke-policy-") as temporary:
                    root = Path(temporary).resolve()
                    os.chmod(root, 0o700)
                    policy_file = root / "policy.json"
                    with policy_file.open("xb") as file:
                        os.fchmod(file.fileno(), 0o600)
                        file.write(_json(policy))
                    current = _validate_producer(
                        _native(
                            "provision",
                            "--policy-file",
                            policy_file,
                            "--expected-policy-digest",
                            previous["policy_digest_sha256"],
                        )
                    )
                if current["policy_digest_sha256"] != _digest(policy) or current["ready"]:
                    raise WorkGrantError("producer revocation did not match its policy")
                with _receiver_directory(create=True) as directory:
                    _replace_at(directory, "secs-public-key-registry.json", current["registry"])
                result.update(
                    producer_revoked=True,
                    policy_id=current["policy_id"],
                    policy_version=current["policy_version"],
                    policy_digest_sha256=current["policy_digest_sha256"],
                )
            except _STATUS_ERRORS:
                result["state"] = "receiver_revoked_producer_unavailable"
            return result
    except (
        LocalHostError,
        LocalPathIntegrityError,
        LocalSecSWalletIssueCreateError,
        signer_profile.SignerProfileError,
        OSError,
    ):
        raise WorkGrantError(
            "receiver revocation could not complete; inspect local receiver storage"
        ) from None
