"""Private, durable, bounded operational audit, separate from mutation receipts.

Only fixed operation names and hashed authority identifiers are retained here.
Request content, credentials, proofs, paths, query strings and response bodies
never enter this store. SQLite serializes writers across API processes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Condition, Event, Lock, Thread

from devgraph.auth.enforcement import AuditLog, AuditRecord
from devgraph.ops.local_path_integrity import (
    LocalPathIntegrityError,
    require_file_descriptor_without_acl,
    require_receiver_directory_path,
)

RETENTION_SECONDS = 30 * 86400
MAX_EVENTS = 100_000
MAX_PAGES = 32768  # 128 MiB with 4096-byte pages, plus one bounded rollback journal.
AUDIT_WAIT_SECONDS = 5.0
BATCH_WINDOW_SECONDS = 0.01
WRITER_IDLE_SECONDS = 0.5
MAX_PENDING_SUBMISSIONS = 16  # Leave capacity in the shared HTTP worker pool during a stall.
MAX_PENDING_EVENTS = 1024
MAX_BATCH_SUBMISSIONS = 32
MAX_BATCH_EVENTS = 256
MAX_JOURNAL_VALIDATION_ATTEMPTS = 3
AUDIT_RELATIVE = Path("devgraph/audit")
_NAME = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,79}\Z")


class AuditUnavailable(RuntimeError):
    def __init__(self):
        super().__init__(
            "retained audit unavailable; retry mutations with the same idempotency key"
        )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(eq=False)
class _Submission:
    entries: tuple[tuple[float, str], ...]
    checked: float | None = None
    done: Event = field(default_factory=Event)
    successful: bool = False
    cancelled: bool = False


class RetainedAuditLog(AuditLog):
    """Retain at most 30 days / 100,000 events; fail closed on storage failure."""

    def __init__(self, data_root: Path, *, clock=time.time):
        super().__init__()
        self.data_root = data_root
        self.directory = data_root / AUDIT_RELATIVE
        self.path = self.directory / "events.sqlite3"
        self.clock = clock
        # The compatibility records lock must never wait behind filesystem I/O.
        self._storage_lock = Lock()
        self._admission = Condition()
        self._pending: deque[_Submission] = deque()
        self._pending_count = 0
        self._pending_events = 0
        self._writer_running = False
        self._admission_blocked = False
        try:
            # Validate the root before creating anything; do not chmod existing paths.
            require_receiver_directory_path(data_root, AUDIT_RELATIVE, missing_ok=True)
            self.directory.parent.mkdir(mode=0o700, exist_ok=True)
            self.directory.mkdir(mode=0o700, exist_ok=True)
            self._validate()
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            with self._connection() as connection:
                connection.execute("PRAGMA page_size=4096")
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS events "
                    "(seq INTEGER PRIMARY KEY AUTOINCREMENT, occurred REAL NOT NULL, "
                    "event TEXT NOT NULL CHECK(length(event) <= 2048))"
                )
                connection.execute("CREATE INDEX IF NOT EXISTS events_time ON events(occurred)")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS health (id INTEGER PRIMARY KEY, checked REAL)"
                )
            self.check()
        except (OSError, sqlite3.Error, LocalPathIntegrityError):
            raise AuditUnavailable() from None

    def _validate(self):
        require_receiver_directory_path(self.data_root, AUDIT_RELATIVE, missing_ok=False)
        if stat.S_IMODE(self.directory.stat().st_mode) != 0o700:
            raise AuditUnavailable()
        for name in (
            "events.sqlite3",
            "events.sqlite3-journal",
            "events.sqlite3-wal",
            "events.sqlite3-shm",
        ):
            self._validate_file(name)

    def _validate_file(self, name: str) -> None:
        for _ in range(MAX_JOURNAL_VALIDATION_ATTEMPTS):
            try:
                fd = os.open(self.directory / name, os.O_RDONLY | os.O_NOFOLLOW)
            except FileNotFoundError:
                return
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077
                ):
                    raise AuditUnavailable()
                require_file_descriptor_without_acl(fd)
                if info.st_nlink == 1:
                    return
                if name != "events.sqlite3-journal" or info.st_nlink != 0:
                    raise AuditUnavailable()
                # Another SQLite connection may commit in DELETE mode between
                # open and fstat, unlinking this otherwise-safe descriptor.
                # Reopen the path: absence is normal, but any replacement must
                # pass every check. Bound retries if journal churn continues.
            finally:
                os.close(fd)
        raise AuditUnavailable()

    @contextmanager
    def _connection(self):
        if not self._storage_lock.acquire(timeout=AUDIT_WAIT_SECONDS):
            raise AuditUnavailable()
        try:
            connection = None
            try:
                self._validate()
                connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA fullfsync=ON")
                connection.execute("PRAGMA secure_delete=ON")
                connection.execute(f"PRAGMA max_page_count={MAX_PAGES}")
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except (OSError, sqlite3.Error, LocalPathIntegrityError):
                raise AuditUnavailable() from None
            finally:
                if connection is not None:
                    connection.close()
        finally:
            self._storage_lock.release()

    def _prune(self, connection):
        connection.execute(
            "DELETE FROM events WHERE occurred < ?", (self.clock() - RETENTION_SECONDS,)
        )
        connection.execute(
            "DELETE FROM events WHERE seq <= (SELECT COALESCE(MAX(seq),0) - ? FROM events)",
            (MAX_EVENTS,),
        )

    def check(self):
        """Prove a durable write and enforce age retention even without traffic."""
        self._submit(_Submission((), checked=self.clock()))

    def append(self, entries: list[dict]):
        try:
            if len(entries) > MAX_BATCH_EVENTS:
                raise AuditUnavailable()
            # Copy before admission so caller mutation cannot alter a queued record.
            serialized = tuple(
                (self.clock(), json.dumps(entry, sort_keys=True, separators=(",", ":")))
                for entry in entries
            )
            if any(len(event) > 2048 for _, event in serialized):
                raise AuditUnavailable()
        except (TypeError, ValueError):
            raise AuditUnavailable() from None
        self._submit(_Submission(serialized))

    def _submit(self, submission: _Submission) -> None:
        """Wait for durable commit, or fail closed within a bounded deadline.

        A timed-out in-flight commit may still finish. That caller is never
        released as successful; middleware therefore cannot admit its request
        or send an unaudited response. Further admission fails immediately until
        the writer settles, instead of filling the shared HTTP worker pool.
        """
        with self._admission:
            if (
                self._admission_blocked
                or self._pending_count >= MAX_PENDING_SUBMISSIONS
                or self._pending_events + len(submission.entries) > MAX_PENDING_EVENTS
            ):
                raise AuditUnavailable()
            self._pending.append(submission)
            self._pending_count += 1
            self._pending_events += len(submission.entries)
            if not self._writer_running:
                self._writer_running = True
                try:
                    Thread(target=self._write_batches, name="devgraph-audit", daemon=True).start()
                except RuntimeError:
                    self._writer_running = False
                    self._pending.remove(submission)
                    self._pending_count -= 1
                    self._pending_events -= len(submission.entries)
                    raise AuditUnavailable() from None
            self._admission.notify()
        if not submission.done.wait(AUDIT_WAIT_SECONDS):
            with self._admission:
                if not submission.done.is_set():
                    submission.cancelled = True
                    self._admission_blocked = True
                    raise AuditUnavailable()
        if not submission.successful or submission.cancelled:
            raise AuditUnavailable()

    def _finish(self, submission: _Submission, *, successful: bool) -> None:
        """Called under admission lock after commit/rollback (or cancellation)."""
        submission.successful = successful and not submission.cancelled
        self._pending_count -= 1
        self._pending_events -= len(submission.entries)
        submission.done.set()

    def _write_batches(self) -> None:
        while True:
            with self._admission:
                while not self._pending:
                    if not self._admission.wait(timeout=WRITER_IDLE_SECONDS) and not self._pending:
                        self._writer_running = False
                        return
                until = time.monotonic() + BATCH_WINDOW_SECONDS
                while len(self._pending) < MAX_BATCH_SUBMISSIONS:
                    remaining = until - time.monotonic()
                    if remaining <= 0:
                        break
                    self._admission.wait(timeout=remaining)
                batch: list[_Submission] = []
                event_count = 0
                while self._pending and len(batch) < MAX_BATCH_SUBMISSIONS:
                    submission = self._pending[0]
                    if submission.cancelled:
                        self._pending.popleft()
                        self._finish(submission, successful=False)
                        continue
                    if event_count + len(submission.entries) > MAX_BATCH_EVENTS:
                        break
                    self._pending.popleft()
                    batch.append(submission)
                    event_count += len(submission.entries)
            successful = False
            try:
                if batch:
                    with self._connection() as connection:
                        self._prune(connection)
                        for submission in batch:
                            connection.executemany(
                                "INSERT INTO events(occurred, event) VALUES (?, ?)",
                                submission.entries,
                            )
                            if submission.checked is not None:
                                connection.execute(
                                    "INSERT OR REPLACE INTO health VALUES (1, ?)",
                                    (submission.checked,),
                                )
                        self._prune(connection)
                    successful = True
            except Exception:
                # A batch failure belongs to every participant. Never release
                # a request on partial/failed commit or expose filesystem errors.
                successful = False
            finally:
                with self._admission:
                    for submission in batch:
                        self._finish(submission, successful=successful)
                    self._admission_blocked = False

    def _publish(self, entries: list[AuditRecord]) -> None:
        self.append(
            [
                {
                    "schema": "devgraph.audit.v1",
                    "type": "authorized_operation",
                    "operation": entry.operation if _NAME.fullmatch(entry.operation) else "unknown",
                    "category": entry.category if _NAME.fullmatch(entry.category) else "unknown",
                    "actor_sha256": _digest(entry.actor_id),
                    "session_sha256": _digest(entry.session_id),
                    "correlation_sha256": _digest(entry.correlation_id),
                }
                for entry in entries
            ]
        )
        with self._lock:
            self.records.extend(entries)
            del self.records[:-100]  # Compatibility/debug view is bounded too.

    def request_event(
        self,
        *,
        request_id: str,
        method: str,
        phase: str,
        route: str = "unmatched",
        status: int | None = None,
    ):
        self.append(
            [
                {
                    "schema": "devgraph.audit.v1",
                    "type": "http_request",
                    "request_id": request_id,
                    "phase": phase,
                    "method": method
                    if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                    else "OTHER",
                    "route": route if _NAME.fullmatch(route) else "unmatched",
                    "status": status,
                }
            ]
        )
