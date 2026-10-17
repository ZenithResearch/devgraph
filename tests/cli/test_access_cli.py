import json

from tests.auth.test_access_policy import draft, narrow

from devgraph.auth.delegated_contract import canonical_json
from devgraph.cli import main


def write(path, value):
    path.write_bytes(canonical_json(value))
    path.chmod(0o600)
    return str(path)


def test_cli_narrows_in_multiple_steps_without_native_or_network_calls(tmp_path, capsys):
    root = draft()
    parent = write(tmp_path / "root.json", root)
    selected = json.loads(canonical_json(root))
    selected.update(subject_public_key="a" * 64, write_grants=[])
    selection = write(tmp_path / "selection.json", selected)
    output = tmp_path / "member.json"
    args = [
        "auth",
        "access",
        "attenuate",
        "--parent-file",
        parent,
        "--selection-file",
        selection,
        "--output-file",
        str(output),
    ]
    assert main(args) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["authority_changed"] is False
    assert json.loads(output.read_bytes())["previous_digest"] == root.digest
    assert output.stat().st_mode & 0o777 == 0o600
    assert main(args) == 2  # Never replace an earlier reviewed selection.
    assert "access_plan_failed" in capsys.readouterr().err


def test_cli_rejects_expansion_without_publishing_output(tmp_path, capsys):
    root = draft()
    selected = narrow(root, subject_public_key="a" * 64, write_grants=[])
    parent = write(tmp_path / "parent.json", selected)
    wider = write(tmp_path / "wider.json", root)
    target = tmp_path / "not-created.json"
    assert (
        main(
            [
                "auth",
                "access",
                "attenuate",
                "--parent-file",
                parent,
                "--selection-file",
                wider,
                "--output-file",
                str(target),
            ]
        )
        == 2
    )
    assert not target.exists()
    assert "access_plan_failed" in capsys.readouterr().err


def test_cli_registry_and_write_projection_are_reviewable_not_applied(tmp_path, capsys):
    root = draft()
    member = narrow(root, subject_public_key="a" * 64, read_grants=[])
    ceiling = write(tmp_path / "ceiling.json", root)
    selected = write(tmp_path / "selected.json", member)
    output = tmp_path / "policy.json"
    assert (
        main(
            [
                "auth",
                "access",
                "export",
                "--ceiling-file",
                ceiling,
                "--draft-file",
                selected,
                "--format",
                "write",
                "--policy-id",
                "test",
                "--policy-version",
                "2",
                "--output-file",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["authority_changed"] is False
    plan = json.loads(output.read_bytes())
    assert plan["schema"] == "devgraph.member-write-plan.v1"
    assert plan["policy"]["schema"] == "secs-devgraph-work-policy.v1"
    assert plan["draft"]["subject_public_key"] == "a" * 64


def test_cli_denies_symlink_and_unknown_fields_without_disclosing_inputs(tmp_path, capsys):
    real = tmp_path / "private.json"
    real.write_text('{"secret":"do-not-log"}')
    real.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    for path in (real, link):
        assert main(["auth", "access", "inspect", "--draft-file", str(path)]) == 2
        assert "do-not-log" not in capsys.readouterr().err


def test_read_export_retains_restrictions_in_non_installable_plan(tmp_path, capsys):
    root = draft()
    selected = narrow(
        root, subject_public_key="a" * 64, installation_id="b" * 32, write_grants=[], not_after=8000
    )
    ceiling = write(tmp_path / "root.json", root)
    member = write(tmp_path / "member.json", selected)
    target = tmp_path / "read-plan.json"
    assert (
        main(
            [
                "auth",
                "access",
                "export",
                "--format",
                "read",
                "--ceiling-file",
                ceiling,
                "--draft-file",
                member,
                "--output-file",
                str(target),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["authority_changed"] is False
    plan = json.loads(target.read_bytes())
    assert plan["schema"] == "devgraph.member-read-plan.v1"
    assert plan["draft"]["installation_id"] == "b" * 32
    assert plan["draft"]["not_after"] == 8000


def test_failed_save_does_not_publish_a_partial_plan(tmp_path, monkeypatch, capsys):
    root = write(tmp_path / "root.json", draft())
    selected = write(tmp_path / "selected.json", narrow(draft(), write_grants=[]))
    target = tmp_path / "output.json"

    def failure(_fd):
        raise OSError("fixture storage failure")

    monkeypatch.setattr("devgraph.ops.access_plans.os.fsync", failure)
    assert (
        main(
            [
                "auth",
                "access",
                "attenuate",
                "--parent-file",
                root,
                "--selection-file",
                selected,
                "--output-file",
                str(target),
            ]
        )
        == 2
    )
    assert not target.exists()
    assert "access_plan_failed" in capsys.readouterr().err


def test_published_example_can_build_a_private_member_registry(tmp_path, capsys):
    from pathlib import Path

    examples = Path(__file__).resolve().parents[2] / "docs/contracts/devgraph-access-draft/v1"
    root = write(
        tmp_path / "root.json", json.loads((examples / "example-ceiling.json").read_text())
    )
    selected = write(
        tmp_path / "selection.json", json.loads((examples / "example-selection.json").read_text())
    )
    output = tmp_path / "members.json"
    assert (
        main(
            [
                "auth",
                "access",
                "registry",
                "--revision",
                "5",
                "--ceiling-file",
                root,
                "--assignment-file",
                selected,
                "--output-file",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(output.read_bytes())["revision"] == 5
    assert json.loads(capsys.readouterr().out)["authority_changed"] is False
