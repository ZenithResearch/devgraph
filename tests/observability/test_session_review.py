"""Synthetic handoff tests: no native signer, approval or production mutation."""

import json
import subprocess
import sys

import pytest
from tests.observability.test_session_actions import REF, fixture

import devgraph.observability.session_review as runner
from devgraph.observability.session_actions import review_lock
from devgraph.ops.credential_work_agent import LocalCredentialWorkAgentError


def prepared(tmp_path):
    bridge, canonical, launched = fixture(tmp_path)
    result = bridge.begin(REF, "done", 1)
    return bridge, canonical, result, launched[0].parent


def interactive(monkeypatch, answer=lambda: ""):
    monkeypatch.setattr(runner.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(runner.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", answer)


def test_readiness_precedes_issuance_and_waiting_never_expires_into_parallel_review(
    monkeypatch, tmp_path
):
    bridge, canonical, first, attempt = prepared(tmp_path)
    calls = []

    def ready():
        assert not calls and not (attempt / "ready-at").exists()
        bridge.clock = lambda: 10**12
        assert bridge.status(first["ticket"])["review_stage"] == "waiting_to_start"
        assert bridge.begin(REF, "done", 1)["ticket"] == first["ticket"]
        assert len(bridge._attempts(attempt.parent)) == 1
        with pytest.raises(ValueError, match="active_close_terminal"):
            bridge.cancel(first["ticket"])
        return ""

    interactive(monkeypatch, ready)

    def execute(**kwargs):
        calls.append(kwargs)
        assert (attempt / "ready-at").exists()
        assert bridge.status(first["ticket"])["review_stage"] == "wallet_review"
        return {"synthetic": True}

    monkeypatch.setattr(runner, "execute_local_credential_work", execute)
    assert runner.run_review(attempt) == 0 and len(calls) == 1
    assert canonical["work_state"]["state"] == "unreviewed"
    assert calls[0]["operation"] == "session.state" and calls[0]["reconcile"] is False
    assert runner.run_review(attempt) == 2 and len(calls) == 1


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("wallet_review_timed_out", "wallet_review_timed_out"),
        ("outcome_unknown: private-material", "outcome_unknown"),
        ("not-allowlisted private-material", "outcome_unknown"),
    ],
)
def test_only_closed_errors_are_saved_without_tty_capture(monkeypatch, tmp_path, failure, expected):
    bridge, _, first, attempt = prepared(tmp_path)
    interactive(monkeypatch)

    def execute(**kwargs):
        raise LocalCredentialWorkAgentError(failure)

    monkeypatch.setattr(runner, "execute_local_credential_work", execute)
    assert runner.run_review(attempt) == 2
    raw = (attempt / "result.json").read_text()
    assert json.loads(raw) == {"error": expected} and "private-material" not in raw
    assert bridge.status(first["ticket"])["error"] == expected
    assert (attempt / "result.json").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("crashed", [False, True])
def test_inactive_cancel_prevents_late_window_and_retains_original_key(
    monkeypatch, tmp_path, crashed
):
    bridge, _, first, attempt = prepared(tmp_path)
    key = (attempt.parent / "idempotency.txt").read_bytes()
    if crashed:
        (attempt / "review-started").write_text("1")
        (attempt / "ready-at").write_text("2")
    with review_lock(attempt.parent):
        with pytest.raises(ValueError, match="active_close_terminal"):
            bridge.cancel(first["ticket"])
    result = bridge.cancel(first["ticket"])
    assert result["error"] == ("outcome_unknown" if crashed else "review_cancelled")
    monkeypatch.setattr(
        runner, "execute_local_credential_work", lambda **_: pytest.fail("late dispatch")
    )
    assert runner.run_review(attempt) == 2
    assert bridge.begin(REF, "done", 1)["ticket"] == first["ticket"]
    assert key == (attempt.parent / "idempotency.txt").read_bytes()


def test_terminal_cancel_is_durable_and_never_issues_credentials(monkeypatch, tmp_path):
    bridge, _, first, attempt = prepared(tmp_path)

    def cancel():
        raise KeyboardInterrupt

    interactive(monkeypatch, cancel)
    monkeypatch.setattr(
        runner, "execute_local_credential_work", lambda **_: pytest.fail("dispatch")
    )
    assert runner.run_review(attempt) == 130
    assert bridge.status(first["ticket"])["error"] == "review_cancelled"


def test_reconcile_keeps_status_only_semantics(monkeypatch, tmp_path):
    bridge, _, first, attempt = prepared(tmp_path)
    bridge.cancel(first["ticket"])
    bridge.reconcile(first["ticket"])
    attempt = bridge._attempts(attempt.parent)[-1]
    interactive(monkeypatch)
    calls = []
    monkeypatch.setattr(
        runner, "execute_local_credential_work", lambda **kw: calls.append(kw) or {}
    )
    assert runner.run_review(attempt) == 0 and calls[0]["reconcile"] is True


def test_non_tty_fails_before_starting_native_work(monkeypatch, tmp_path):
    bridge, _, first, attempt = prepared(tmp_path)
    monkeypatch.setattr(runner.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(
        runner, "execute_local_credential_work", lambda **_: pytest.fail("dispatch")
    )
    assert runner.run_review(attempt) == 2
    assert bridge.status(first["ticket"])["error"] == "approval_required"


def test_real_module_entrypoint_without_tty_never_prompts_or_dispatches(tmp_path):
    bridge, _, first, attempt = prepared(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "devgraph.observability.session_review",
            "--attempt-directory",
            str(attempt),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert bridge.status(first["ticket"])["error"] == "approval_required"
    assert not (attempt / "ready-at").exists()


def test_changed_bundle_after_ready_gate_cannot_issue(monkeypatch, tmp_path):
    bridge, _, first, attempt = prepared(tmp_path)

    def changed():
        (attempt.parent / "idempotency.txt").write_text("changed-private-material")
        return ""

    interactive(monkeypatch, changed)
    monkeypatch.setattr(
        runner, "execute_local_credential_work", lambda **_: pytest.fail("dispatch")
    )
    assert runner.run_review(attempt) == 2
    assert bridge.status(first["ticket"])["error"] == "review_failed"
    assert "changed-private-material" not in (attempt / "result.json").read_text()


def test_inactive_cancel_preserves_result_left_by_crash_and_cannot_mark_committed(tmp_path):
    bridge, _, first, attempt = prepared(tmp_path)
    (attempt / "result.json").write_text('{"receipt":{"receipt_id":"synthetic"}}')
    (attempt / "result.json").chmod(0o600)
    original = (attempt / "result.json").read_bytes()
    bridge.cancel(first["ticket"])
    assert (attempt / "result.json").read_bytes() == original
    assert bridge.status(first["ticket"])["phase"] != "committed"


@pytest.mark.parametrize("failure", ["interrupt", "unexpected", "large", "unserializable"])
def test_post_execution_failures_remain_unknown_and_keep_retry_identity(
    monkeypatch, tmp_path, failure
):
    bridge, _, first, attempt = prepared(tmp_path)
    key = (attempt.parent / "idempotency.txt").read_bytes()
    interactive(monkeypatch)

    def executed(**kwargs):
        if failure == "interrupt":
            raise KeyboardInterrupt
        if failure == "unexpected":
            raise RuntimeError("private-material")
        return {"data": "x" * 65536 if failure == "large" else object()}

    monkeypatch.setattr(runner, "execute_local_credential_work", executed)
    assert runner.run_review(attempt) != 0
    assert bridge.status(first["ticket"])["error"] == "outcome_unknown"
    assert (attempt.parent / "idempotency.txt").read_bytes() == key
    bridge.reconcile(first["ticket"])
    assert bridge._attempts(attempt.parent)[-1].joinpath("review-mode").read_text() == "reconcile"


def test_completed_worker_lock_prevents_creating_or_launching_next_attempt(tmp_path):
    bridge, _, first, attempt = prepared(tmp_path)
    bridge.cancel(first["ticket"])
    with review_lock(attempt.parent):
        with pytest.raises(ValueError, match="active_close_terminal"):
            bridge.begin(REF, "done", 1)
        assert len(bridge._attempts(attempt.parent)) == 1


@pytest.mark.parametrize("error", [["private-material"], {"private-material": True}])
def test_malformed_result_error_is_not_disclosed_or_committed(tmp_path, error):
    bridge, _, first, attempt = prepared(tmp_path)
    (attempt / "result.json").write_text(json.dumps({"error": error}))
    (attempt / "result.json").chmod(0o600)
    (attempt / "exit-code").write_text("2")
    (attempt / "exit-code").chmod(0o600)
    value = bridge.status(first["ticket"])
    assert value["phase"] != "committed" and "error" not in value
    assert "private-material" not in json.dumps(value)


def test_interrupted_reconciliation_preserves_status_unavailable(monkeypatch, tmp_path):
    bridge, _, first, attempt = prepared(tmp_path)
    bridge.cancel(first["ticket"])
    bridge.reconcile(first["ticket"])
    attempt = bridge._attempts(attempt.parent)[-1]
    interactive(monkeypatch)

    def interrupted(**kwargs):
        assert kwargs["reconcile"] is True
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "execute_local_credential_work", interrupted)
    assert runner.run_review(attempt) == 130
    assert bridge.status(first["ticket"])["error"] == "status_unavailable"
