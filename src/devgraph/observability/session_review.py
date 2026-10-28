"""One-shot native review runner; no raw native output is captured or persisted."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

from devgraph.observability.session_actions import (
    REVIEW_ERRORS,
    TICKET,
    _directory,
    _read,
    _write,
    review_lock,
)
from devgraph.ops.credential_work_agent import (
    LocalCredentialWorkAgentError,
    execute_local_credential_work,
)
from devgraph.work_requests import WorkRequest


def run_review(attempt):
    import re

    if not attempt.is_absolute() or not re.fullmatch(r"attempt-\d{2}", attempt.name):
        raise ValueError("invalid_session_review_attempt")
    directory = attempt.parent
    _directory(directory)
    _directory(attempt)
    request_raw = _read(directory / "request.json")
    key_raw = _read(directory / "idempotency.txt", 256)
    request = WorkRequest.from_json(request_raw)
    if not TICKET.fullmatch(directory.name) or request.digest != directory.name:
        raise ValueError("invalid_session_review_bundle")
    if request.operation != "session.state" or _read(attempt / "ready-gated", 8) != b"1\n":
        raise ValueError("invalid_session_review_bundle")
    mode = _read(attempt / "review-mode", 16)
    if mode not in {b"mutation", b"reconcile"}:
        raise ValueError("invalid_session_review_mode")
    with review_lock(directory):
        if (attempt / "exit-code").exists() or (attempt / "cancelled").exists():
            return 2
        # A crashed or manually reopened attempt cannot invoke Wallet twice.
        _write(attempt / "review-started", str(time.time()))
        code, result = 2, {"error": "review_failed"}
        execution_started = False
        unknown = "status_unavailable" if mode == b"reconcile" else "outcome_unknown"
        previous = {}

        def interrupted(signum, frame):
            raise KeyboardInterrupt

        try:
            for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
                previous[sig] = signal.signal(sig, interrupted)
            if not sys.stdin.isatty() or not sys.stderr.isatty():
                result = {"error": "approval_required"}
            else:
                print(
                    "Check saved session result"
                    if mode == b"reconcile"
                    else "DevGraph session work → " + request.payload["state"].upper()
                )
                print("Session: " + request.id)
                print("Press Return when ready to start Wallet review. Ctrl-C cancels.")
                # This gate is not approval and never forwards text to Wallet.
                if input() != "":
                    result = {"error": "review_cancelled"}
                else:
                    if (
                        _read(directory / "request.json") != request_raw
                        or _read(directory / "idempotency.txt", 256) != key_raw
                    ):
                        raise ValueError("session_review_bundle_changed")
                    _write(attempt / "ready-at", str(time.time()))
                    execution_started = True
                    result = execute_local_credential_work(
                        request_file=directory / "request.json",
                        idempotency_key_file=directory / "idempotency.txt",
                        operation="session.state",
                        reconcile=mode == b"reconcile",
                    )
                    code = 0
        except (EOFError, KeyboardInterrupt):
            result = {"error": unknown if execution_started else "review_cancelled"}
            code = 130
        except LocalCredentialWorkAgentError as exc:
            candidate = str(exc).split(":", 1)[0]
            result = {"error": candidate if candidate in REVIEW_ERRORS else unknown}
        except Exception:
            result = {"error": unknown if execution_started else "review_failed"}
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            try:
                raw = json.dumps(result)
                if not isinstance(result, dict) or len(raw.encode()) > 65536:
                    raise ValueError("invalid_session_review_result")
            except (TypeError, ValueError):
                code, raw = (
                    2,
                    json.dumps({"error": unknown if execution_started else "review_failed"}),
                )
            _write(attempt / "result.json", raw)
            _write(attempt / "exit-code", str(code) + "\n")
        return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-directory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        code = run_review(args.attempt_directory)
    except (ValueError, OSError):
        print("Session review unavailable. Return to Pulse.", file=sys.stderr)
        return 2
    print("Return to Pulse to check the saved state. Press Return to close.")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
