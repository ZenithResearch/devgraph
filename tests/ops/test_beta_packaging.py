"""Release archives and integrations exclude host state and preserve installation."""

import io
import json
import subprocess
import tarfile
from pathlib import Path

import pytest
from scripts.build_beta import build, validate_path, write_bundle
from scripts.install_agent_integration import install


def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    files = {
        "plugins/devgraph/skills/devgraph/SKILL.md": "---\nname: devgraph\n---\n",
        "plugins/devgraph/skills/devgraph/references/work.md": "Work contract\n",
        "plugins/devgraph/.codex-plugin/plugin.json": '{"name":"devgraph"}',
        "integrations/hermes/devgraph/plugin.yaml": "name: devgraph\n",
        "integrations/hermes/devgraph/__init__.py": "def register(ctx): pass\n",
        ".agents/plugins/marketplace.json": '{"name":"devgraph-beta"}',
        "pyproject.toml": '[project]\nversion = "0.1.0b1"\n',
        "LICENSE": "Synthetic test license\n",
        "SECURITY.md": "Private reporting\n",
    }
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    return root


def test_hermes_install_includes_canonical_skill_and_preserves_config(tmp_path):
    root = source(tmp_path)
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text("untouched: true\n")
    target = install("hermes", home, source_root=root)
    assert (target / "plugin.yaml").is_file()
    assert (target / "skills/devgraph/references/work.md").read_text() == "Work contract\n"
    assert (home / "config.yaml").read_text() == "untouched: true\n"
    with pytest.raises(ValueError, match="already installed"):
        install("hermes", home, source_root=root)
    assert (target / "plugin.yaml").read_text() == "name: devgraph\n"


def test_installer_rejects_source_links_and_leaves_no_partial_install(tmp_path):
    root = source(tmp_path)
    (root / "plugins/devgraph/skills/devgraph/leak").symlink_to(tmp_path / "private-key")
    home = tmp_path / "codex"
    with pytest.raises(ValueError, match="symlinks"):
        install("codex-skill", home, source_root=root)
    assert not (home / "skills/devgraph").exists()
    assert not list((home / "skills").iterdir())


@pytest.mark.parametrize("name", [".git/config", ".env", ".env.local", "secrets/key.json",
                                  "credentials/read", "user.key", "../escape", "/absolute"])
def test_release_rejects_local_state_and_unsafe_names(name):
    with pytest.raises(ValueError):
        validate_path(name)


def test_archive_is_reproducible_and_contains_no_host_identity(tmp_path):
    first, second = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
    write_bundle(first, "devgraph", {"README.md": b"demo", ".env.example": b"example"})
    write_bundle(second, "devgraph", {".env.example": b"example", "README.md": b"demo"})
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(first.read_bytes())) as archive:
        assert all(m.uid == 0 and m.gid == 0 and m.uname == "" and m.mtime == 0
                   for m in archive)


def test_release_requires_committed_review_and_bundles_both_agents(tmp_path):
    root = source(tmp_path)
    def git(*args):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    git("init", "--quiet")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "fixture")
    output = tmp_path / "release"
    result = build(output, root=root)
    assert len(result["artifacts"]) == 3
    manifest = json.loads((output / "release-manifest.json").read_text())
    assert manifest["git_history_included"] is False
    with tarfile.open(output / "devgraph-0.1.0b1-hermes.tar.gz") as archive:
        assert "devgraph/skills/devgraph/SKILL.md" in archive.getnames()
        assert "devgraph/LICENSE" in archive.getnames()
    (root / ".env").write_text("synthetic=not-a-real-secret\n")
    with pytest.raises(ValueError, match="commit the reviewed source"):
        build(tmp_path / "dirty-release", root=root)


def test_unlicensed_candidate_requires_explicit_private_review(tmp_path):
    root = source(tmp_path)
    (root / "LICENSE").unlink()
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture"],
                   cwd=root, check=True, capture_output=True)
    with pytest.raises(ValueError, match="LICENSE"):
        build(tmp_path / "public", root=root)
    result = build(tmp_path / "private", root=root, private_review=True)
    assert result["distribution"] == "private-review"
    assert result["project_license_included"] is False
    for item in result["artifacts"]:
        with tarfile.open(tmp_path / "private" / item["file"]) as archive:
            notices = [m for m in archive if m.name.endswith("/REVIEW_ONLY.txt")]
            assert len(notices) == 1
            assert b"grants no license" in archive.extractfile(notices[0]).read()
