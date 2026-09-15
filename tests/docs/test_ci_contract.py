from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"
PYPROJECT = ROOT / "pyproject.toml"
VERIFICATION = ROOT / "docs" / "dev" / "verification.md"
README = ROOT / "README.md"
CURRENT_STATE = ROOT / "docs" / "current-state.md"
USAGE = ROOT / "docs" / "usage.md"

REQUIRED_GATES = (
    ("Test suite", "uv run pytest -q"),
    ("Repository verification", "bash docs/dev/verification.md"),
    ("Lint", "uv run ruff check src tests"),
    ("Whitespace", "git diff --check"),
)


def workflow_text() -> str:
    return WORKFLOW.read_text()


def test_workflow_pins_the_supported_python_and_locked_uv_environment() -> None:
    text = workflow_text()

    assert "pull_request:" in text
    assert "push:" in text
    assert "branches: [main]" in text
    assert 'python-version: "3.10"' in text
    assert "uv sync --locked" in text
    assert (ROOT / ".python-version").read_text().strip() == "3.10"
    assert 'requires-python = ">=3.10"' in PYPROJECT.read_text()
    assert (ROOT / "uv.lock").is_file()
    assert "secrets." not in text
    assert "continue-on-error" not in text


def test_workflow_runs_each_required_gate_as_a_separate_named_step() -> None:
    text = workflow_text()

    for name, command in REQUIRED_GATES:
        assert f"- name: {name}" in text
        assert f"run: {command}" in text


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
