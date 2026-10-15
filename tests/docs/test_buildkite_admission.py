"""CI must reject mismatched source/evidence before granting a passing result."""

import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest
from scripts.ci.buildkite import (
    check_candidate_sources,
    companion_preflight,
    unpack_evidence,
    wallet_revisions,
)

ROOT = Path(__file__).resolve().parents[2]


def repository(path):
    path.mkdir()
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    return path


def commit(path):
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "fixture",
        ],
        check=True,
    )
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def test_companion_admission_rejects_prs_wrong_commits_and_dirty_sources(tmp_path):
    wallet, graph = repository(tmp_path / "wallet"), repository(tmp_path / "graph")
    wallet_sha = commit(wallet)
    source = graph / "docs/dev/generic-wallet-source-set.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"sources": {"wallet_generic": {"revision": wallet_sha}}}))
    graph_sha = commit(graph)
    env = {
        "DEVGRAPH_REVISION": graph_sha,
        "BUILDKITE_COMMIT": wallet_sha,
        "BUILDKITE_SOURCE": "ui",
        "BUILDKITE_PULL_REQUEST": "false",
    }
    assert companion_preflight(wallet, env, graph)["wallet"] == wallet_sha
    for bad in (
        {"DEVGRAPH_REVISION": "main"},
        {"BUILDKITE_COMMIT": "b" * 40},
        {"BUILDKITE_PULL_REQUEST": "10"},
        {"BUILDKITE_SOURCE": "webhook"},
    ):
        with pytest.raises(ValueError):
            companion_preflight(wallet, {**env, **bad}, graph)
    (wallet / "unreviewed").write_text("source")
    with pytest.raises(ValueError, match="uncommitted"):
        companion_preflight(wallet, env, graph)


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("candidate/link", tarfile.SYMTYPE),
        ("other/file", tarfile.REGTYPE),
    ],
)
def test_evidence_unpack_rejects_unsafe_members_without_writing(tmp_path, name, kind):
    archive = tmp_path / "input.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.linkname = "../../escape"
        stream.addfile(info)
    output = tmp_path / "output"
    with pytest.raises(ValueError):
        unpack_evidence(archive, output)
    assert not output.exists()


def test_evidence_unpack_preserves_bytes_and_rejects_duplicates(tmp_path):
    archive = tmp_path / "input.tar.gz"
    for duplicate in (False, True):
        with tarfile.open(archive, "w:gz") as stream:
            for _ in range(2 if duplicate else 1):
                info = tarfile.TarInfo("evidence/report.json")
                info.size = 2
                stream.addfile(info, io.BytesIO(b"{}"))
        output = tmp_path / str(duplicate)
        if duplicate:
            with pytest.raises(ValueError):
                unpack_evidence(archive, output)
            assert not output.exists()
        else:
            unpack_evidence(archive, output)
            assert (output / "evidence/report.json").read_bytes() == b"{}"


def test_installed_evidence_requires_all_three_exact_source_revisions():
    sources = {
        "wallet_generic": {"revision": "a" * 40},
        "secs_generic_todo": {"revision": "b" * 40},
    }
    candidate = {"sources": {"wallet": "a" * 40, "secs": "b" * 40, "devgraph_public": "c" * 40}}
    check_candidate_sources(candidate, "c" * 40, sources)
    for role in candidate["sources"]:
        changed = {"sources": {**candidate["sources"], role: "d" * 40}}
        with pytest.raises(ValueError, match="another source set"):
            check_candidate_sources(changed, "c" * 40, sources)


def test_private_history_is_fetched_by_immutable_lockfile_revisions():
    assert wallet_revisions()
    assert all(len(revision) == 40 for revision in wallet_revisions())


def test_manual_pipelines_do_not_publish_private_source_or_fixture_logs():
    text = (ROOT / ".buildkite/companion-pipeline.yml").read_text()
    assert "block:" in text and "depends_on: companion-source-review" in text
    artifacts = text.split("artifact_paths:", 1)[1]
    assert "**" not in artifacts and ".log" not in artifacts
    assert "paired-wallet" not in artifacts
    for script in (ROOT / ".buildkite").glob("*.sh"):
        subprocess.run(["bash", "-n", str(script)], check=True)
