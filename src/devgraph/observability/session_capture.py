"""Metadata-only source reducers and private durable dispatch outbox.

This journal is delivery evidence, not a second Work tracker. It does not ingest
transcripts, retain stdout/commands, or mutate DevGraph. Producers must provide
explicit source namespaces and Work scope; no cwd/title inference is performed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from devgraph.observability.session_ref import (
    SessionIdentity,
    SessionLink,
    opaque,
    timestamp,
    work_ref,
)

MAX_EVENT_BYTES = 65_536


def _unique(pairs: list[tuple]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_session_event_field")
        result[key] = value
    return result


def native_session_id(application: str, line: str) -> str | None:
    """Read only typed identity events; never regex-match narrative output."""
    if len(line.encode()) > MAX_EVENT_BYTES:
        return None
    try:
        data = json.loads(line, object_pairs_hook=_unique)
        if not isinstance(data, dict):
            return None
        value = None
        if application == "codex" and data.get("type") == "thread.started":
            value = data.get("thread_id")
        elif (
            application == "claude-code"
            and data.get("parent_tool_use_id") is None
            and (
                (data.get("type") == "system" and data.get("subtype") == "init")
                or data.get("type") == "result"
            )
        ):
            value = data.get("session_id")
        return None if value is None else opaque(value)
    except (ValueError, TypeError, RecursionError):
        return None


@dataclass(frozen=True)
class Dispatch:
    attempt_id: str
    source_instance_id: str
    application: str
    work_refs: tuple[str, ...] = ()
    parent: SessionIdentity | None = None
    tool_call_id: str | None = None
    persistence: str = "unknown"

    def __post_init__(self) -> None:
        opaque(self.attempt_id)
        # Validate the source/application without inventing a native conversation ID.
        SessionIdentity(self.application, self.source_instance_id, "pending")
        if not isinstance(self.work_refs, tuple) or len(self.work_refs) > 32:
            raise ValueError("invalid_session_work_scope")
        for value in self.work_refs:
            work_ref(value)
        if len(set(self.work_refs)) != len(self.work_refs):
            raise ValueError("duplicate_session_work_ref")
        if self.parent is not None and not isinstance(self.parent, SessionIdentity):
            raise ValueError("invalid_session_parent")
        if self.tool_call_id is not None:
            opaque(self.tool_call_id)
        if self.persistence not in {"unknown", "requested", "ephemeral"}:
            raise ValueError("invalid_session_persistence")


class SessionJournal:
    """Explicit opt-in directory, owner-only, immutable idempotent events.

    SQLite transactions are durably committed before a child is launched or
    an observed identifier is acknowledged. No existing source DB is opened.
    """

    def __init__(self, directory: Path, *, read_only: bool = False) -> None:
        directory = Path(directory).absolute()
        # Reject symlinked ancestors, including a symlinked journal root.
        if any(p.is_symlink() for p in (directory, *directory.parents)):
            raise ValueError("unsafe_session_journal_directory")
        if not read_only:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("unsafe_session_journal_directory")
        path = directory / "session-events.sqlite3"
        flags = os.O_RDONLY if read_only else os.O_CREAT | os.O_RDWR
        fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise ValueError("unsafe_session_journal_file")
            if stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1:
                raise ValueError("unsafe_session_journal_file")
        finally:
            os.close(fd)
        self.db = sqlite3.connect(
            path.as_uri() + ("?mode=ro" if read_only else "?mode=rw"), uri=True, timeout=2
        )
        if read_only:
            return
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "event_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL, "
            "recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))"
        )
        self.db.execute("CREATE TABLE IF NOT EXISTS launches (attempt_id TEXT PRIMARY KEY)")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def _write(self, key: str, payload: dict) -> None:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(raw.encode()) > MAX_EVENT_BYTES:
            raise ValueError("session_event_too_large")
        with self.db:
            self.db.execute(
                "INSERT INTO events(event_key,payload) VALUES (?,?) "
                "ON CONFLICT(event_key) DO NOTHING",
                (key, raw),
            )
            existing = self.db.execute("SELECT payload FROM events WHERE event_key=?", (key,))
            old = existing.fetchone()
            if old is None or old[0] != raw:
                raise ValueError("session_event_conflict")

    def dispatch(self, dispatch: Dispatch) -> None:
        self._write(
            dispatch.attempt_id + ":dispatch",
            {
                "schema": "devgraph.session-capture.v1",
                "type": "dispatch",
                **asdict(dispatch),
            },
        )

    def claim_launch(self, attempt_id: str) -> None:
        self._dispatch(attempt_id)
        try:
            with self.db:
                self.db.execute("INSERT INTO launches(attempt_id) VALUES (?)", (attempt_id,))
        except sqlite3.IntegrityError:
            raise ValueError("session_attempt_already_launched") from None

    def _dispatch(self, attempt_id: str) -> dict:
        opaque(attempt_id)
        row = self.db.execute(
            "SELECT payload FROM events WHERE event_key=?", (attempt_id + ":dispatch",)
        ).fetchone()
        if row is None:
            raise ValueError("unknown_session_attempt")
        return json.loads(row[0])

    def observe(self, attempt_id: str, external_session_id: str) -> SessionIdentity:
        dispatch = self._dispatch(attempt_id)
        identity = SessionIdentity(
            dispatch["application"], dispatch["source_instance_id"], external_session_id
        )
        self._write(
            attempt_id + ":native",
            {
                "schema": "devgraph.session-capture.v1",
                "type": "native_session",
                "attempt_id": attempt_id,
                "identity": asdict(identity),
            },
        )
        return identity

    def attempt(self, attempt_id: str) -> dict:
        dispatch = self._dispatch(attempt_id)
        native = self.db.execute(
            "SELECT payload,recorded_at FROM events WHERE event_key=?", (attempt_id + ":native",)
        ).fetchone()
        if native is None:
            raise ValueError("session_native_identity_unresolved")
        return {"dispatch": dispatch, "native": json.loads(native[0]), "observed_at": native[1]}

    def exited(self, attempt_id: str, returncode: int) -> None:
        self._dispatch(attempt_id)
        if type(returncode) is not int or not -(2**31) <= returncode < 2**31:
            raise ValueError("invalid_session_exit_code")
        self._write(
            attempt_id + ":exit",
            {
                "schema": "devgraph.session-capture.v1",
                "type": "process_exit",
                "attempt_id": attempt_id,
                "returncode": returncode,
            },
        )

    def observe_link(
        self,
        origin: SessionIdentity,
        target: SessionIdentity,
        *,
        relation: str,
        source_instance_id: str,
        event_id: str,
    ) -> dict:
        """Retain first observation of an exact persisted source binding, across restarts."""
        key = "source-link:" + source_instance_id + ":" + event_id
        observed = datetime.now(timezone.utc)
        link = SessionLink(
            source_instance_id,
            event_id,
            event_id,
            relation,
            origin,
            target,
            observed,
            "source_observed",
        )
        value = {
            "schema": "devgraph.session-link-observation.v1",
            "type": "source_link",
            **asdict(link),
            "observed_at": timestamp(observed),
        }
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
        with self.db:
            self.db.execute(
                "INSERT INTO events(event_key,payload) VALUES (?,?) "
                "ON CONFLICT(event_key) DO NOTHING",
                (key, raw),
            )
            stored = self.db.execute(
                "SELECT payload FROM events WHERE event_key=?", (key,)
            ).fetchone()
            previous = json.loads(stored[0])
            if any(previous[k] != value[k] for k in value if k != "observed_at"):
                raise ValueError("session_event_conflict")
        return previous

    def page(self, *, after: int = 0, limit: int = 50) -> dict:
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_session_event_page")
        rows = self.db.execute(
            "SELECT sequence,payload,recorded_at FROM events WHERE sequence>? "
            "ORDER BY sequence LIMIT ?",
            (after, limit + 1),
        ).fetchall()
        items = [
            {"sequence": seq, "recorded_at": recorded, **json.loads(raw)}
            for seq, raw, recorded in rows[:limit]
        ]
        return {
            "schema": "devgraph.session-capture-page.v1",
            "items": items,
            "next_after": items[-1]["sequence"] if len(rows) > limit else None,
            "generated_at": timestamp(datetime.now(timezone.utc)),
            "canonical_work_updated": False,
        }
