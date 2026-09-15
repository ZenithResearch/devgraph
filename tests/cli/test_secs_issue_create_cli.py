from __future__ import annotations

import inspect
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import devgraph.cli as cli
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.secs_issue_create import SecSIssueCreateDenied
from devgraph.cli import _parser, main
from devgraph.events.outbox import EVENT_RECEIPT_LABEL
from devgraph.local_host import LocalHostConfig, write_local_config
from devgraph.model.repository import WorkObjectRepository, WorkObjectRepositoryError
from devgraph.model.work import Issue
from devgraph.ops import secs_issue_create_receiver as receiver
from devgraph.ops.local_path_integrity import LocalPathIntegrityError
from devgraph.ops.migrate import ManifestError
from devgraph.storage.base import StorageUnavailable
from devgraph.storage.memory import MemoryGraphStorage

FIXTURES = Path(__file__).parents[1] / "fixtures" / "secs_devgraph_issue_create_v1"
EXPECTED_NOW = 1_800_000_000


def _write_private(path: Path, content: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o600)
    return path


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _receiver_manifest() -> bytes:
    binding = json.loads(_fixture("receiver-policy-binding.json"))
    return json.dumps(
        {
            "audience": "devgraph://receiver-local",
            "operation": "devgraph.issue.create.v1",
            "policy_binding": binding,
            "schema": "devgraph-secs-issue-create-receiver.v1",
            "schema_version": 1,
            "stable_issuer": "secs:devgraph-receiver-local",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _local_files(tmp_path: Path) -> tuple[Path, Path, Path, Path, MemoryGraphStorage]:
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o700)
    config_path = tmp_path / "local.json"
    write_local_config(LocalHostConfig.build(data_root=data_root), config_path)
    _write_private(data_root / "secrets" / "neo4j_password", b"not-returned\n")
    bundle = data_root / receiver.RECEIVER_BUNDLE_RELATIVE_PATH
    _write_private(bundle / receiver.RECEIVER_MANIFEST_NAME, _receiver_manifest())
    _write_private(
        bundle / receiver.SECS_PUBLIC_KEY_REGISTRY_NAME,
        _fixture("secs-public-key-registry.json"),
    )
    request = _write_private(tmp_path / "request.json", _fixture("request.json"))
    projection = _write_private(
        tmp_path / "signed-projection.json",
        _fixture("signed-projection.json"),
    )
    idempotency = _write_private(
        tmp_path / "idempotency-key.txt",
        _fixture("idempotency-key.txt"),
    )
    return config_path, request, projection, idempotency, MemoryGraphStorage()


def _patch_storage(
    monkeypatch: pytest.MonkeyPatch,
    storage: MemoryGraphStorage,
) -> list[object]:
    captured: list[object] = []

    def build(config):
        captured.append(config)
        return storage

    monkeypatch.setattr(receiver, "Neo4jGraphStorage", build)
    monkeypatch.setattr(receiver, "_require_canonical_readiness", lambda _storage: None)
    monkeypatch.setattr(receiver.time, "time", lambda: EXPECTED_NOW)
    return captured


def _execute(
    config_path: Path,
    request: Path,
    projection: Path,
    idempotency: Path,
) -> dict[str, object]:
    with patch.object(receiver, "DEFAULT_CONFIG_PATH", config_path):
        return receiver.execute_local_secs_issue_create_v1(
            request_file=request,
            signed_projection_file=projection,
            idempotency_key_file=idempotency,
        )


def _graph_counts(storage: MemoryGraphStorage) -> tuple[int, int, int]:
    return (
        len(storage.query("Issue")),
        len(storage.query(EVENT_RECEIPT_LABEL)),
        len(storage.list_edges("EMITTED_EVENT")),
    )


def test_exact_cli_executes_one_verified_issue_receipt_audit_and_duplicate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, request, projection, idempotency, storage = _local_files(tmp_path)
    captured = _patch_storage(monkeypatch, storage)

    first = _execute(config, request, projection, idempotency)
    replay = _execute(config, request, projection, idempotency)

    assert first == {
        "audit": {"published_records": 1},
        "duplicate": False,
        "issue": {
            "id": "issue-golden",
            "kind": "Issue",
            "status": "draft",
            "version": 1,
        },
        "operation": "devgraph.issue.create.v1",
        "receipt": {
            "correlation_id": (
                "dg:sha256:"
                "d3bfcfc7829aece79f74d643525406e6565a49a3f862e71739aea4fc87d5e337"
            ),
            "id": first["receipt"]["id"],  # type: ignore[index]
            "status": "pending",
            "subject_id": "issue-golden",
            "subject_label": "Issue",
        },
    }
    assert replay["duplicate"] is True
    assert replay["issue"] is None
    assert replay["receipt"] == first["receipt"]
    assert _graph_counts(storage) == (1, 1, 1)
    assert len(captured) == 2
    for storage_config in captured:
        assert storage_config.uri == "bolt://127.0.0.1:7687"
        assert storage_config.user == "neo4j"
        assert storage_config.password == "not-returned"
        assert storage_config.database == "neo4j"
    serialized = json.dumps([first, replay])
    assert _fixture("idempotency-key.txt").decode().strip() not in serialized
    assert "Golden issue" not in serialized
    assert "secs_verifier_signature" not in serialized
    assert "not-returned" not in serialized


def test_cli_rejects_unbounded_persisted_correlation_without_copying_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, request, projection, idempotency, storage = _local_files(tmp_path)
    _patch_storage(monkeypatch, storage)
    monkeypatch.setattr(receiver, "DEFAULT_CONFIG_PATH", config)
    first = _execute(config, request, projection, idempotency)
    receipt_id = first["receipt"]["id"]  # type: ignore[index]
    receipt_node = storage.get_node(EVENT_RECEIPT_LABEL, receipt_id)
    assert receipt_node is not None
    private_correlation = "private-correlation-value"
    storage.update_node(
        EVENT_RECEIPT_LABEL,
        receipt_id,
        {**receipt_node.properties, "correlation_id": private_correlation},
    )

    code = main(
        [
            "secs-issue-create-v1",
            "--request-file",
            str(request),
            "--signed-projection-file",
            str(projection),
            "--idempotency-key-file",
            str(idempotency),
        ]
    )
    output = capsys.readouterr()

    assert code == 2
    assert output.out == ""
    assert json.loads(output.err) == {
        "error": "exact Issue-create result is malformed"
    }
    assert private_correlation not in output.err
    assert "Traceback" not in output.err
    assert _graph_counts(storage) == (1, 1, 1)


@pytest.mark.parametrize(
    "selected",
    ("request", "projection", "idempotency", "receiver", "registry", "password"),
)
def test_every_operation_and_receiver_file_must_be_private_owner_only(
    selected: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, request, projection, idempotency, storage = _local_files(tmp_path)
    local = LocalHostConfig.build(data_root=tmp_path / "data")
    paths = {
        "request": request,
        "projection": projection,
        "idempotency": idempotency,
        "receiver": (
            local.data_root
            / receiver.RECEIVER_BUNDLE_RELATIVE_PATH
            / receiver.RECEIVER_MANIFEST_NAME
        ),
        "registry": (
            local.data_root
            / receiver.RECEIVER_BUNDLE_RELATIVE_PATH
            / receiver.SECS_PUBLIC_KEY_REGISTRY_NAME
        ),
        "password": local.data_root / "secrets" / "neo4j_password",
    }
    paths[selected].chmod(0o640)
    _patch_storage(monkeypatch, storage)

    with pytest.raises(receiver.LocalSecSIssueCreateError, match="permissions"):
        _execute(config, request, projection, idempotency)

    assert _graph_counts(storage) == (0, 0, 0)


def test_issue_receiver_fails_closed_when_mount_ownership_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, request, projection, idempotency, storage = _local_files(tmp_path)
    captured = _patch_storage(monkeypatch, storage)

    def ownership_disabled(
        _data_root: Path,
        _relative_path: Path,
        *,
        missing_ok: bool,
    ) -> None:
        assert missing_ok is False
        raise LocalPathIntegrityError(
            "configured data-root mount has ownership disabled"
        )

    monkeypatch.setattr(
        receiver,
        "require_receiver_directory_path",
        ownership_disabled,
    )
    with pytest.raises(
        receiver.LocalSecSIssueCreateError,
        match="mount has ownership disabled",
    ):
        _execute(config, request, projection, idempotency)

    assert captured == []
    assert _graph_counts(storage) == (0, 0, 0)


def test_symlinked_input_and_oversized_files_fail_before_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, request, projection, idempotency, storage = _local_files(tmp_path)
    _patch_storage(monkeypatch, storage)
    link = tmp_path / "request-link.json"
    link.symlink_to(request)

    with pytest.raises(receiver.LocalSecSIssueCreateError, match="regular file"):
        _execute(config, link, projection, idempotency)

    _write_private(request, b"{" + b" " * receiver.REQUEST_FILE_MAX_BYTES)
    with pytest.raises(receiver.LocalSecSIssueCreateError, match="size limit"):
        _execute(config, request, projection, idempotency)
    assert _graph_counts(storage) == (0, 0, 0)


def test_owner_fifo_is_rejected_without_blocking_on_open(tmp_path: Path) -> None:
    fifo = tmp_path / "owner-input.fifo"
    os.mkfifo(fifo, mode=0o600)
    proof = (
        "import sys\n"
        "from pathlib import Path\n"
        "from devgraph.ops.secs_issue_create_receiver import "
        "LocalSecSIssueCreateError, _read_private_bounded_file\n"
        "try:\n"
        "    _read_private_bounded_file(Path(sys.argv[1]), "
        "label='FIFO input', maximum_bytes=4)\n"
        "except LocalSecSIssueCreateError:\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(1)\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", proof, str(fifo)],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )

    assert completed.returncode == 0
    assert completed.stdout == completed.stderr == ""


@pytest.mark.parametrize(
    ("mutate", "reason"),
    (
        ("request", "devgraph_request_digest_mismatch"),
        ("expired", "secs_authority_not_current"),
        ("idempotency", "devgraph_idempotency_digest_mismatch"),
    ),
)
def test_malformed_expired_and_mismatched_inputs_have_zero_graph_side_effects(
    mutate: str,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, request, projection, idempotency, storage = _local_files(tmp_path)
    _patch_storage(monkeypatch, storage)
    if mutate == "request":
        _write_private(
            request,
            b'{"id":"issue-golden","kind":"Issue","title":"Changed"}',
        )
    elif mutate == "expired":
        monkeypatch.setattr(receiver.time, "time", lambda: EXPECTED_NOW + 60)
    else:
        _write_private(idempotency, b"different-idempotency-key-0001\n")

    with pytest.raises(SecSIssueCreateDenied, match=reason):
        _execute(config, request, projection, idempotency)

    assert _graph_counts(storage) == (0, 0, 0)


def test_receiver_manifest_and_key_registry_are_closed_and_fail_before_graph_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, request, projection, idempotency, storage = _local_files(tmp_path)
    local = LocalHostConfig.build(data_root=tmp_path / "data")
    bundle = local.data_root / receiver.RECEIVER_BUNDLE_RELATIVE_PATH
    _patch_storage(monkeypatch, storage)

    manifest = json.loads(_receiver_manifest())
    manifest["operation"] = "devgraph.other.v1"
    _write_private(
        bundle / receiver.RECEIVER_MANIFEST_NAME,
        json.dumps(manifest).encode(),
    )
    with pytest.raises(receiver.LocalSecSIssueCreateError, match="manifest"):
        _execute(config, request, projection, idempotency)
    assert _graph_counts(storage) == (0, 0, 0)

    _write_private(bundle / receiver.RECEIVER_MANIFEST_NAME, _receiver_manifest())
    registry = json.loads(_fixture("secs-public-key-registry.json"))
    registry["unknown"] = True
    _write_private(
        bundle / receiver.SECS_PUBLIC_KEY_REGISTRY_NAME,
        json.dumps(registry).encode(),
    )
    with pytest.raises(receiver.LocalSecSIssueCreateError, match="trust configuration"):
        _execute(config, request, projection, idempotency)
    assert _graph_counts(storage) == (0, 0, 0)


def test_unready_canonical_store_fails_before_adapter_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, request, projection, idempotency, storage = _local_files(tmp_path)
    _patch_storage(monkeypatch, storage)

    def unready(_storage) -> None:
        raise receiver.LocalSecSIssueCreateError(
            "canonical local Devgraph is not ready: unapplied_migrations"
        )

    monkeypatch.setattr(receiver, "_require_canonical_readiness", unready)
    with pytest.raises(receiver.LocalSecSIssueCreateError, match="not ready"):
        _execute(config, request, projection, idempotency)
    assert _graph_counts(storage) == (0, 0, 0)


def test_invalid_packaged_migration_manifest_is_fixed_safe_cli_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, request, projection, idempotency, storage = _local_files(tmp_path)
    monkeypatch.setattr(receiver, "DEFAULT_CONFIG_PATH", config)
    monkeypatch.setattr(receiver, "Neo4jGraphStorage", lambda _config: storage)
    monkeypatch.setattr(receiver.time, "time", lambda: EXPECTED_NOW)
    monkeypatch.setattr(
        receiver,
        "load_manifest",
        lambda _path: (_ for _ in ()).throw(ManifestError("private package path")),
    )

    code = main(
        [
            "secs-issue-create-v1",
            "--request-file",
            str(request),
            "--signed-projection-file",
            str(projection),
            "--idempotency-key-file",
            str(idempotency),
        ]
    )
    output = capsys.readouterr()

    assert code == 2
    assert output.out == ""
    assert json.loads(output.err) == {
        "error": "canonical migration manifest is invalid"
    }
    assert "private package path" not in output.err
    assert "Traceback" not in output.err
    assert _graph_counts(storage) == (0, 0, 0)


def test_idempotency_file_is_one_exact_ascii_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, request, projection, idempotency, storage = _local_files(tmp_path)
    _patch_storage(monkeypatch, storage)
    for raw in (b"a" * 129, b"a" * 16 + b"\n\n", b"a" * 16 + b"\r\n", b"\xff"):
        _write_private(idempotency, raw)
        with pytest.raises(receiver.LocalSecSIssueCreateError, match="idempotency key file"):
            _execute(config, request, projection, idempotency)
        assert _graph_counts(storage) == (0, 0, 0)


def test_cli_surface_has_only_the_three_operation_file_flags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, request, projection, idempotency, storage = _local_files(tmp_path)
    _patch_storage(monkeypatch, storage)
    monkeypatch.setattr(receiver, "DEFAULT_CONFIG_PATH", config)

    code = main(
        [
            "secs-issue-create-v1",
            "--request-file",
            str(request),
            "--signed-projection-file",
            str(projection),
            "--idempotency-key-file",
            str(idempotency),
        ]
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out)["operation"] == "devgraph.issue.create.v1"
    parser = _parser()
    command_parser = next(
        action.choices["secs-issue-create-v1"]
        for action in parser._actions
        if isinstance(getattr(action, "choices", None), dict)
    )
    options = {
        option
        for action in command_parser._actions
        for option in action.option_strings
        if option not in {"--help", "-h"}
    }
    assert options == {
        "--idempotency-key-file",
        "--request-file",
        "--signed-projection-file",
    }
    for forbidden in (
        "--audience",
        "--database",
        "--operation",
        "--password",
        "--uri",
        "--user",
    ):
        assert forbidden not in options


def test_cli_existing_issue_conflict_is_bounded_json_with_zero_new_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, request, projection, idempotency, storage = _local_files(tmp_path)
    existing = WorkObjectRepository(storage).create(
        Issue(id="issue-golden", title="Existing private title")
    )
    audit = AuditLog()
    _patch_storage(monkeypatch, storage)
    monkeypatch.setattr(receiver, "DEFAULT_CONFIG_PATH", config)
    monkeypatch.setattr(receiver, "AuditLog", lambda: audit)

    code = main(
        [
            "secs-issue-create-v1",
            "--request-file",
            str(request),
            "--signed-projection-file",
            str(projection),
            "--idempotency-key-file",
            str(idempotency),
        ]
    )
    output = capsys.readouterr()

    assert code == 2
    assert output.out == ""
    assert json.loads(output.err) == {"error": "work_object_already_exists"}
    assert "Traceback" not in output.err
    for private_value in (
        "Existing private title",
        "Golden issue",
        _fixture("idempotency-key.txt").decode().strip(),
    ):
        assert private_value not in output.err
    assert storage.get_node("Issue", "issue-golden") is not None
    assert WorkObjectRepository(storage).get_by_id("Issue", "issue-golden") == existing
    assert _graph_counts(storage) == (1, 0, 0)
    assert audit.records == []


def test_cli_create_race_is_same_bounded_conflict_and_rolls_back_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class CreateRaceStorage(MemoryGraphStorage):
        def create_node(self, label, node_id, properties=None):
            if label == "Issue":
                raise KeyError("private Neo4j uniqueness detail")
            return super().create_node(label, node_id, properties)

    config, request, projection, idempotency, _storage = _local_files(tmp_path)
    storage = CreateRaceStorage()
    audit = AuditLog()
    _patch_storage(monkeypatch, storage)
    monkeypatch.setattr(receiver, "DEFAULT_CONFIG_PATH", config)
    monkeypatch.setattr(receiver, "AuditLog", lambda: audit)

    code = main(
        [
            "secs-issue-create-v1",
            "--request-file",
            str(request),
            "--signed-projection-file",
            str(projection),
            "--idempotency-key-file",
            str(idempotency),
        ]
    )
    output = capsys.readouterr()

    assert code == 2
    assert output.out == ""
    assert json.loads(output.err) == {"error": "work_object_already_exists"}
    assert "private Neo4j uniqueness detail" not in output.err
    assert "Traceback" not in output.err
    assert _graph_counts(storage) == (0, 0, 0)
    assert audit.records == []


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    (
        (
            WorkObjectRepositoryError("private repository payload"),
            "work_object_repository_error",
        ),
        (
            StorageUnavailable("private storage payload"),
            "canonical_storage_unavailable",
        ),
    ),
)
def test_cli_normalizes_expected_receiver_failures_without_copying_details(
    error: Exception,
    expected_reason: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(**_kwargs):
        raise error

    monkeypatch.setattr(cli, "execute_local_secs_issue_create_v1", fail)

    code = main(
        [
            "secs-issue-create-v1",
            "--request-file",
            str(tmp_path / "request"),
            "--signed-projection-file",
            str(tmp_path / "projection"),
            "--idempotency-key-file",
            str(tmp_path / "idempotency"),
        ]
    )
    output = capsys.readouterr()

    assert code == 2
    assert output.out == ""
    assert json.loads(output.err) == {"error": expected_reason}
    assert str(error) not in output.err
    assert "Traceback" not in output.err


def test_receiver_has_no_generic_authority_transport_or_database_input_seam() -> None:
    assert set(
        inspect.signature(receiver.execute_local_secs_issue_create_v1).parameters
    ) == {
        "idempotency_key_file",
        "request_file",
        "signed_projection_file",
    }
    source = inspect.getsource(receiver)
    for forbidden in (
        "AuthorityContext",
        "LocalDevVerifier",
        "Neo4jConfig.from_env",
        "httpx",
        "os.environ",
    ):
        assert forbidden not in source


def test_private_reader_rejects_non_owner_and_non_regular_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = _write_private(tmp_path / "input", b"safe")
    current_uid = os.geteuid()
    monkeypatch.setattr(receiver.os, "geteuid", lambda: current_uid + 1)
    with pytest.raises(receiver.LocalSecSIssueCreateError, match="owned"):
        receiver._read_private_bounded_file(path, label="test", maximum_bytes=4)
    monkeypatch.undo()
    directory = tmp_path / "directory"
    directory.mkdir(mode=0o700)
    assert stat.S_ISDIR(directory.stat().st_mode)
    with pytest.raises(receiver.LocalSecSIssueCreateError, match="regular file"):
        receiver._read_private_bounded_file(directory, label="test", maximum_bytes=4)
