from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / ".github/workflows/source.yml"
RUNNER = ROOT / "scripts/ci/run.sh"
PYPROJECT = ROOT / "pyproject.toml"
VERIFICATION = ROOT / "docs" / "dev" / "verification.md"
README = ROOT / "README.md"
CURRENT_STATE = ROOT / "docs" / "current-state.md"
USAGE = ROOT / "docs" / "usage.md"


def test_actions_preserves_all_public_lanes_without_private_credentials():
    pipeline = PIPELINE.read_text()
    runner = RUNNER.read_text() + (ROOT / "scripts/ci/repository.sh").read_text()
    for lane in ("repository", "protocol", "sdk", "codeql", "holder-sessions"):
        assert lane in pipeline
    assert 'bash scripts/ci/run.sh "$DG_CI_LANE"' in pipeline
    for command in (
        "bash docs/dev/verification.md", "uv sync --locked",
        "uv run ruff check src tests scripts integrations", "git diff --check",
        "cargo +1.85.1 test --locked", "--target wasm32-unknown-unknown",
        "hostile-map-order,hostile-number-repr", "npm run test:browser",
        "installed-consumer.mjs", "scripts/audit-rust-locks.py", "scripts/build_beta.py",
    ):
        assert command in runner
    assert "continue-on-error" not in pipeline and "secrets:" not in pipeline
    assert "companion-pipeline" not in pipeline
    assert 'requires-python = ">=3.10"' in PYPROJECT.read_text()
    assert (ROOT / ".python-version").read_text().strip() == "3.10"
    assert "uv python install 3.10" in (ROOT / "scripts/ci/bootstrap.sh").read_text()


def test_holder_session_lane_pins_real_native_crypto_instead_of_skipping_it():
    import json

    pin = json.loads((ROOT / "docs/dev/holder-session-source-set.json").read_bytes())
    assert pin["secs_repository"] == "https://github.com/ZenithResearch/secS-magik"
    assert len(pin["secs_revision"]) == 40
    runner = RUNNER.read_text()
    assert "SECS_HOLDER_GRANT_TEST_BINARY=" in runner
    assert "SECS_READ_AUTHORITY_TEST_BINARY=" in runner
    assert 'test "$(git -C "$DG_CI_TMP/holder-secs" rev-parse HEAD)" = "$secs_revision"' in runner
    assert "tools/holder-grant-authority/Cargo.toml tools/read-authority/Cargo.toml" in runner
    assert "uv run pytest -q tests/native_read" in runner


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


def test_required_actions_gate_fails_for_any_incomplete_matrix():
    source = PIPELINE.read_text()
    assert "name: DevGraph CI" in source
    assert "if: always()" in source
    assert "needs: [verify]" in source
    assert "VERIFICATION_RESULT: ${{ needs.verify.result }}" in source
    assert 'test "$VERIFICATION_RESULT" = success' in source
    assert "fail-fast: false" in source
    assert "merge_group:" in source
    assert "paths:" not in source
    assert "pull_request_target:" not in source
    assert "contents: read" in source and "persist-credentials: false" in source
    assert "retention-days: 7" in source
    import os
    import subprocess
    for result in ("success", "failure", "cancelled", "skipped", ""):
        checked = subprocess.run(["bash", "-c", 'test "$VERIFICATION_RESULT" = success'],
                                 env={**os.environ, "VERIFICATION_RESULT": result})
        assert (checked.returncode == 0) == (result == "success")


def test_public_actions_cannot_enter_manual_companion_lane(tmp_path):
    import os
    import subprocess
    result = subprocess.run(["bash", str(RUNNER), "companion"], cwd=tmp_path,
                            env={**os.environ, "GITHUB_ACTIONS": "true"},
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "manual-only" in result.stderr
