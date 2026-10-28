"""Explicit local session capture. No graph credentials or graph writes."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from devgraph.observability.session_capture import Dispatch, SessionJournal, native_session_id
from devgraph.observability.session_ref import SessionIdentity


def configure(subcommands: argparse._SubParsersAction) -> None:
    parser = subcommands.add_parser("sessions", help="capture source session metadata locally")
    commands = parser.add_subparsers(dest="sessions_command", required=True)
    run = commands.add_parser("run", help="launch Codex/Claude with metadata-only identity capture")
    run.add_argument("--client", choices=("codex", "claude-code"), required=True)
    run.add_argument(
        "--source-instance",
        required=True,
        help="stable opaque ID for the actual client host/profile/store",
    )
    run.add_argument("--journal-dir", type=Path, required=True)
    run.add_argument("--attempt-id", default=None)
    run.add_argument("--work", action="append", default=[])
    run.add_argument("--parent-hermes-id")
    run.add_argument("--hermes-source-instance")
    run.add_argument("--tool-call-id")
    run.add_argument("client_args", nargs=argparse.REMAINDER)
    events = commands.add_parser("events", help="read a bounded local metadata outbox page")
    events.add_argument("--journal-dir", type=Path, required=True)
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--limit", type=int, default=50)
    prepare = commands.add_parser("prepare-import", help="prepare exact signed session attribution")
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--ref", required=True)
    prepare.add_argument("--work", required=True)
    prepare.add_argument("--link-ref")
    prepare.add_argument("--output-dir", type=Path, required=True)
    state = commands.add_parser(
        "prepare-state", help="prepare DevGraph session completion or reopening"
    )
    state.add_argument("--config", type=Path, required=True)
    state.add_argument("--ref", required=True)
    state.add_argument("--state", choices=("open", "done"), required=True)
    state.add_argument("--output-dir", type=Path, required=True)


def _arguments(application: str, arguments: list[str]) -> list[str]:
    arguments = arguments[1:] if arguments[:1] == ["--"] else list(arguments)
    if application == "codex":
        if not arguments or arguments[0] != "exec":
            raise ValueError("session_capture_requires_codex_exec")
        return arguments if "--json" in arguments else ["exec", "--json", *arguments[1:]]
    if not any(arg in {"-p", "--print"} for arg in arguments):
        raise ValueError("session_capture_requires_claude_print")
    if any(arg == "--output-format" or arg.startswith("--output-format=") for arg in arguments):
        raise ValueError("session_capture_owns_output_format")
    return ["--output-format", "stream-json", "--verbose", *arguments]


def capture_process(dispatch: Dispatch, arguments: list[str], journal: SessionJournal) -> int:
    """Persist before spawn. Forward the native stream; store only typed IDs/exit.

    No shell expansion, approval flag changes, source-directory scan, model probe,
    or transcript file is introduced. Caller retains responsibility for cwd/auth.
    A retry must resume explicitly with a NEW attempt ID; do not repeat a launch.
    """
    binary = shutil.which("claude" if dispatch.application == "claude-code" else "codex")
    if binary is None:
        raise ValueError("session_client_unavailable")
    arguments = _arguments(dispatch.application, arguments)
    journal.dispatch(dispatch)
    # A dispatched attempt is immutable; never launch it again after an uncertain outcome.
    journal.claim_launch(dispatch.attempt_id)
    process = subprocess.Popen([binary, *arguments], stdout=subprocess.PIPE)
    assert process.stdout is not None
    oversized = False
    try:
        while chunk := process.stdout.readline(65_537):
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            if not oversized and len(chunk) <= 65_536:
                try:
                    native_id = native_session_id(dispatch.application, chunk.decode("utf-8"))
                except UnicodeDecodeError:
                    native_id = None
                if native_id:
                    journal.observe(dispatch.attempt_id, native_id)
            oversized = not chunk.endswith(b"\n") and len(chunk) > 65_536
        result = process.wait()
        journal.exited(dispatch.attempt_id, result)
        return result
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def execute(args: argparse.Namespace) -> int:
    journal = None
    try:
        if args.sessions_command in {"prepare-import", "prepare-state"}:
            from devgraph.observability.session_prepare import prepare, prepare_state, write_bundle
            from devgraph.observability.session_read_runtime import SessionReader
            from devgraph.observability.session_sources import owned

            owned(args.config, private=True)
            reader = SessionReader(json.loads(args.config.read_text()))
            request = (
                prepare_state(reader, args.ref, args.state)
                if args.sessions_command == "prepare-state"
                else prepare(reader, args.ref, args.work, link_ref=args.link_ref)
            )
            print(json.dumps(write_bundle(request, args.output_dir)))
            return 0
        if args.sessions_command == "events":
            if not (args.journal_dir / "session-events.sqlite3").is_file():
                raise ValueError("session_journal_unavailable")
            journal = SessionJournal(args.journal_dir, read_only=True)
            print(json.dumps(journal.page(after=args.after, limit=args.limit)))
            return 0
        if bool(args.parent_hermes_id) != bool(args.hermes_source_instance):
            raise ValueError("incomplete_hermes_session_parent")
        parent = (
            SessionIdentity("hermes", args.hermes_source_instance, args.parent_hermes_id)
            if args.parent_hermes_id
            else None
        )
        dispatch = Dispatch(
            args.attempt_id or str(uuid4()),
            args.source_instance,
            args.client,
            tuple(args.work),
            parent,
            args.tool_call_id,
            "ephemeral"
            if any(
                value in args.client_args
                for value in (
                    "--ephemeral",
                    "--no-session-persistence",
                )
            )
            else "unknown",
        )
        # Validate before creating the private journal or launching anything.
        _arguments(args.client, args.client_args)
        journal = SessionJournal(args.journal_dir)
        print(
            json.dumps({"attempt_id": dispatch.attempt_id, "capture": "local_metadata_only"}),
            file=sys.stderr,
        )
        return capture_process(dispatch, args.client_args, journal)
    except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error):
        # Arguments, paths and subprocess diagnostics can contain private material.
        print(
            json.dumps({"error": "session_capture_failed", "outcome": "unknown"}), file=sys.stderr
        )
        return 2
    finally:
        if journal is not None:
            journal.close()
