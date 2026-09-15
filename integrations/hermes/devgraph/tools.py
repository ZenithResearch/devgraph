"""Typed CLI composition, bounded process I/O, and safe tool results.

No credential files are opened here. No model argument selects an executable,
environment, endpoint, configuration file, or arbitrary CLI command.
"""

from __future__ import annotations

import json
import os
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path

WORK_KINDS = ("Proposal", "Initiative", "Project", "Issue", "Task")
WORK_OPERATIONS = (
    "create", "patch", "status", "archive", "accept", "convert", "parent.set",
    "dependency.add", "dependency.remove", "blocker.add", "blocker.remove",
)
ARENA_OPERATIONS = ("create", "patch", "archive", "member.set")
RELATIONSHIPS = ("children", "parent", "dependencies", "dependents", "blockers", "blocked")
READ_OPERATIONS = ("work", *RELATIONSHIPS, "arena", "arena-members", "arena-of")
ID_PATTERN = r"[a-z0-9](?:[a-z0-9-]{0,254}[a-z0-9])?"
IDEMPOTENCY_PATTERN = r"[A-Za-z0-9._~-]{16,128}"
MAX_REQUEST_BYTES = 131_072
MAX_OUTPUT_BYTES = 262_144
MAX_DEPTH = 32
RETRY_GUIDANCE = "The outcome may be unknown. Retry the identical request and idempotency key."
_SENSITIVE = re.compile(
    r"credential|password|secret|token|seed|private.?key|api.?key|signed.?projection|"
    r"authorization|idempotency|signer.?environment", re.I,
)
_SECRET_TEXT = re.compile(
    r"dgread1_[A-Za-z0-9_-]{43}|\bBearer\s+\S+|"
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----|"
    r"\b(?:api[_ -]?key|password|token|secret|credential|private[_ -]?key)\s*[:=]\s*\S+",
    re.I | re.S,
)


class PluginError(Exception):
    """Contains only a fixed diagnostic code, never request or child output."""


def _require(condition, code="invalid_arguments"):
    if not condition:
        raise PluginError(code)


def _account_home():
    # Match the CLI's account-owned signer selection; do not inherit fake HOME.
    import pwd

    return Path(pwd.getpwuid(os.geteuid()).pw_dir)


def _settings():
    from hermes_cli.config import load_config

    _require(os.name == "posix", "unsupported_platform")
    config = load_config()
    entry = config.get("plugins", {}).get("entries", {}).get("devgraph", {})
    _require(type(entry) is dict and not set(entry) - {"cli_path", "timeout_seconds"},
             "invalid_plugin_configuration")
    raw = entry.get("cli_path", str(_account_home() / ".local/bin/devgraph"))
    _require(type(raw) is str and Path(raw).is_absolute(), "invalid_cli_path")
    executable = Path(raw).resolve(strict=True)
    info = executable.stat()
    _require(stat.S_ISREG(info.st_mode) and info.st_uid in (0, os.geteuid())
             and not info.st_mode & 0o022 and os.access(executable, os.X_OK),
             "unsafe_cli_path")
    timeout = entry.get("timeout_seconds", 90)
    _require(type(timeout) in (int, float) and 1 <= timeout <= 120,
             "invalid_plugin_configuration")
    return executable, timeout


def available():
    """Configuration-only discovery check; never probes or changes the graph."""
    try:
        _settings()
        return True
    except Exception:
        return False


def _stop(process):
    # Also stop native signer children; a timeout can still follow graph commit.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def _run(executable, arguments, timeout):
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    finished = False
    process = subprocess.Popen(
        [str(executable), *arguments], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/", start_new_session=True,
        env={"HOME": str(_account_home()), "PATH": "/usr/bin:/bin",
             "PYTHONNOUSERSITE": "1", "PYTHONSAFEPATH": "1"},
    )
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PluginError("cli_timeout")
                for key, _ in selector.select(min(remaining, 0.2)):
                    raw = os.read(key.fileobj.fileno(), 16_384)
                    if not raw:
                        selector.unregister(key.fileobj)
                    else:
                        chunks[key.data].extend(raw)
                        if sum(map(len, chunks.values())) > MAX_OUTPUT_BYTES:
                            raise PluginError("cli_output_limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PluginError("cli_timeout")
        process.wait(timeout=remaining)
        _require(process.returncode == 0 or (
            arguments == ["local", "status"] and process.returncode == 1
        ), "cli_failed")
        try:
            result = json.loads(chunks["stdout"], parse_constant=_invalid_constant)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise PluginError("invalid_cli_output") from None
        _require(type(result) is dict, "invalid_cli_output")
        unhealthy_status = (arguments == ["local", "status"] and process.returncode == 1
                            and result.get("healthy") is False)
        _require(process.returncode == 0 or unhealthy_status, "cli_failed")
        # A failed CLI can return JSON even if an old wrapper lost its exit code.
        _require("error" not in result, "cli_failed")
        finished = True
        return result
    except subprocess.TimeoutExpired:
        raise PluginError("cli_timeout") from None
    finally:
        if not finished or process.poll() is None:
            _stop(process)
        process.stdout.close()
        process.stderr.close()


def _invalid_constant(_value):
    raise ValueError("nonfinite_json")


def _redact(value, depth=0):
    _require(depth <= MAX_DEPTH, "cli_output_depth")
    if type(value) is dict:
        return {key: _redact(item, depth + 1) for key, item in value.items()
                if not _SENSITIVE.search(key) and not _SECRET_TEXT.search(key)}
    if type(value) is list:
        return [_redact(item, depth + 1) for item in value]
    if type(value) is str:
        return _SECRET_TEXT.sub("[redacted]", value)
    return value


def _id(value):
    _require(type(value) is str and re.fullmatch(ID_PATTERN, value) is not None)
    return value


def _read_arguments(args):
    allowed = {"operation", "kind", "id", "include_archived", "after_id",
               "after_resource", "limit"}
    _require(not set(args) - allowed and args.get("operation") in READ_OPERATIONS)
    operation = args["operation"]
    kind, identifier = args.get("kind"), args.get("id")
    needs_kind = operation in ("work", "arena-of", *RELATIONSHIPS)
    _require(kind in WORK_KINDS if needs_kind else kind is None)
    if operation in ("blockers", "blocked"):
        _require(kind == "Task")
    if operation not in ("work", "arena"):
        _require(identifier is not None)
    command = ["query", operation]
    if needs_kind and operation not in ("blockers", "blocked"):
        command.append(kind)
    if identifier is not None:
        command.append(_id(identifier))
    is_list = operation in ("work", "arena") and identifier is None
    is_relationship = operation in (*RELATIONSHIPS, "arena-members")
    _require(type(args.get("include_archived", False)) is bool)
    if args.get("include_archived"):
        _require(is_list)
        command.append("--include-archived")
    if "limit" in args:
        limit = args["limit"]
        _require(type(limit) is int and 1 <= limit <= 100 and (is_list or is_relationship))
        command.extend(("--limit", str(limit)))
    elif is_list or is_relationship:
        command.extend(("--limit", "20"))
    if "after_id" in args:
        _require(is_list)
        command.extend(("--after-id", _id(args["after_id"])))
    if "after_resource" in args:
        cursor = args["after_resource"]
        _require(is_relationship and type(cursor) is str
                 and re.fullmatch(r"[A-Za-z]+/" + ID_PATTERN, cursor) is not None)
        cursor_kind = cursor.split("/", 1)[0]
        _require(cursor_kind in (("Initiative", "Task") if operation == "arena-members"
                                 else WORK_KINDS))
        command.extend(("--after-resource", cursor))
    return command


def _write_arguments(args, domain, directory):
    _require(set(args) == {"request", "idempotency_key"})
    request, key = args["request"], args["idempotency_key"]
    _require(type(request) is dict and set(request) == {
        "schema", "operation", "kind", "id", "expected_version", "payload",
    })
    operations = WORK_OPERATIONS if domain == "work" else ARENA_OPERATIONS
    _require(request["schema"] == f"devgraph.{domain}-request.v1")
    _require(request["operation"] in operations)
    _require(request["kind"] in (WORK_KINDS if domain == "work"
                                else ("Arena", "Initiative", "Task")))
    _id(request["id"])
    version = request["expected_version"]
    _require(version is None or (type(version) is int and 1 <= version <= 2**53 - 1))
    _require((request["operation"] == "create") == (version is None))
    _require(type(request["payload"]) is dict)
    _require(type(key) is str and re.fullmatch(IDEMPOTENCY_PATTERN, key) is not None)
    # The CLI validates the complete versioned payload before signing. Keep a
    # stable serialization, including the caller's exact idempotency value.
    raw = json.dumps(request, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    _require(len(raw) <= MAX_REQUEST_BYTES, "request_too_large")
    for name, content in (("request.json", raw), ("idempotency-key.txt", key.encode("ascii"))):
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
    return [domain, request["operation"], "--request-file", str(directory / "request.json"),
            "--idempotency-key-file", str(directory / "idempotency-key.txt")]


def handle(name, args):
    writing = name in ("devgraph_work_operation", "devgraph_arena_operation")
    submitted = False
    try:
        _require(type(args) is dict)
        executable, timeout = _settings()
        if writing:
            domain = "work" if name == "devgraph_work_operation" else "arena"
            with tempfile.TemporaryDirectory(prefix="devgraph-hermes-") as temporary:
                directory = Path(temporary).resolve()
                directory.chmod(0o700)
                command = _write_arguments(args, domain, directory)
                submitted = True
                result = _run(executable, command, timeout)
        elif name == "devgraph_read":
            result = _run(executable, _read_arguments(args), timeout)
        else:
            _require(name == "devgraph_status" and not args)
            status = _run(executable, ["local", "status"], timeout)
            readiness = status.get("api", {}).get("api", {}).get("readiness", {})
            result = {"configured": status.get("configured") is True,
                      "healthy": status.get("healthy") is True,
                      "migration_version": readiness.get("current_applied_version")}
        return json.dumps({"ok": True, "data": _redact(result)}, allow_nan=False)
    except Exception as error:
        code = str(error) if isinstance(error, PluginError) else "plugin_unavailable"
        result = {"ok": False, "error": code}
        if writing and submitted:
            result.update(outcome_unknown=True, guidance=RETRY_GUIDANCE)
        return json.dumps(result)


def _schema(name, description, properties, required):
    return {"name": name, "description": description, "parameters": {
        "type": "object", "additionalProperties": False,
        "properties": properties, "required": required,
    }}


def _write_schema(domain, operations, kinds):
    return _schema(
        f"devgraph_{domain}_operation",
        "Submit an explicitly requested signed operation through the configured Wallet and secS. "
        "Current grants and version preconditions are enforced by Devgraph. "
        "Reuse the same request and idempotency key after any uncertain result.",
        {"request": {"type": "object", "additionalProperties": False, "properties": {
            "schema": {"type": "string", "enum": [f"devgraph.{domain}-request.v1"]},
            "operation": {"type": "string", "enum": list(operations)},
            "kind": {"type": "string", "enum": list(kinds)},
            "id": {"type": "string", "pattern": "^" + ID_PATTERN + "$"},
            "expected_version": {"type": ["integer", "null"], "minimum": 1,
                                 "maximum": 2**53 - 1},
            "payload": {"type": "object", "description":
                        "Payload from the versioned operation contract; native CLI validates it."},
        }, "required": ["schema", "operation", "kind", "id", "expected_version", "payload"]},
         "idempotency_key": {"type": "string", "pattern": "^" + IDEMPOTENCY_PATTERN + "$",
                             "description": "Stable retry identifier, not a credential."}},
        ["request", "idempotency_key"],
    )


SCHEMAS = (
    _schema("devgraph_status", "Check local health without revealing configuration or keys.",
            {}, []),
    _schema("devgraph_read", "Read Work, Arenas, or relationships with the local read capability. "
            "Lists default to 20 records, maximum 100; page after the last item's ID/resource.", {
                "operation": {"type": "string", "enum": list(READ_OPERATIONS)},
                "kind": {"type": "string", "enum": list(WORK_KINDS)},
                "id": {"type": "string", "pattern": "^" + ID_PATTERN + "$"},
                "include_archived": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "after_id": {"type": "string"}, "after_resource": {"type": "string"},
            }, ["operation"]),
    _write_schema("work", WORK_OPERATIONS, WORK_KINDS),
    _write_schema("arena", ARENA_OPERATIONS, ("Arena", "Initiative", "Task")),
)
