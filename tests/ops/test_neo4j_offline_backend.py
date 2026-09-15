from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from devgraph.ops.neo4j_offline_backend import (
    BACKEND_ID,
    PINNED_IMAGE,
    TARGET_AUTHORITY_LABEL,
    TARGET_CLASSIFICATION,
    TARGET_CLASSIFICATION_LABEL,
    TARGET_RECEIVER_LABEL,
    BackendError,
    Neo4jCommunityOfflineDumpBackend,
    disposable_target_volume_labels,
)


class FakeRunner:
    def __init__(
        self,
        *,
        authorized: bool = True,
        exit_code: int = 0,
        timeout: bool = False,
        interrupt: bool = False,
    ) -> None:
        self.authorized = authorized
        self.exit_code = exit_code
        self.timeout = timeout
        self.interrupt = interrupt
        self.generation = 1
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        self.volumes = {"devgraph-data"}
        self.containers: set[str] = set()
        self.imported_payload: bytes | None = None

    def _volume_json(self, name: str) -> str:
        labels = None
        if name == "devgraph-data" and self.authorized:
            labels = {
                TARGET_CLASSIFICATION_LABEL: TARGET_CLASSIFICATION,
                TARGET_RECEIVER_LABEL: BACKEND_ID,
                TARGET_AUTHORITY_LABEL: "authority-fixture-001",
            }
        return json.dumps(
            {
                "CreatedAt": f"2026-07-20T18:00:00.{self.generation:09d}Z",
                "Driver": "local",
                "Labels": labels,
                "Mountpoint": f"/var/lib/docker/volumes/{name}-{self.generation}/_data",
                "Name": name,
                "Options": None,
                "Scope": "local",
            }
        )

    def __call__(self, argv, **kwargs):
        argv = tuple(argv)
        self.calls.append((argv, kwargs))
        binary = kwargs.get("text") is False
        empty = b"" if binary else ""

        if argv[:3] == ("docker", "volume", "create"):
            self.volumes.add(argv[-1])
            return subprocess.CompletedProcess(argv, 0, stdout=argv[-1] + "\n", stderr="")
        if argv[:3] == ("docker", "volume", "rm"):
            self.volumes.discard(argv[-1])
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:3] == ("docker", "volume", "inspect"):
            name = argv[3]
            if name not in self.volumes:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="not found")
            return subprocess.CompletedProcess(
                argv, 0, stdout=self._volume_json(name), stderr=""
            )
        if argv[:2] == ("docker", "create"):
            name = argv[argv.index("--name") + 1]
            self.containers.add(name)
            return subprocess.CompletedProcess(argv, 0, stdout=name + "\n", stderr="")
        if argv[:3] == ("docker", "rm", "--force"):
            self.containers.discard(argv[-1])
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if argv[:2] == ("docker", "inspect"):
            code = 0 if argv[-1] in self.containers else 1
            return subprocess.CompletedProcess(argv, code, stdout="", stderr="")
        if argv[:2] != ("docker", "run"):
            raise AssertionError(argv)

        if self.interrupt:
            raise KeyboardInterrupt
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if "dump" in argv and self.exit_code:
            return subprocess.CompletedProcess(
                argv,
                self.exit_code,
                stdout=empty,
                stderr=b"secret" if binary else "secret",
            )
        if argv[-2:] == ("neo4j-admin", "--version"):
            return subprocess.CompletedProcess(
                argv, 0, stdout="5.26.28\n", stderr="safe"
            )
        if "du" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="42\t/data\n", stderr="")
        if "find" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if "df" in argv:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                    "volume 200000 1 199999 1% /data\n"
                ),
                stderr="",
            )
        if "cat" in argv:
            return subprocess.CompletedProcess(
                argv, 0, stdout=b"real-command-shaped-dump", stderr=b""
            )
        if "tee" in argv:
            self.imported_payload = kwargs.get("input")
            return subprocess.CompletedProcess(
                argv, 0, stdout=self.imported_payload, stderr=b""
            )
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=empty,
            stderr=b"raw-secret" if binary else "raw-secret",
        )


def backend(
    tmp_path: Path,
    runner: FakeRunner,
    timeout: int = 37,
) -> Neo4jCommunityOfflineDumpBackend:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    return Neo4jCommunityOfflineDumpBackend(
        "devgraph-data",
        artifact,
        timeout_seconds=timeout,
        runner=runner,
    )


def test_target_requires_receiver_held_disposable_authority(tmp_path: Path) -> None:
    subject = backend(tmp_path, FakeRunner(authorized=False))

    assert subject.inspect_target().classification == "unclassified"


def test_target_authority_is_held_in_docker_volume_labels(tmp_path: Path) -> None:
    runner = FakeRunner()
    subject = backend(tmp_path, runner)

    target = subject.inspect_target()

    assert target.classification == TARGET_CLASSIFICATION
    assert target.physical_identity.startswith("volume-")
    assert target.empty is True
    probe_calls = [
        call for call in runner.calls if any(tool in call[0] for tool in ("find", "df"))
    ]
    assert probe_calls
    assert all("--entrypoint" in call[0] and "7474:7474" in call[0] for call in probe_calls)


def test_disposable_volume_label_builder_is_fixed_and_validated() -> None:
    labels = disposable_target_volume_labels("authority-fixture-001")

    assert labels == (
        "--label",
        f"{TARGET_CLASSIFICATION_LABEL}={TARGET_CLASSIFICATION}",
        "--label",
        f"{TARGET_RECEIVER_LABEL}={BACKEND_ID}",
        "--label",
        f"{TARGET_AUTHORITY_LABEL}=authority-fixture-001",
    )
    with pytest.raises(ValueError, match="invalid_disposable_volume_authority"):
        disposable_target_volume_labels("short")


def test_recreated_named_volume_is_rejected_before_load(tmp_path: Path) -> None:
    runner = FakeRunner()
    subject = backend(tmp_path, runner)
    subject.payload_path.write_bytes(b"dump")
    expected = subject.inspect_target()
    runner.generation += 1

    with pytest.raises(BackendError, match="invalid_restore_payload_or_target"):
        subject.load((subject.payload_path,), expected, 1)

    assert not any("load" in call[0] for call in runner.calls if call[0][:2] == ("docker", "run"))


def test_dump_uses_pinned_named_volumes_and_anchored_export(tmp_path: Path) -> None:
    runner = FakeRunner()
    subject = backend(tmp_path, runner)

    evidence = subject.dump()

    dump_call = next(call for call in runner.calls if "dump" in call[0])
    argv, kwargs = dump_call
    assert "--volumes-from" in argv
    assert any(item.startswith("type=volume,source=") for item in argv)
    assert PINNED_IMAGE in argv
    assert argv[-5:] == (
        "neo4j-admin",
        "database",
        "dump",
        "--to-path=/backups",
        "neo4j",
    )
    assert "--overwrite-destination" not in argv
    assert kwargs["shell"] is False
    assert any(
        call[0][-2:] == ("neo4j:neo4j", "/backups")
        and "--user" in call[0]
        and "--entrypoint" in call[0]
        and call[0][call[0].index("--entrypoint") + 1] == "chown"
        and call[1]["shell"] is False
        for call in runner.calls
    )
    assert str(tmp_path) not in " ".join(argv)
    assert evidence.operation == "dump"
    assert subject.payload_path.read_bytes() == b"real-command-shaped-dump"
    assert not any(name.startswith("devgraph-backup-") for name in runner.volumes)
    assert not runner.containers


def test_load_imports_verified_bytes_and_uses_pinned_volume_holder(tmp_path: Path) -> None:
    runner = FakeRunner()
    subject = backend(tmp_path, runner)
    subject.payload_path.write_bytes(b"verified dump")
    expected = subject.inspect_target()

    evidence = subject.load((subject.payload_path,), expected, 1)

    load_call = next(call for call in runner.calls if "load" in call[0])
    argv, kwargs = load_call
    assert "--volumes-from" in argv
    assert any(item.startswith("type=volume,source=") for item in argv)
    assert "--overwrite-destination" not in argv
    assert str(tmp_path) not in " ".join(argv)
    assert kwargs["shell"] is False
    assert runner.imported_payload == b"verified dump"
    assert evidence.operation == "load"
    assert subject.last_evidence is not None
    assert subject.last_evidence.operation in {"load", "volume_inspect"}
    assert not any(name.startswith("devgraph-backup-") for name in runner.volumes)
    assert not runner.containers


def test_version_uses_same_pinned_image(tmp_path: Path) -> None:
    runner = FakeRunner()
    subject = backend(tmp_path, runner)

    evidence = subject.version()

    argv, _ = runner.calls[0]
    assert PINNED_IMAGE in argv
    assert argv[-2:] == ("neo4j-admin", "--version")
    assert evidence.reported_version == "5.26.28"


@pytest.mark.parametrize("operation", ["dump", "version"])
def test_backend_timeout_is_fixed_safe_error(tmp_path: Path, operation: str) -> None:
    runner = FakeRunner(timeout=True)
    subject = backend(tmp_path, runner)

    with pytest.raises(BackendError, match="backend_timeout") as captured:
        getattr(subject, operation)()

    assert captured.value.__cause__ is None
    assert "secret" not in repr(captured.value.evidence).lower()
    assert any(call[0][:3] == ("docker", "rm", "--force") for call in runner.calls)
    assert any(call[0][:2] == ("docker", "inspect") for call in runner.calls)


def test_backend_interruption_is_fixed_and_cleans_container(tmp_path: Path) -> None:
    runner = FakeRunner(interrupt=True)
    subject = backend(tmp_path, runner)

    with pytest.raises(BackendError, match="backend_interrupted") as captured:
        subject.version()

    assert captured.value.__cause__ is None
    assert not runner.containers


def test_backend_nonzero_exit_is_fixed_safe_error(tmp_path: Path) -> None:
    runner = FakeRunner(exit_code=17)
    subject = backend(tmp_path, runner)

    with pytest.raises(BackendError, match="backend_command_failed") as captured:
        subject.dump()

    assert captured.value.evidence.exit_code == 17
    assert "secret" not in repr(captured.value.evidence).lower()
    assert str(tmp_path) not in repr(captured.value.evidence)
    assert not runner.containers
    assert not any(name.startswith("devgraph-backup-") for name in runner.volumes)


@pytest.mark.parametrize("volume", ["", "/host/path", "bad volume", "../escape"])
def test_backend_rejects_invalid_volume_names(tmp_path: Path, volume: str) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()

    with pytest.raises(ValueError, match="invalid_owned_backend_resources"):
        Neo4jCommunityOfflineDumpBackend(volume, artifact)


def test_backend_rejects_relative_or_symlinked_artifact_root(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    link = tmp_path / "link"
    link.symlink_to(artifact, target_is_directory=True)

    with pytest.raises(ValueError, match="invalid_owned_backend_resources"):
        Neo4jCommunityOfflineDumpBackend("devgraph-data", Path("relative"))
    with pytest.raises(ValueError, match="invalid_owned_backend_resources"):
        Neo4jCommunityOfflineDumpBackend("devgraph-data", link)
