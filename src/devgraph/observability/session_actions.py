"""Private session-state review bundles for a same-origin local UI.

Only the existing CLI can submit; native Wallet still requires the human's real
terminal. No key, credential, arbitrary command, path or Work mutation is accepted
from the browser. Pending/uncertain attempts retain their original retry identity.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import stat
import subprocess
import threading
import time
from pathlib import Path

from devgraph.observability.session_prepare import prepare_state, write_bundle
from devgraph.observability.session_reads import REF_ID
from devgraph.observability.session_sources import owned
from devgraph.work_requests import WorkRequest

TICKET = re.compile(r"[0-9a-f]{64}")
REVIEW_LEASE_SECONDS = 300


def _directory(path, *, create=False):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("unsafe_session_action_directory")


def _read(path, maximum=65536):
    if owned(path, private=True).st_size > maximum:
        raise ValueError("session_action_result_limit")
    with path.open("rb") as handle:
        value = handle.read(maximum + 1)
    if len(value) > maximum:
        raise ValueError("session_action_result_limit")
    return value


def _write(path, content, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def open_terminal(command):
    subprocess.run(
        ["/usr/bin/open", "-a", "Terminal", str(command)],
        check=True,
        capture_output=True,
        timeout=15,
    )


class SessionStateActions:
    def __init__(self, *, root: Path, python: Path, reader, launch=None, clock=None):
        if not root.is_absolute() or not python.is_absolute() or not python.is_file():
            raise ValueError("session_action_runtime_unavailable")
        self.root, self.python, self.reader = root, python, reader
        _directory(root, create=True)
        self.launch = launch or open_terminal
        self.clock = clock or time.time
        self.lock = threading.Lock()

    def _bundle(self, ticket):
        if not isinstance(ticket, str) or not TICKET.fullmatch(ticket):
            raise ValueError("invalid_session_action_ticket")
        directory = self.root / ticket
        _directory(directory)
        request = WorkRequest.from_json(_read(directory / "request.json"))
        if request.operation != "session.state" or request.digest != ticket:
            raise ValueError("invalid_session_action_bundle")
        return directory, request

    @staticmethod
    def _attempts(directory):
        entries = list(directory.iterdir())
        if len(entries) > 30:
            raise ValueError("session_action_attempt_limit")
        return sorted(
            (p for p in entries if re.fullmatch(r"attempt-\d{2}", p.name)), key=lambda p: p.name
        )

    def begin(self, ref_id, state, expected_version):
        if not isinstance(ref_id, str) or not REF_ID.fullmatch(ref_id):
            raise ValueError("invalid_session_reference")
        if state not in {"open", "done"} or type(expected_version) is not int:
            raise ValueError("invalid_session_state_intent")
        candidate = WorkRequest.from_json(
            json.dumps(
                {
                    "schema": "devgraph.work-request.v2",
                    "operation": "session.state",
                    "kind": "SessionRef",
                    "id": ref_id,
                    "expected_version": expected_version,
                    "payload": {"state": state},
                }
            ).encode()
        )
        with self.lock:
            directory = self.root / candidate.digest
            if directory.exists():
                directory, request = self._bundle(candidate.digest)
                prior = self.status(candidate.digest)
                if prior["phase"] in {"committed", "awaiting_approval"}:
                    return prior
                # A committed write may have lost its response. Keep the original
                # intent reachable even after the current state/revision changed.
                try:
                    fresh = prepare_state(self.reader, ref_id, state)
                    if fresh.expected_version != expected_version:
                        raise ValueError("session_state_changed_refresh_required")
                except ValueError:
                    return {**prior, "phase": "needs_reconciliation"}
            else:
                request = prepare_state(self.reader, ref_id, state)
                if request.expected_version != expected_version:
                    raise ValueError("session_state_changed_refresh_required")
                if sum(1 for _ in self.root.iterdir()) >= 1000:
                    raise ValueError("session_action_capacity")
                write_bundle(request, directory)
            return self._review(directory, request)

    def reconcile(self, ticket):
        with self.lock:
            directory, request = self._bundle(ticket)
            return self._review(directory, request, reconcile=True)

    def _review(self, directory, request, *, reconcile=False):
        attempts = self._attempts(directory)
        if attempts and not (attempts[-1] / "exit-code").exists():
            pending = self.status(request.digest)
            if pending["phase"] == "awaiting_approval":
                return pending
        if len(attempts) >= 20:
            raise ValueError("session_action_attempt_limit")
        attempt = directory / f"attempt-{len(attempts) + 1:02d}"
        _directory(attempt, create=True)
        command = attempt / "Review session state.command"
        result = attempt / "result.json"
        pending = attempt / "result.pending"
        exit_code = attempt / "exit-code"
        _write(attempt / "started-at", str(self.clock()))
        arguments = [
            str(self.python),
            "-I",
            "-m",
            "devgraph.cli",
            "work",
            "session.state",
            "--credential-v2",
            "--request-file",
            str(directory / "request.json"),
            "--idempotency-key-file",
            str(directory / "idempotency.txt"),
        ]
        if reconcile:
            arguments.append("--reconcile")
        # stdin AND stderr stay on the real tty: the CLI and Wallet require it.
        script = "#!/bin/sh\numask 077\n"
        for signal_name, code in (("HUP", 129), ("INT", 130), ("TERM", 143)):
            interrupted = (
                f"printf '%s\\n' {code} > " + shlex.quote(str(exit_code)) + f"; exit {code}"
            )
            script += "trap " + shlex.quote(interrupted) + " " + signal_name + "\n"
        script += (
            "printf '%s\\n' "
            + shlex.quote(
                "Check saved DevGraph session result"
                if reconcile
                else "DevGraph session work → " + request.payload["state"].upper()
            )
            + "\n"
        )
        script += (
            " ".join(shlex.quote(a) for a in arguments) + " > " + shlex.quote(str(pending)) + "\n"
        )
        script += (
            "result_code=$?\n/bin/mv "
            + shlex.quote(str(pending))
            + " "
            + shlex.quote(str(result))
            + "\n"
        )
        script += "printf '%s\\n' \"$result_code\" > " + shlex.quote(str(exit_code)) + "\n"
        script += (
            "printf '\\nReturn to Pulse to check the saved state. Press Return to close.\\n'\n"
            "read -r close_review\n"
        )
        _write(command, script, 0o700)
        try:
            self.launch(command)
        except (OSError, subprocess.SubprocessError):
            _write(exit_code, "127\n")
            raise ValueError("session_approval_window_unavailable") from None
        return self.status(request.digest)

    def status(self, ticket):
        directory, request = self._bundle(ticket)
        attempts = self._attempts(directory)
        value = {
            "ticket": ticket,
            "id": request.id,
            "requested_state": request.payload["state"],
            "phase": "awaiting_approval",
            "approval": "native_terminal",
        }
        if not attempts:
            value["phase"] = "unconfirmed"
            return value
        attempt = attempts[-1]
        _directory(attempt)
        exit_file = attempt / "exit-code"
        if not exit_file.exists():
            try:
                age = self.clock() - float(_read(attempt / "started-at", 64))
                if not 0 <= age < REVIEW_LEASE_SECONDS:
                    value["phase"] = "unconfirmed"
            except (OSError, ValueError):
                value["phase"] = "unconfirmed"
            return value
        value["phase"] = "needs_review"
        try:
            result = json.loads(_read(attempt / "result.json"))
            if not isinstance(result, dict):
                raise ValueError("invalid_session_action_result")
            receipt = result.get("receipt") or {}
            if not isinstance(receipt, dict):
                raise ValueError("invalid_session_action_result")
            if (
                _read(exit_file, 16).strip() != b"0"
                or receipt.get("operation") != request.authority_operation
                or receipt.get("subject_label") != "SessionRef"
                or receipt.get("subject_id") != request.id
                or not isinstance(receipt.get("receipt_id"), str)
            ):
                return value
            canonical = self.reader.graph_reader("/session-refs/v1/" + request.id)
            current = (canonical or {}).get("work_state")
            if (
                not current
                or canonical.get("id") != request.id
                or canonical.get("canonical") is not True
                or type(current.get("version")) is not int
                or current["version"] <= request.expected_version
                or current.get("state") not in {"open", "done"}
            ):
                value["phase"] = "unconfirmed"
                return value
            # Show current canonical truth, even if another actor changed it again.
            value.update(phase="committed", work_state=current, receipt_id=receipt["receipt_id"])
        except (ValueError, OSError, TypeError):
            value["phase"] = "unconfirmed"
        return value
