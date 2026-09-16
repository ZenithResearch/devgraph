from __future__ import annotations

import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Barrier, Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from devgraph.api.audit import RetainedAuditMiddleware
from devgraph.ops.retained_audit import AuditUnavailable, RetainedAuditLog


def events(log):
    with sqlite3.connect(log.path) as connection:
        return [json.loads(row[0]) for row in connection.execute("SELECT event FROM events")]


def record(log):
    log.record(
        actor_id="actor-secret",
        session_id="session-secret",
        correlation_id="correlation-secret",
        category="read",
        operation="get_work_object",
        safe_summary={"title": "private-work-content", "token": "secret-token"},
    )


def test_audit_uses_service_subtree_without_writing_mount_root(tmp_path):
    service = tmp_path / "devgraph"
    service.mkdir(mode=0o700)
    tmp_path.chmod(0o555)
    try:
        log = RetainedAuditLog(tmp_path)
        record(log)
        assert log.path == service / "audit/events.sqlite3"
        assert len(events(log)) == 1
    finally:
        tmp_path.chmod(0o700)


def test_persists_after_reopen_without_payloads_or_raw_authority(tmp_path):
    log = RetainedAuditLog(tmp_path)
    record(log)
    reopened = RetainedAuditLog(tmp_path)
    assert len(events(reopened)) == 1
    raw = reopened.path.read_bytes()
    for secret in (
        b"actor-secret",
        b"session-secret",
        b"correlation-secret",
        b"private-work-content",
        b"secret-token",
    ):
        assert secret not in raw


def test_transaction_rollback_and_nested_publish(tmp_path):
    log = RetainedAuditLog(tmp_path)
    with pytest.raises(ValueError), log.transaction():
        record(log)
        raise ValueError("rollback")
    assert events(log) == []
    with log.transaction(), log.transaction():
        record(log)
        assert events(log) == []
    assert len(events(log)) == 1


def test_retention_count_age_and_concurrent_writers(tmp_path, monkeypatch):
    monkeypatch.setattr("devgraph.ops.retained_audit.MAX_EVENTS", 10)
    now = [10000000]
    log = RetainedAuditLog(tmp_path, clock=lambda: now[0])
    second = RetainedAuditLog(tmp_path, clock=lambda: now[0])
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda i: record(log if i % 2 else second), range(40)))
    assert len(events(log)) == 10
    now[0] += 31 * 86400
    log.check()
    assert events(log) == []


def test_concurrent_commit_removing_open_journal_is_revalidated(tmp_path, monkeypatch):
    log = RetainedAuditLog(tmp_path)
    journal = log.directory / "events.sqlite3-journal"
    open_file = os.open
    removed = []
    # A separate SQLite writer really creates and removes the rollback journal;
    # the interception only schedules its commit between our open and fstat.
    with closing(sqlite3.connect(log.path, isolation_level=None, check_same_thread=False)) as other:
        other.execute("BEGIN IMMEDIATE")
        other.execute("UPDATE health SET checked = checked + 1")
        assert journal.is_file()

        def commit_after_journal_open(path, flags, *args, **kwargs):
            fd = open_file(path, flags, *args, **kwargs)
            if Path(path) == journal and not removed:
                other.commit()
                removed.append(os.fstat(fd).st_nlink)
                assert not journal.exists()
            return fd

        monkeypatch.setattr(os, "open", commit_after_journal_open)
        log.append([{"after_concurrent_commit": True}])
    assert removed == [0]
    assert events(log) == [{"after_concurrent_commit": True}]


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "permissions"])
def test_revalidates_replacement_of_unlinked_journal(tmp_path, monkeypatch, unsafe):
    log = RetainedAuditLog(tmp_path)
    journal = log.directory / "events.sqlite3-journal"
    journal.touch(mode=0o600)
    replacement = tmp_path / "replacement"
    replacement.touch(mode=0o600)
    open_file = os.open
    replaced = []

    def replace_after_journal_open(path, flags, *args, **kwargs):
        fd = open_file(path, flags, *args, **kwargs)
        if Path(path) == journal and not replaced:
            journal.unlink()
            assert os.fstat(fd).st_nlink == 0
            if unsafe == "symlink":
                journal.symlink_to(replacement)
            elif unsafe == "hardlink":
                journal.hardlink_to(replacement)
            else:
                os.close(open_file(journal, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
                journal.chmod(0o644)
            replaced.append(True)
        return fd

    monkeypatch.setattr(os, "open", replace_after_journal_open)
    with pytest.raises(AuditUnavailable):
        log.append([{"must_not_commit": True}])
    assert replaced == [True]
    journal.unlink()
    assert events(log) == []


def test_journal_revalidation_is_bounded_during_repeated_unlink(tmp_path, monkeypatch):
    log = RetainedAuditLog(tmp_path)
    journal = log.directory / "events.sqlite3-journal"
    journal.touch(mode=0o600)
    open_file = os.open
    removed = []

    def remove_each_open_journal(path, flags, *args, **kwargs):
        fd = open_file(path, flags, *args, **kwargs)
        if Path(path) == journal:
            journal.unlink()
            os.close(open_file(journal, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            removed.append(True)
        return fd

    monkeypatch.setattr(os, "open", remove_each_open_journal)
    with pytest.raises(AuditUnavailable):
        log.append([{"must_not_commit": True}])
    assert 1 < len(removed) <= 3
    journal.unlink()
    assert events(log) == []


@pytest.mark.parametrize("name", ["events.sqlite3", "events.sqlite3-wal", "events.sqlite3-shm"])
def test_unlinked_nonjournal_store_file_still_fails_closed(tmp_path, monkeypatch, name):
    log = RetainedAuditLog(tmp_path)
    target = log.directory / name
    target.touch(mode=0o600, exist_ok=True)
    open_file = os.open
    removed = []

    def remove_after_store_open(path, flags, *args, **kwargs):
        fd = open_file(path, flags, *args, **kwargs)
        if Path(path) == target:
            target.unlink()
            removed.append(os.fstat(fd).st_nlink)
        return fd

    monkeypatch.setattr(os, "open", remove_after_store_open)
    with pytest.raises(AuditUnavailable):
        log.append([{"must_not_commit": True}])
    assert removed == [0]
    assert not target.exists()


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "permissions", "directory"])
def test_rejects_unsafe_store(tmp_path, unsafe):
    log = RetainedAuditLog(tmp_path)
    if unsafe == "symlink":
        log.path.rename(tmp_path / "original")
        log.path.symlink_to(tmp_path / "original")
    elif unsafe == "hardlink":
        (tmp_path / "alias").hardlink_to(log.path)
    elif unsafe == "directory":
        log.directory.chmod(0o755)
    else:
        log.path.chmod(0o644)
    with pytest.raises(AuditUnavailable):
        record(log)


def test_http_denial_is_retained_without_untrusted_content(tmp_path):
    log = RetainedAuditLog(tmp_path)
    app = FastAPI()
    app.add_middleware(RetainedAuditMiddleware, audit_log=log)
    with TestClient(app) as client:
        response = client.get(
            "/secret-path?secret=hidden", headers={"Authorization": "secret-proof"}
        )
    assert response.status_code == 404
    assert [item["phase"] for item in events(log)] == ["attempt", "response"]
    assert events(log)[1]["status"] == 404
    raw = log.path.read_bytes()
    assert b"secret-path" not in raw and b"hidden" not in raw and b"secret-proof" not in raw


def test_audit_failure_denies_before_delegate_and_after_commit_reports_retry(tmp_path, monkeypatch):
    log = RetainedAuditLog(tmp_path)
    app = FastAPI()
    app.add_middleware(RetainedAuditMiddleware, audit_log=log)
    called = []

    @app.post("/mutation")
    def mutation():
        called.append(True)
        log.path.chmod(0o644)
        return {"committed": True}

    with TestClient(app) as client:
        response = client.post("/mutation")
        assert response.status_code == 503
        assert "a commit may exist" in response.json()["detail"]
        assert len(called) == 1
        assert client.post("/mutation").status_code == 503
        assert len(called) == 1


def test_concurrent_submissions_share_durable_commit_without_splitting_transactions(
    tmp_path, monkeypatch
):
    log = RetainedAuditLog(tmp_path)
    monkeypatch.setattr("devgraph.ops.retained_audit.BATCH_WINDOW_SECONDS", 0.05)
    connection = log._connection
    commits = []

    @contextmanager
    def counted_connection():
        with connection() as database:
            yield database
        commits.append(True)

    monkeypatch.setattr(log, "_connection", counted_connection)
    barrier = Barrier(12)

    def submit(index):
        barrier.wait(timeout=3)
        log.append([{"group": index, "entry": 1}, {"group": index, "entry": 2}])
        # Returning means our complete transaction is already visible durably.
        assert [item["entry"] for item in events(log) if item["group"] == index] == [1, 2]

    with ThreadPoolExecutor(12) as pool:
        list(pool.map(submit, range(12)))
    assert len(commits) < 12
    persisted = events(log)
    assert len(persisted) == 24
    for offset in range(0, len(persisted), 2):
        assert persisted[offset]["group"] == persisted[offset + 1]["group"]
        assert [item["entry"] for item in persisted[offset:offset + 2]] == [1, 2]


def test_failed_shared_transaction_rolls_back_and_fails_every_participant(tmp_path, monkeypatch):
    log = RetainedAuditLog(tmp_path)
    monkeypatch.setattr("devgraph.ops.retained_audit.BATCH_WINDOW_SECONDS", 0.05)
    prune = log._prune
    calls = []

    def fail_after_inserts(connection):
        calls.append(True)
        prune(connection)
        if len(calls) == 2:
            raise sqlite3.OperationalError("simulated disk failure after batch insert")

    monkeypatch.setattr(log, "_prune", fail_after_inserts)
    barrier = Barrier(8)

    def submit(index):
        barrier.wait(timeout=3)
        with pytest.raises(AuditUnavailable):
            log.append([{"group": index}])

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(submit, range(8)))
    assert len(calls) == 2, "all simultaneous participants used one transaction"
    assert events(log) == []
    monkeypatch.setattr(log, "_prune", prune)
    log.append([{"recovered": True}])
    assert events(log) == [{"recovered": True}]


def test_stalled_writer_bounds_callers_fails_closed_and_recovers(tmp_path, monkeypatch):
    log = RetainedAuditLog(tmp_path)
    monkeypatch.setattr("devgraph.ops.retained_audit.AUDIT_WAIT_SECONDS", 0.08)
    monkeypatch.setattr("devgraph.ops.retained_audit.BATCH_WINDOW_SECONDS", 0.01)
    connection = log._connection
    entered, release = Event(), Event()

    @contextmanager
    def stalled_connection():
        entered.set()
        assert release.wait(timeout=3), "test must release its simulated disk stall"
        with connection() as database:
            yield database

    monkeypatch.setattr(log, "_connection", stalled_connection)
    try:
        started = time.monotonic()
        with pytest.raises(AuditUnavailable):
            log.append([{"attempt": "timed-out"}])
        assert entered.is_set()
        assert time.monotonic() - started < 0.5
        started = time.monotonic()
        with pytest.raises(AuditUnavailable):
            log.check()
        assert time.monotonic() - started < 0.04, "stalled-writer admission must fail immediately"
        with log._admission:
            assert log._pending_count == 1
    finally:
        release.set()
    # An in-flight durable operation can settle after timeout, but must never
    # turn its already-failed caller into an admitted HTTP request.
    until = time.monotonic() + 1
    while time.monotonic() < until:
        with log._admission:
            if not log._admission_blocked:
                break
        time.sleep(0.005)
    monkeypatch.setattr(log, "_connection", connection)
    # The short timeout above proves stalled callers fail closed quickly. The
    # recovery proof should allow a normal durable SQLite write on slower CI.
    monkeypatch.setattr("devgraph.ops.retained_audit.AUDIT_WAIT_SECONDS", 1.0)
    log.check()


def test_queue_capacity_rejects_overload_without_waiting_or_dropping_success(
    tmp_path, monkeypatch
):
    log = RetainedAuditLog(tmp_path)
    monkeypatch.setattr("devgraph.ops.retained_audit.MAX_PENDING_SUBMISSIONS", 1)
    connection = log._connection
    entered, release = Event(), Event()

    @contextmanager
    def held_connection():
        entered.set()
        assert release.wait(timeout=3)
        with connection() as database:
            yield database

    monkeypatch.setattr(log, "_connection", held_connection)
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(log.append, [{"admitted": True}])
        try:
            assert entered.wait(timeout=1)
            started = time.monotonic()
            with pytest.raises(AuditUnavailable):
                log.append([{"overloaded": True}])
            assert time.monotonic() - started < 0.1
        finally:
            release.set()
        first.result(timeout=1)
    assert events(log) == [{"admitted": True}]


def test_health_check_and_request_event_both_wait_for_shared_durable_commit(tmp_path, monkeypatch):
    log = RetainedAuditLog(tmp_path)
    monkeypatch.setattr("devgraph.ops.retained_audit.BATCH_WINDOW_SECONDS", 0.05)
    connection = log._connection
    entered, release = Event(), Event()

    @contextmanager
    def held_commit():
        with connection() as database:
            yield database
            entered.set()
            assert release.wait(timeout=3)

    monkeypatch.setattr(log, "_connection", held_commit)
    with ThreadPoolExecutor(2) as pool:
        checking = pool.submit(log.check)
        appending = pool.submit(log.append, [{"attempt": True}])
        try:
            assert entered.wait(timeout=1)
            assert not checking.done()
            assert not appending.done()
            assert events(log) == [], "uncommitted attempt must not be observable"
        finally:
            release.set()
        checking.result(timeout=1)
        appending.result(timeout=1)
    assert events(log) == [{"attempt": True}]


def test_writer_exits_when_idle_and_restarts_for_next_submission(tmp_path, monkeypatch):
    monkeypatch.setattr("devgraph.ops.retained_audit.WRITER_IDLE_SECONDS", 0.03)
    log = RetainedAuditLog(tmp_path)
    until = time.monotonic() + 1
    while log._writer_running and time.monotonic() < until:
        time.sleep(0.005)
    assert not log._writer_running
    log.append([{"next": True}])
    assert events(log) == [{"next": True}]


def test_durability_and_secure_deletion_pragmas_remain_enabled(tmp_path):
    log = RetainedAuditLog(tmp_path)
    with log._connection() as connection:
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA fullfsync").fetchone()[0] == 1
        assert connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_http_attempt_commits_before_delegate_and_response_commits_before_send(
    tmp_path, monkeypatch
):
    log = RetainedAuditLog(tmp_path)
    app = FastAPI()
    app.add_middleware(RetainedAuditMiddleware, audit_log=log)
    called = []

    @app.get("/read")
    def read():
        called.append(True)
        return {"read": True}

    connection = log._connection
    entered = [Event(), Event()]
    release = [Event(), Event()]
    invocations = []

    @contextmanager
    def held_commit():
        phase = len(invocations)
        invocations.append(phase)
        with connection() as database:
            yield database
            entered[phase].set()
            assert release[phase].wait(timeout=3)

    monkeypatch.setattr(log, "_connection", held_commit)
    with TestClient(app) as client, ThreadPoolExecutor(1) as pool:
        response = pool.submit(client.get, "/read")
        try:
            assert entered[0].wait(timeout=1)
            assert called == []
            assert not response.done()
            release[0].set()
            assert entered[1].wait(timeout=1)
            assert called == [True]
            assert not response.done()
        finally:
            for gate in release:
                gate.set()
        assert response.result(timeout=1).status_code == 200
    assert [item["phase"] for item in events(log)] == ["attempt", "response"]


def test_queued_submission_that_times_out_is_cancelled_before_disk_write(tmp_path, monkeypatch):
    log = RetainedAuditLog(tmp_path)
    monkeypatch.setattr("devgraph.ops.retained_audit.AUDIT_WAIT_SECONDS", 0.1)
    connection = log._connection
    entered, release = Event(), Event()

    @contextmanager
    def stalled_connection():
        entered.set()
        assert release.wait(timeout=3)
        with connection() as database:
            yield database

    monkeypatch.setattr(log, "_connection", stalled_connection)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(log.append, [{"phase": "in-flight"}])
        try:
            assert entered.wait(timeout=1)
            second = pool.submit(log.append, [{"phase": "queued"}])
            with pytest.raises(AuditUnavailable):
                first.result(timeout=1)
            with pytest.raises(AuditUnavailable):
                second.result(timeout=1)
        finally:
            release.set()
    until = time.monotonic() + 1
    while time.monotonic() < until:
        with log._admission:
            if not log._pending_count:
                break
        time.sleep(0.005)
    assert {item["phase"] for item in events(log)} <= {"in-flight"}
