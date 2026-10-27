from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from devgraph.auth.context import AuthorityContext
from devgraph.auth.credentials import CredentialEnvelope
from devgraph.cli import main
from devgraph.observability.attachment import HermesSessionAttachmentService
from devgraph.observability.hermes_session_ref import HermesSessionRef
from devgraph.observability.session_capture import Dispatch, SessionJournal, native_session_id
from devgraph.observability.session_cli import _arguments, capture_process
from devgraph.observability.session_import import import_attempt
from devgraph.observability.session_ref import SessionIdentity, SessionLink, SessionRef
from devgraph.observability.session_registry import SessionRegistry
from devgraph.storage.memory import MemoryGraphStorage

NOW = datetime(2026, 10, 27, 20, 0, tzinfo=timezone.utc)


def reference(app="codex", source="host-profile-a", external="native-1"):
    return SessionRef(
        SessionIdentity(app, source, external), "importer", "auth-session", "corr", NOW
    )


def authority():
    return AuthorityContext(
        CredentialEnvelope(
            actor_id="receiver",
            session_id="verified-auth-session",
            correlation_id="verified-corr",
            scopes=frozenset(),
            expires_at=NOW + timedelta(days=1),
            issuer="fixture",
            audience="fixture",
        )
    )


def journal(tmp_path):
    # macOS aliases /var and /tmp are symlinks; use the canonical temp root.
    return SessionJournal(tmp_path.resolve() / "outbox")


def test_namespace_application_and_source_all_participate_in_identity():
    a = reference()
    assert (
        len(
            {
                a.id,
                reference(source="host-profile-b").id,
                reference(app="claude-code").id,
                reference(external="native-2").id,
            }
        )
        == 4
    )
    assert replace(a, actor_id="another-importer", created_at=NOW + timedelta(days=1)).id == a.id
    assert reference(app="another-harness").identity.ref_type == "SessionRef"
    assert reference(app="grok").identity.ref_type == "GrokSessionRef"


@pytest.mark.parametrize(
    "changes",
    [
        {"application": "Open AI"},
        {"source_instance_id": "/private/profile"},
        {"external_session_id": "Bearer secret"},
        {"external_session_id": ""},
    ],
)
def test_identity_rejects_paths_free_text_and_empty_ids(changes):
    data = {"application": "codex", "source_instance_id": "profile", "external_session_id": "id"}
    with pytest.raises(ValueError):
        SessionIdentity(**(data | changes))


def test_strict_roundtrip_does_not_accept_rebound_identity_or_transcript_fields():
    ref = reference()
    props = ref.to_node_properties()
    assert SessionRef.from_node_properties(ref.id, props) == ref
    for changed in [
        props | {"external_session_id": "another"},
        props | {"transcript": "private"},
        props | {"schema_version": True},
        props | {"schema_version": 2},
        props | {"ref_type": "GrokSessionRef"},
    ]:
        with pytest.raises(ValueError):
            SessionRef.from_node_properties(ref.id, changed)
    with pytest.raises(ValueError):
        replace(ref, created_at=NOW.replace(tzinfo=None))


def test_legacy_shape_and_edges_survive_and_normalization_requires_source():
    storage = MemoryGraphStorage()
    storage.create_node("Issue", "issue-a", {"title": "fixture", "version": 1})
    legacy = HermesSessionRef("hsr-a", "native-1", "actor", "auth", "corr", created_at=NOW)
    before = legacy.to_node_properties()
    HermesSessionAttachmentService(storage).attach(
        legacy, subject_label="Issue", subject_id="issue-a"
    )
    normalized = SessionRef.from_legacy(legacy, source_instance_id="known-hermes-store")
    SessionRegistry(storage).put(normalized)
    assert storage.get_node("HermesSessionRef", legacy.id).properties == before
    assert len(storage.list_edges("ATTRIBUTED_TO_HERMES_SESSION")) == 1
    assert normalized.session_id == "auth"
    assert normalized.identity.external_session_id == "native-1"
    assert legacy.to_node_properties() == before


def test_direct_session_needs_no_parent_and_repeat_import_preserves_first_authority():
    storage = MemoryGraphStorage()
    registry = SessionRegistry(storage)
    first = registry.put(reference(app="claude-code"))
    assert (
        registry.put(replace(first, actor_id="later", created_at=NOW + timedelta(days=1))) == first
    )
    assert not storage.list_edges()


def test_repeated_delegations_and_forks_have_witnesses_and_event_conflicts_rollback():
    storage = MemoryGraphStorage()
    registry = SessionRegistry(storage)
    parent, child = reference(app="hermes"), reference()
    first = SessionLink(
        "observer", "event-1", "attempt-1", "delegated_to", parent.identity, child.identity, NOW
    )
    registry.link(parent, child, first)
    registry.link(parent, child, first)
    registry.link(parent, child, replace(first, event_id="event-2", attempt_id="attempt-2"))
    fork = reference(external="fork-1")
    registry.link(
        fork,
        child,
        replace(first, event_id="event-3", relation="forked_from", origin=fork.identity),
    )
    assert len(storage.query("SessionLink")) == 3
    stranger = reference(external="stranger")
    with pytest.raises(ValueError, match="session_event_conflict"):
        registry.link(parent, stranger, replace(first, target=stranger.identity))
    assert storage.get_node("SessionRef", stranger.id) is None
    with pytest.raises(ValueError, match="self_session_link"):
        replace(first, target=parent.identity)


@pytest.mark.parametrize(
    "app,line,expected",
    [
        ("codex", '{"type":"thread.started","thread_id":"thread-a"}', "thread-a"),
        ("claude-code", '{"type":"system","subtype":"init","session_id":"claude-a"}', "claude-a"),
        ("claude-code", '{"type":"result","session_id":"claude-a","result":"PRIVATE"}', "claude-a"),
        ("codex", '{"type":"item.completed","thread_id":"forged"}', None),
        ("codex", "Session ID: forged", None),
        ("claude-code", '{"type":"assistant","session_id":"child-text"}', None),
        ("grok", '{"type":"result","session_id":"unqualified"}', None),
        ("codex", '{"type":"thread.started","thread_id":"a","thread_id":"b"}', None),
        ("codex", '{"type":"thread.started","thread_id":23}', None),
        ("codex", '{"type":"thread.started","thread_id":"/private/path"}', None),
        ("codex", "not-json", None),
    ],
)
def test_source_reduction_only_accepts_typed_native_identity(app, line, expected):
    assert native_session_id(app, line) == expected


def test_forwarded_claude_subagent_identity_cannot_replace_root_session():
    event = {"type": "result", "session_id": "child", "parent_tool_use_id": "parent-tool"}
    assert native_session_id("claude-code", json.dumps(event)) is None


def test_journal_survives_reopen_replay_and_conflicts_without_raw_content(tmp_path):
    directory = tmp_path.resolve() / "outbox"
    dispatch = Dispatch(
        "attempt-1",
        "codex-store",
        "codex",
        ("Task/example",),
        SessionIdentity("hermes", "hermes-store", "parent-1"),
        "tool-call-1",
    )
    j = SessionJournal(directory)
    j.dispatch(dispatch)
    j.claim_launch(dispatch.attempt_id)
    j.observe(dispatch.attempt_id, "native-1")
    j.close()
    j = SessionJournal(directory)
    j.dispatch(dispatch)
    j.observe(dispatch.attempt_id, "native-1")
    with pytest.raises(ValueError, match="already_launched"):
        j.claim_launch(dispatch.attempt_id)
    with pytest.raises(ValueError, match="conflict"):
        j.observe(dispatch.attempt_id, "another-native")
    with pytest.raises(ValueError, match="conflict"):
        j.dispatch(replace(dispatch, work_refs=("Task/substitution",)))
    j.exited(dispatch.attempt_id, 1)
    page = j.page(limit=2)
    assert [r["type"] for r in page["items"]] == ["dispatch", "native_session"]
    rest = j.page(after=page["next_after"], limit=2)
    assert rest["items"][0]["returncode"] == 1
    assert rest["next_after"] is None
    assert rest["canonical_work_updated"] is False
    assert all(r["recorded_at"].endswith("Z") for r in page["items"])
    j.close()
    readonly = SessionJournal(directory, read_only=True)
    assert len(readonly.page()["items"]) == 3
    readonly.close()
    assert os.stat(directory).st_mode & 0o077 == 0
    assert os.stat(directory / "session-events.sqlite3").st_mode & 0o077 == 0


def test_unresolved_journal_does_not_invent_identity_and_rejects_unsafe_path(tmp_path):
    j = journal(tmp_path)
    j.dispatch(Dispatch("pending", "store", "codex"))
    with pytest.raises(ValueError, match="unresolved"):
        j.attempt("pending")
    with pytest.raises(ValueError):
        j.page(limit=101)
    j.close()
    unsafe = tmp_path.resolve() / "unsafe"
    unsafe.mkdir(mode=0o755)
    with pytest.raises(ValueError, match="unsafe_session_journal"):
        SessionJournal(unsafe)
    alias = tmp_path.resolve() / "alias"
    alias.symlink_to(tmp_path.resolve() / "outbox", target_is_directory=True)
    with pytest.raises(ValueError):
        SessionJournal(alias)


def test_capture_to_canonical_import_is_atomic_exact_and_does_not_complete_work(tmp_path):
    j = journal(tmp_path)
    j.dispatch(
        Dispatch(
            "run-1",
            "codex-store",
            "codex",
            ("Issue/issue-a", "Task/task-a"),
            SessionIdentity("hermes", "hermes-store", "parent"),
        )
    )
    j.observe("run-1", "native")
    storage = MemoryGraphStorage()
    storage.create_node("Issue", "issue-a", {"title": "fixture", "version": 2})
    storage.create_node("Task", "task-a", {"title": "fixture", "version": 3})
    registry = SessionRegistry(storage)
    with pytest.raises(ValueError, match="version_conflict"):
        import_attempt(
            registry,
            j,
            "run-1",
            authority=authority(),
            expected_work_versions={"Issue/issue-a": 2, "Task/task-a": 4},
        )
    assert storage.query("SessionRef") == []
    assert storage.query("SessionLink") == []
    with pytest.raises(ValueError, match="scope_conflict"):
        import_attempt(
            registry, j, "run-1", authority=authority(), expected_work_versions={"Issue/issue-a": 2}
        )
    result = import_attempt(
        registry,
        j,
        "run-1",
        authority=authority(),
        expected_work_versions={"Issue/issue-a": 2, "Task/task-a": 3},
    )
    assert result.actor_id == "receiver"
    assert result.session_id == "verified-auth-session"
    assert result.identity.external_session_id == "native"
    assert len(storage.query("SessionRef")) == 2
    assert len(storage.query("SessionLink")) == 1
    assert len(storage.list_edges("ATTRIBUTED_TO_SESSION")) == 2
    assert storage.get_node("Task", "task-a").properties == {"title": "fixture", "version": 3}
    import_attempt(
        registry,
        j,
        "run-1",
        authority=authority(),
        expected_work_versions={"Issue/issue-a": 2, "Task/task-a": 3},
    )
    assert len(storage.list_edges("ATTRIBUTED_TO_SESSION")) == 2
    j.close()


@pytest.mark.parametrize(
    ("application", "event", "arguments"),
    [
        (
            "codex",
            {"type": "thread.started", "thread_id": "real-native"},
            ["exec", "--sandbox", "read-only", "PRIVATE-prompt"],
        ),
        (
            "claude-code",
            {"type": "system", "subtype": "init", "session_id": "real-native"},
            ["-p", "PRIVATE-prompt", "--permission-mode", "plan"],
        ),
    ],
)
def test_capture_preserves_caller_permissions_and_never_relaunches_attempt(
    tmp_path, monkeypatch, capfd, application, event, arguments
):
    binary = tmp_path.resolve() / "fake-client"
    binary.write_text(
        f"#!{sys.executable}\nimport json,sys\n"
        f"print(json.dumps({event!r}))\n"
        "print(json.dumps({'type':'item.completed','text':'PRIVATE-prompt'}))\n"
        "sys.exit(3)\n"
    )
    binary.chmod(0o700)
    monkeypatch.setattr("devgraph.observability.session_cli.shutil.which", lambda name: str(binary))
    dispatch = Dispatch("attempt-cli", "store", application)
    j = journal(tmp_path)
    assert capture_process(dispatch, arguments, j) == 3
    output = capfd.readouterr().out
    assert "PRIVATE-prompt" in output  # Forwarded, not persisted.
    assert "PRIVATE-prompt" not in json.dumps(j.page())
    assert j.attempt("attempt-cli")["native"]["identity"]["external_session_id"] == "real-native"
    with pytest.raises(ValueError, match="already_launched"):
        capture_process(dispatch, arguments, j)
    assert _arguments("codex", ["exec", "--json", "prompt"]).count("--json") == 1
    assert "--dangerously-skip-permissions" not in _arguments("claude-code", ["-p", "prompt"])
    j.close()


def test_cli_invalid_capture_cannot_spawn_or_create_journal(tmp_path, monkeypatch, capsys):
    path = tmp_path.resolve() / "never-created"
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: pytest.fail("unexpected child"))
    assert (
        main(
            [
                "sessions",
                "run",
                "--client",
                "codex",
                "--source-instance",
                "source",
                "--journal-dir",
                str(path),
                "--parent-hermes-id",
                "parent",
                "--",
                "exec",
                "p",
            ]
        )
        == 2
    )
    assert not path.exists()
    assert "session_capture_failed" in capsys.readouterr().err
