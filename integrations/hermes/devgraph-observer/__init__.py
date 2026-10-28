"""Hermes public lifecycle hooks. No network, credentials, or transcript retention.

Events witness source activity and Work inspection/accepted tool results. They do
not confer authority or assert graph attribution. Failure never changes a tool
result or injects prompt context. Existing processes require a new session to
load plugins; the reader reconciles their source stores independently.
"""

import hashlib
import json
import os
import re
import shlex
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,255}")
WORK = re.compile(r"(?:Proposal|Initiative|Project|Issue|Task)/[A-Za-z0-9._:-]{1,200}")


def _id(value):
    return value if isinstance(value, str) and IDENT.fullmatch(value) else None


def _write(event):
    home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).resolve()
    directory = home / "devgraph-observer"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("unsafe_observer_directory")
    path = directory / "events.sqlite3"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ValueError("unsafe_observer_file")
    finally:
        os.close(fd)
    raw = json.dumps(event, sort_keys=True, separators=(",", ":"))
    key = hashlib.sha256(raw.encode()).hexdigest()
    with sqlite3.connect(path, timeout=1) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY, "
            "event_key TEXT UNIQUE, payload TEXT, observed_at TEXT)"
        )
        db.execute(
            "INSERT OR IGNORE INTO events(event_key,payload,observed_at) VALUES (?,?,?)",
            (key, raw, datetime.now(timezone.utc).isoformat()),
        )


def _object(value):
    if isinstance(value, str) and len(value.encode()) <= 262144:
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def reduce_event(hook, payload):
    session = _id(payload.get("session_id") or payload.get("child_session_id"))
    if not session:
        return []
    event = {"schema": "devgraph.hermes-observation.v1", "hook": hook, "session_id": session}
    for key in ("turn_id", "tool_call_id", "parent_session_id"):
        value = _id(payload.get(key))
        if value:
            event[key] = value
    events = [event]
    if hook != "post_tool_call":
        return events
    args, result = _object(payload.get("args")), _object(payload.get("result"))
    tool = payload.get("tool_name")
    # A successful read is an inspected relationship, never a mutation receipt.
    if result.get("ok") is True:
        target = args if tool == "devgraph_read" else args.get("request", {})
        if tool in {"devgraph_read", "devgraph_work_operation"} and isinstance(target, dict):
            key = str(target.get("kind", "")) + "/" + str(target.get("id", ""))
            if WORK.fullmatch(key):
                events.append(
                    {
                        **event,
                        "work_ref": key,
                        "basis": (
                            "inspected_work"
                            if tool == "devgraph_read"
                            else "successful_work_tool_result"
                        ),
                    }
                )
    # Observe only a directly invoked native CLI and its typed machine event.
    # Shell pipelines/background handles are deliberately unresolved.
    command = args.get("command", "")
    if tool == "terminal" and isinstance(command, str) and len(command) <= 16384:
        if any(c in command for c in ";|&`$\n"):
            return events
        try:
            argv = shlex.split(command)
        except ValueError:
            return events
        executable = Path(argv[0]).name if argv else ""
        app = (
            "codex"
            if executable == "codex" and "exec" in argv and "--json" in argv
            else ("claude-code" if executable == "claude" and "stream-json" in argv else None)
        )
        output = result.get("output", result.get("stdout", ""))
        if app and isinstance(output, str) and len(output.encode()) <= 262144:
            for line in output.splitlines()[:256]:
                value = _object(line)
                native = None
                if app == "codex" and value.get("type") == "thread.started":
                    native = value.get("thread_id")
                if (
                    app == "claude-code"
                    and value.get("parent_tool_use_id") is None
                    and (
                        value.get("type") == "result"
                        or (value.get("type") == "system" and value.get("subtype") == "init")
                    )
                ):
                    native = value.get("session_id")
                if _id(native):
                    events.append(
                        {
                            **event,
                            "native_application": app,
                            "native_id": native,
                            "basis": "typed_cli_identity_event",
                        }
                    )
    return events


def observe(hook, **payload):
    try:
        for event in reduce_event(hook, payload):
            _write(event)
    except Exception:
        # No raw error, arguments or result logging. Do not affect agent execution.
        return None
    return None


def register(ctx):
    for hook in (
        "on_session_start",
        "on_session_end",
        "on_session_finalize",
        "pre_llm_call",
        "post_llm_call",
        "post_tool_call",
        "subagent_start",
        "subagent_stop",
    ):
        ctx.register_hook(hook, lambda _hook=hook, **kwargs: observe(_hook, **kwargs))
