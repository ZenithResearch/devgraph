from __future__ import annotations

import importlib.util
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from devgraph.observability.session_capture import Dispatch, SessionJournal
from devgraph.observability.session_read_runtime import Handler, SessionReader
from devgraph.observability.session_sources import NativeSources


def source(tmp_path, app="hermes"):
    return {
        "id": "test-source",
        "label": "Fixture",
        "application": app,
        "root": str(tmp_path.resolve()),
    }


def hermes_db(tmp_path):
    with sqlite3.connect(tmp_path / "state.db") as db:
        db.execute(
            "CREATE TABLE sessions(id TEXT,parent_session_id TEXT,started_at REAL,"
            "ended_at REAL,last_activity_at REAL,cwd TEXT,system_prompt TEXT)"
        )
        db.execute(
            "INSERT INTO sessions VALUES ('child','parent',100,NULL,101,'/work/repo',"
            "'PRIVATE PROMPT NEVER PROJECTED')"
        )
        db.execute("INSERT INTO sessions VALUES ('parent',NULL,99,102,102,NULL,NULL)")


def plugin():
    path = Path(__file__).parents[2] / "integrations/hermes/devgraph-observer/__init__.py"
    spec = importlib.util.spec_from_file_location("observer_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_metadata_and_exact_parent_not_live_inference(tmp_path):
    hermes_db(tmp_path)
    sources = NativeSources([source(tmp_path)])
    rows, coverage = sources.scan()
    child = next(r for r in rows if r["external_session_id"] == "child")
    assert child["status"] == "unknown"
    assert child["links"][0]["basis"] == "source_parent_session_id"
    assert coverage[0]["status"] == "available"
    assert "PRIVATE" not in json.dumps(rows)
    assert sources.resolve(child) == "available"
    with sqlite3.connect(tmp_path / "state.db") as db:
        db.execute("DELETE FROM sessions WHERE id='child'")
    assert sources.resolve(child) == "missing"


def test_unavailable_store_is_not_all_clear(tmp_path):
    rows, coverage = NativeSources([source(tmp_path)]).scan()
    assert rows == []
    assert coverage[0]["status"] == "unavailable"
    assert not (tmp_path / "state.db").exists()


def test_codex_exact_locator_and_turn_status(tmp_path):
    root = tmp_path.resolve()
    path = root / "sessions" / "retained.jsonl"
    path.parent.mkdir()
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "native"}}) + "\n")
    with sqlite3.connect(root / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads(id,updated_at,created_at,cwd,source,rollout_path)")
        db.execute("INSERT INTO threads VALUES ('native',100,99,NULL,'cli',?)", (str(path),))
    with sqlite3.connect(root / "thread_history_1.sqlite") as db:
        db.execute("CREATE TABLE thread_turns(thread_id,status,started_at,completed_at)")
        db.execute("INSERT INTO thread_turns VALUES ('native','inProgress',100,NULL)")
    native = NativeSources([source(root, "codex")])
    rows, _ = native.scan()
    assert rows[0]["status"] == "running"
    assert native.resolve(rows[0]) == "available"
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "other"}}))
    assert native.resolve(rows[0]) == "mismatch"
    path.unlink()
    path.symlink_to(root / "state_5.sqlite")
    assert native.resolve(rows[0]) == "unavailable"


def test_claude_duplicate_ids_are_ambiguous(tmp_path):
    native_id = "01234567-0123-0123-0123-012345678901"
    for directory in ("a", "b"):
        path = tmp_path / "projects" / directory / (native_id + ".jsonl")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"sessionId": native_id, "message": "PRIVATE"}) + "\n")
    sources = NativeSources([source(tmp_path, "claude-code")])
    rows, _ = sources.scan()
    assert len(rows) == 1
    assert "PRIVATE" not in json.dumps(rows)
    assert sources.resolve(rows[0]) == "ambiguous"


def test_captured_session_stays_visible_when_source_record_is_missing(tmp_path):
    root = tmp_path.resolve()
    hermes_db(root)
    directory = root / "capture"
    journal = SessionJournal(directory)
    journal.dispatch(Dispatch("attempt", "test-source", "hermes", ("Task/work",)))
    identity = journal.observe("attempt", "missing-native")
    journal.close()
    reader = SessionReader(
        {"sources": [source(root)], "journals": [str(directory)]}, work_reader=lambda key: None
    )
    result = reader.detail(identity.id)
    assert result["record_available"] is False
    assert result["source_resolution"] == "missing"
    assert result["related_work"][0]["basis"] == "declared_dispatch_scope"
    assert result["related_work"][0]["resolution"] == "missing"


def test_plugin_reduces_content_and_distinguishes_work_inspection(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    observer = plugin()
    payload = {
        "session_id": "child",
        "turn_id": "t1",
        "tool_call_id": "call1",
        "tool_name": "devgraph_read",
        "args": {"kind": "Task", "id": "task-a"},
        "result": json.dumps({"ok": True, "data": {"description": "PRIVATE"}}),
        "user_message": "PRIVATE PROMPT",
    }
    observer.observe("post_tool_call", **payload)
    observer.observe("post_tool_call", **payload)
    with sqlite3.connect(tmp_path / "devgraph-observer/events.sqlite3") as db:
        rows = db.execute("SELECT payload FROM events").fetchall()
    assert len(rows) == 2  # lifecycle + binding, idempotent on replay
    assert "PRIVATE" not in str(rows)
    hermes_db(tmp_path)
    reader = SessionReader(
        {"sources": [source(tmp_path)]},
        work_reader=lambda key: {"key": key, "title": "Work", "version": 1},
    )
    child = next(r for r in reader.snapshot()["items"] if r["external_session_id"] == "child")
    detail = reader.detail(child["id"])
    assert detail["related_work"][0]["basis"] == "inspected_work"
    assert detail["related_work"][0]["resolution"] == "available"
    assert detail["canonical"] is False


@pytest.mark.parametrize("result", [{"ok": False}, {"outcome_unknown": True}, {}])
def test_failed_or_unknown_work_never_claims_binding(result):
    events = plugin().reduce_event(
        "post_tool_call",
        {
            "session_id": "s",
            "tool_name": "devgraph_work_operation",
            "result": result,
            "args": {"request": {"kind": "Task", "id": "t"}},
        },
    )
    assert all("work_ref" not in e for e in events)


def test_native_cli_requires_direct_command_and_typed_event():
    observer = plugin()
    data = {
        "session_id": "s",
        "tool_name": "terminal",
        "args": {"command": "codex exec --json 'PRIVATE'"},
        "result": {"output": '{"type":"thread.started","thread_id":"native"}\n'},
    }
    assert observer.reduce_event("post_tool_call", data)[-1]["native_id"] == "native"
    data["args"]["command"] = "echo codex exec --json"
    assert len(observer.reduce_event("post_tool_call", data)) == 1
    data["args"]["command"] = "codex exec --json x &"
    assert len(observer.reduce_event("post_tool_call", data)) == 1


def test_http_auth_rechecked_and_writes_rejected(tmp_path):
    hermes_db(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.authority = "127.0.0.1:" + str(server.server_port)
    server.audience = "fixture"
    server.reader = SessionReader({"sources": [source(tmp_path)]})
    audits = []
    server.audit = lambda *args: audits.append(args)

    class Verifier:
        revoked = False

        def verify(self, token, *, audience):
            if token != "fixture-token" or self.revoked:
                raise ValueError("denied")

    server.verifier = Verifier()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path="/session-refs/v1", *, token="fixture-token", method="GET", extra=None):
        headers = {"Authorization": "Bearer " + token, **(extra or {})}
        req = urllib.request.Request(
            "http://" + server.authority + path, headers=headers, method=method
        )
        try:
            with opener.open(req) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    try:
        assert request(token="")[0] == 401
        assert request()[0] == 200
        assert request(method="POST")[0] == 405
        assert request(extra={"Origin": "http://evil.example"})[0] == 403
        assert request("/session-refs/v1?root=/etc")[0] == 400
        assert request("/session-refs/v1?limit=101")[0] == 400
        assert request("/session-refs/v1/../../secret")[0] == 400
        page = request("/session-refs/v1?limit=1")[1]
        second = request("/session-refs/v1?after=" + page["next_after"])[1]
        assert second["items"][0]["id"] != page["items"][0]["id"]
        assert str(tmp_path) not in json.dumps(page)
        server.verifier.revoked = True
        assert request()[0] == 401
        assert ("denied", "credential_required") in audits
    finally:
        server.shutdown()
        server.server_close()
