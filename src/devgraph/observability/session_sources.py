"""Bounded, read-only native session metadata. Never ingests a transcript.

Source namespaces are operator configuration, not inferred from cwd or model.
This is a disposable source projection, not canonical SessionRef persistence.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import time
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

from devgraph.observability.session_ref import SessionIdentity, work_ref

UUID = re.compile(r"[0-9a-fA-F-]{36}")
LIMIT = 200
MAX_LINE = 65_536


def utc(value=None):
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat() if value else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def owned(path: Path, *, private=False):
    """Configured roots may resolve macOS home symlinks; files may not redirect."""
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or (private and stat.S_IMODE(info.st_mode) & 0o077)
    ):
        raise ValueError("unsafe_source_file")
    return info


def readonly(path: Path):
    owned(path)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    deadline = time.monotonic() + 2
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    db.execute("PRAGMA query_only=ON")
    return db


def row(source, native_id, *, updated=None, started=None, status="unknown", cwd=None):
    identity = SessionIdentity(source["application"], source["id"], native_id)
    return {
        "id": identity.id,
        "application": identity.application,
        "source_instance_id": identity.source_instance_id,
        "external_session_id": native_id,
        "profile": source["label"],
        "context": Path(cwd).name[:80] if cwd else None,
        "started_at": utc(started),
        "last_activity_at": utc(updated),
        "activity_basis": "source_metadata",
        "status": status,
        "work_refs": [],
        "links": [],
        "record_available": True,
        "persistence": "source_observed",
        "canonical": False,
    }


class NativeSources:
    def __init__(self, sources):
        self.sources = sources
        for source in sources:
            SessionIdentity(source["application"], source["id"], "pending")
            if source["application"] not in {"hermes", "codex", "claude-code"}:
                raise ValueError("unsupported_source_application")
            if not Path(source["root"]).is_absolute():
                raise ValueError("source_root_must_be_absolute")

    def scan(self):
        items, coverage = [], []
        for source in self.sources:
            try:
                rows, partial = getattr(self, source["application"].replace("-", "_"))(source)
                items.extend(rows)
                coverage.append(
                    {
                        "source_instance_id": source["id"],
                        "label": source["label"],
                        "application": source["application"],
                        "status": "available",
                        "loaded": len(rows),
                        "bounded": partial,
                        "window_basis": {
                            "hermes": "recently_started",
                            "codex": "recently_updated",
                            "claude-code": "file_modified",
                        }[source["application"]],
                    }
                )
            except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
                coverage.append(
                    {
                        "source_instance_id": source["id"],
                        "label": source["label"],
                        "application": source["application"],
                        "status": "unavailable",
                        "loaded": 0,
                        "bounded": True,
                    }
                )
        # A native backend is a runtime relationship, not an extra delegated agent.
        # Resolve the persisted ID in admitted stores; a model/provider name proves nothing.
        index = {r["id"]: r for r in items}
        for parent in list(items):
            native_id = parent.pop("_codex_thread_id", None)
            if not native_id:
                continue
            candidates = []
            for source in self.sources:
                if source["application"] != "codex":
                    continue
                candidate = row(source, native_id)
                if self.resolve(candidate) == "available":
                    candidates.append(candidate)
            if len(candidates) != 1:
                parent["unresolved_native_binding"] = {
                    "application": "codex",
                    "external_session_id": native_id,
                    "reason": "ambiguous_store" if candidates else "source_unavailable",
                }
                continue
            target = index.get(candidates[0]["id"])
            if target is None:
                target = candidates[0]
                items.append(target)
                index[target["id"]] = target
            origin_identity = {
                k: parent[k] for k in ("application", "source_instance_id", "external_session_id")
            }
            target_identity = {k: target[k] for k in origin_identity}
            evidence = {
                "basis": "hermes_model_config.codex_thread_id",
                "origin": origin_identity,
                "target": target_identity,
            }
            parent["links"].append({"id": target["id"], "relation": "uses_runtime", **evidence})
            target["links"].append({"id": parent["id"], "relation": "runtime_for", **evidence})
        return items, coverage

    def hermes(self, source):
        with readonly(Path(source["root"]) / "state.db") as db:
            columns = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
            binding = (
                "CASE WHEN json_valid(model_config) AND length(model_config)<=65536 "
                "THEN json_extract(model_config,'$.codex_thread_id') END"
                if "model_config" in columns
                else "NULL"
            )
            rows = db.execute(
                "SELECT id,parent_session_id,started_at,ended_at,last_activity_at,cwd "
                f", {binding} AS codex_thread_id "
                # Hermes indexes started_at, not COALESCE(last_activity_at,...).
                # Bound the source window before touching wide historical rows.
                "FROM sessions ORDER BY started_at DESC LIMIT ?",
                (LIMIT + 1,),
            ).fetchall()
        result = []
        for r in rows[:LIMIT]:
            item = row(
                source,
                r["id"],
                started=r["started_at"],
                updated=r["last_activity_at"] or r["started_at"],
                cwd=r["cwd"],
                status="ended" if r["ended_at"] else "unknown",
            )
            if isinstance(r["codex_thread_id"], str):
                try:
                    SessionIdentity("codex", "pending", r["codex_thread_id"])
                    item["_codex_thread_id"] = r["codex_thread_id"]
                except ValueError:
                    item["unresolved_native_binding"] = {"reason": "invalid_stored_identity"}
            if r["parent_session_id"]:
                parent = SessionIdentity("hermes", source["id"], r["parent_session_id"])
                item["links"].append(
                    {
                        "relation": "delegated_from",
                        "id": parent.id,
                        "basis": "source_parent_session_id",
                    }
                )
            result.append(item)
        return result, len(rows) > LIMIT

    def codex(self, source):
        root = Path(source["root"])
        with readonly(root / "state_5.sqlite") as db:
            rows = db.execute(
                "SELECT id,updated_at,created_at,cwd,source FROM threads "
                "ORDER BY updated_at DESC LIMIT ?",
                (LIMIT + 1,),
            ).fetchall()
        result = []
        for r in rows[:LIMIT]:
            try:
                origin = json.loads(r["source"])
            except (ValueError, TypeError):
                origin = {}
            sub = origin.get("subagent", {}) if isinstance(origin, dict) else {}
            if isinstance(sub, dict) and sub.get("other") == "guardian":
                continue
            item = row(
                source, r["id"], updated=r["updated_at"], started=r["created_at"], cwd=r["cwd"]
            )
            spawn = sub.get("thread_spawn", {}) if isinstance(sub, dict) else {}
            if spawn.get("parent_thread_id"):
                parent = SessionIdentity("codex", source["id"], spawn["parent_thread_id"])
                item["links"].append(
                    {"relation": "spawned_from", "id": parent.id, "basis": "source_thread_spawn"}
                )
            result.append(item)
        history = root / "thread_history_1.sqlite"
        if history.exists():
            with readonly(history) as db:
                for item in result:
                    turn = db.execute(
                        "SELECT status,started_at,completed_at FROM thread_turns "
                        "WHERE thread_id=? ORDER BY started_at DESC LIMIT 1",
                        (item["external_session_id"],),
                    ).fetchone()
                    if turn:
                        item["status"] = (
                            "running"
                            if turn["status"] == "inProgress"
                            else (
                                turn["status"]
                                if turn["status"] in {"completed", "failed", "interrupted"}
                                else "unknown"
                            )
                        )
                        item["activity_basis"] = "native_turn_status"
                        item["last_activity_at"] = utc(turn["completed_at"] or turn["started_at"])
        return result, len(rows) > LIMIT

    def claude_code(self, source):
        root = Path(source["root"]) / "projects"
        # Bound discovery independently of transcript size. No message bodies retained.
        files, partial = [], False
        for directory in islice(root.iterdir(), 100):
            if directory.is_symlink() or not directory.is_dir():
                continue
            for path in islice(directory.iterdir(), 1000):
                if path.suffix == ".jsonl" and UUID.fullmatch(path.stem):
                    info = owned(path)
                    files.append((info.st_mtime, path))
                if len(files) >= 1000:
                    partial = True
                    break
            if partial:
                break
        result, seen = [], set()
        for modified, path in sorted(files, reverse=True)[:LIMIT]:
            if path.stem in seen:
                continue
            seen.add(path.stem)
            if self.claude_identity(path) != path.stem:
                continue
            item = row(source, path.stem, updated=modified)
            item["activity_basis"] = "file_modified"
            result.append(item)
        return result, partial or len(files) > LIMIT

    @staticmethod
    def claude_identity(path):
        owned(path)
        with path.open("rb") as stream:
            for _ in range(16):
                line = stream.readline(MAX_LINE + 1)
                if not line or len(line) > MAX_LINE:
                    return None
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict) and value.get("sessionId"):
                    return value["sessionId"]
        return None

    def resolve(self, item):
        """Recheck exact source ID at open time; no request-supplied path."""
        source = next(s for s in self.sources if s["id"] == item["source_instance_id"])
        root, native = Path(source["root"]), item["external_session_id"]
        try:
            if source["application"] == "hermes":
                with readonly(root / "state.db") as db:
                    found = db.execute("SELECT id FROM sessions WHERE id=?", (native,)).fetchone()
                return "available" if found else "missing"
            if source["application"] == "codex":
                with readonly(root / "state_5.sqlite") as db:
                    found = db.execute(
                        "SELECT rollout_path FROM threads WHERE id=?", (native,)
                    ).fetchone()
                if not found:
                    return "missing"
                path = Path(found[0])
                # Native stored locator must remain inside an admitted source tree.
                if not any(
                    path.resolve().is_relative_to((root / part).resolve())
                    for part in ("sessions", "archived_sessions")
                ):
                    return "unavailable"
                owned(path)
                with path.open("rb") as stream:
                    value = json.loads(stream.readline(MAX_LINE + 1))
                return (
                    "available"
                    if (
                        value.get("type") == "session_meta"
                        and value.get("payload", {}).get("id") == native
                    )
                    else "mismatch"
                )
            matches = []
            for directory in islice((root / "projects").iterdir(), 100):
                if not directory.is_symlink() and directory.is_dir():
                    path = directory / (native + ".jsonl")
                    if path.exists():
                        matches.append(path)
            if len(matches) != 1:
                return "ambiguous" if matches else "missing"
            return "available" if self.claude_identity(matches[0]) == native else "mismatch"
        except (OSError, ValueError, sqlite3.Error, TypeError):
            return "unavailable"


def hook_evidence(source):
    """Only this plugin's reduced metadata, not source tool messages."""
    path = Path(source["root"]) / "devgraph-observer" / "events.sqlite3"
    if not path.exists():
        return []
    owned(path, private=True)
    with readonly(path) as db:
        rows = db.execute(
            "SELECT payload,observed_at FROM events ORDER BY sequence DESC LIMIT 1000"
        ).fetchall()
    result = []
    for record in rows:
        value = json.loads(record[0])
        value["observed_at"] = record[1]
        SessionIdentity("hermes", source["id"], value["session_id"])
        if value.get("work_ref"):
            work_ref(value["work_ref"])
        result.append(value)
    return result
