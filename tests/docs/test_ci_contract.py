from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / ".buildkite" / "pipeline.yml"
RUNNER = ROOT / ".buildkite" / "run.sh"
PYPROJECT = ROOT / "pyproject.toml"
VERIFICATION = ROOT / "docs" / "dev" / "verification.md"
README = ROOT / "README.md"
CURRENT_STATE = ROOT / "docs" / "current-state.md"
USAGE = ROOT / "docs" / "usage.md"


def test_buildkite_preserves_all_public_lanes_without_private_credentials():
    pipeline = PIPELINE.read_text()
    runner = RUNNER.read_text() + (ROOT / ".buildkite/repository.sh").read_text()
    for lane in ("repository", "protocol", "sdk", "codeql"):
        assert f"key: {lane}" in pipeline
        assert f"bash .buildkite/run.sh {lane}" in pipeline
    for command in (
        "bash docs/dev/verification.md", "uv sync --locked",
        "uv run ruff check src tests scripts integrations", "git diff --check",
        "cargo +1.85.1 test --locked", "--target wasm32-unknown-unknown",
        "hostile-map-order,hostile-number-repr", "npm run test:browser",
        "installed-consumer.mjs", "scripts/audit-rust-locks.py", "scripts/build_beta.py",
    ):
        assert command in runner
    assert "soft_fail" not in pipeline and "secrets:" not in pipeline
    assert "companion-pipeline" not in pipeline
    assert 'requires-python = ">=3.10"' in PYPROJECT.read_text()
    assert (ROOT / ".python-version").read_text().strip() == "3.10"
    assert "uv python install 3.10" in (ROOT / ".buildkite/bootstrap.sh").read_text()


def test_repository_verification_accepts_linked_git_worktrees() -> None:
    text = VERIFICATION.read_text()

    assert "git rev-parse --is-inside-work-tree" in text
    assert "test -d .git" not in text


def test_current_docs_state_runnable_and_non_runnable_boundaries() -> None:
    readme = README.read_text()
    current = CURRENT_STATE.read_text()
    usage = USAGE.read_text()

    assert "private, loopback-only production composition" in readme
    assert "fake-credential-monitor" in usage
    assert "console-script entry point" in usage
    assert "bounded `devgraph` operator CLI" in current
    assert "No writable credential is registered" in current
    assert "does not claim encryption" in current
    assert "production credential verifier" in readme


def test_public_entry_docs_do_not_publish_personal_machine_paths() -> None:
    for path in (README, CURRENT_STATE, USAGE):
        assert "/Users/" not in path.read_text(), path


def test_continuous_integration_contract_is_current_and_bounded() -> None:
    readme = README.read_text()

    assert "## Continuous integration contract" in readme
    assert "pull requests to `main` and pushes to" in readme
    for boundary in (
        "deployment",
        "live Neo4j",
        "Matrix",
        "secS-magik",
        "Dregg",
        "Hermes runtime",
    ):
        assert boundary in readme
