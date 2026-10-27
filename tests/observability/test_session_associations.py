import json
import sqlite3

from devgraph.observability.session_associations import read_inspections, record_work_inspection
from devgraph.observability.session_read_runtime import SessionReader


def config(tmp_path):
    root = tmp_path.resolve()
    native = root / "native"
    path = native / "sessions/retained.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "native-id"}}))
    with sqlite3.connect(native / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads(id,rollout_path,created_at,updated_at,cwd,source)")
        db.execute("INSERT INTO threads VALUES ('native-id',?,1,2,NULL,'cli')", (str(path),))
    source = {"id": "native-store", "application": "codex", "label": "Fixture", "root": str(native)}
    cfg = root / "config.json"
    cfg.write_text(json.dumps({"sources": [source]}))
    cfg.chmod(0o600)
    return cfg


def environment(**extra):
    return {"DEVGRAPH_SESSION_OBSERVATION": "1", "CODEX_THREAD_ID": "native-id", **extra}


def test_successful_inspection_resolves_native_identity_and_deduplicates(tmp_path):
    cfg = config(tmp_path)
    for _ in range(2):
        assert record_work_inspection("Task", "work", config_path=cfg, environ=environment())
    rows = read_inspections(cfg.parent / "work-inspections.sqlite3")
    assert len(rows) == 1
    assert rows[0]["key"] == "Task/work"
    assert rows[0]["basis"] == "successful_cli_work_read"
    value = json.loads(cfg.read_text())
    value["inspection_file"] = str(cfg.parent / "work-inspections.sqlite3")
    reader = SessionReader(
        value, work_reader=lambda key: {"key": key, "title": "Work", "version": 2}
    )
    detail = reader.detail(rows[0]["ref_id"])
    assert detail["related_work"][0]["resolution"] == "available"
    assert detail["related_work"][0]["basis"] == "successful_cli_work_read"
    assert "native/" not in json.dumps(detail)


def test_unknown_source_id_cannot_create_work_binding(tmp_path):
    cfg = config(tmp_path)
    assert not record_work_inspection(
        "Task", "work", config_path=cfg, environ=environment(CODEX_THREAD_ID="missing")
    )
    assert not (cfg.parent / "work-inspections.sqlite3").exists()


def test_mixed_caller_environments_do_not_guess_a_harness(tmp_path):
    cfg = config(tmp_path)
    assert not record_work_inspection(
        "Task", "work", config_path=cfg, environ=environment(CLAUDE_SESSION_ID="other")
    )


def test_observation_requires_explicit_launcher_opt_in(tmp_path):
    cfg = config(tmp_path)
    assert not record_work_inspection(
        "Task", "work", config_path=cfg, environ={"CODEX_THREAD_ID": "native-id"}
    )
    assert not (cfg.parent / "work-inspections.sqlite3").exists()


def test_invalid_work_never_writes_or_raises_into_successful_cli_result(tmp_path):
    cfg = config(tmp_path)
    assert not record_work_inspection("Secret", "../x", config_path=cfg, environ=environment())
    assert not (cfg.parent / "work-inspections.sqlite3").exists()
