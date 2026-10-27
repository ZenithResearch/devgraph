"""Opt-in metadata witness for successful canonical CLI Work reads.

The environment supplies a claimed caller ID, then the configured source resolves
it exactly. This witnesses inspection, not signed graph attribution or mutation.
Only the session-aware launcher enables this; ordinary CLI/test behavior is intact.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from devgraph.observability.session_ref import SessionIdentity, work_ref
from devgraph.observability.session_sources import NativeSources, owned, row

DEFAULT_CONFIG = (
    Path.home() / "Library/Application Support/Zenith/Devgraph/session-reader/config.json"
)


def record_work_inspection(kind, identifier, *, config_path=DEFAULT_CONFIG, environ=None):
    """Best-effort after an authorized successful get; never changes its result."""
    env = os.environ if environ is None else environ
    if env.get("DEVGRAPH_SESSION_OBSERVATION") != "1":
        return False
    try:
        owned(config_path, private=True)
        config = json.loads(config_path.read_text())
        identities = [
            (app, env[key])
            for app, key in (
                ("codex", "CODEX_THREAD_ID"),
                ("claude-code", "CLAUDE_SESSION_ID"),
            )
            if env.get(key)
        ]
        if len(identities) != 1:
            return False
        app, native_id = identities[0]
        candidates = [s for s in config["sources"] if s["application"] == app]
        if len(candidates) != 1:
            return False
        source = candidates[0]
        identity = SessionIdentity(app, source["id"], native_id)
        if NativeSources([source]).resolve(row(source, native_id)) != "available":
            return False
        key = kind + "/" + identifier
        work_ref(key)
        path = config_path.parent / "work-inspections.sqlite3"
        if path.exists():
            owned(path, private=True)
        else:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        with sqlite3.connect(path, timeout=1) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS inspections ("
                "ref_id TEXT, work_ref TEXT, application TEXT, source_instance_id TEXT, "
                "external_session_id TEXT, observed_at TEXT, "
                "PRIMARY KEY(ref_id,work_ref))"
            )
            db.execute(
                "INSERT INTO inspections VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(ref_id,work_ref) DO UPDATE SET observed_at=excluded.observed_at",
                (
                    identity.id,
                    key,
                    app,
                    source["id"],
                    native_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return True
    except Exception:
        return False


def read_inspections(path):
    if not path.exists():
        return []
    owned(path, private=True)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
        rows = db.execute(
            "SELECT ref_id,work_ref,application,source_instance_id,"
            "external_session_id,observed_at FROM inspections "
            "ORDER BY observed_at DESC LIMIT 1000"
        ).fetchall()
    result = []
    for ref, work, app, source, native, at in rows:
        if SessionIdentity(app, source, native).id != ref:
            raise ValueError("invalid_inspection_identity")
        work_ref(work)
        result.append(
            {
                "ref_id": ref,
                "key": work,
                "observed_at": at,
                "basis": "successful_cli_work_read",
                "caller_basis": "resolved_environment",
            }
        )
    return result
