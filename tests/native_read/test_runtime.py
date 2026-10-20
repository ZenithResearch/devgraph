import json
from pathlib import Path

import pytest

from devgraph.signed_read_runtime import create_app, private_json


def test_receiver_never_enables_without_explicit_configuration(monkeypatch):
    monkeypatch.delenv("DEVGRAPH_SIGNED_READ_CONFIG", raising=False)
    with pytest.raises(RuntimeError, match="explicit reviewed configuration"):
        create_app()


def test_configuration_is_private_unique_bounded_and_not_a_symlink(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"schema": "fixture"}))
    path.chmod(0o600)
    assert private_json(path) == {"schema": "fixture"}
    path.chmod(0o644)
    with pytest.raises(ValueError):
        private_json(path)

    path.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        private_json(link)
    path.write_text('{"schema":"one","schema":"two"}')
    with pytest.raises(ValueError):
        private_json(path)


@pytest.mark.parametrize("data_root", [None, "relative/path"])
def test_live_receiver_requires_retained_audit(monkeypatch, data_root):
    monkeypatch.setenv("DEVGRAPH_SIGNED_READ_CONFIG", "/not-read-before-audit-check.json")
    monkeypatch.delenv("DEVGRAPH_DATA_ROOT", raising=False)
    if data_root is not None:
        monkeypatch.setenv("DEVGRAPH_DATA_ROOT", data_root)
    with pytest.raises(RuntimeError, match="retained audit"):
        create_app()


def test_member_selection_requires_explicit_runtime_version():
    from devgraph.signed_read_runtime import ReadRuntimeConfiguration

    values = {
        "schema": "devgraph.signed-read-runtime.v1",
        **{
            field: "/private/fixture/" + field
            for field in (
                "enrollment_policy_file",
                "authority_policy_file",
                "membership_trust_file",
                "revoked_memberships_file",
                "enrollment_database_file",
                "secs_binary",
                "issuer_key_file",
            )
        },
        "secs_binary_sha256": "a" * 64,
    }
    ReadRuntimeConfiguration.model_validate(values)
    values["member_policy_file"] = "/private/fixture/members.json"
    with pytest.raises(ValueError):
        ReadRuntimeConfiguration.model_validate(values)
    values["schema"] = "devgraph.signed-read-runtime.v2"
    assert ReadRuntimeConfiguration.model_validate(values).member_policy_file.is_absolute()
    del values["member_policy_file"]
    with pytest.raises(ValueError):
        ReadRuntimeConfiguration.model_validate(values)


def test_operator_administration_requires_explicit_v4_and_separate_database():
    from devgraph.signed_read_runtime import ReadRuntimeConfiguration

    values = {
        "schema": "devgraph.signed-read-runtime.v3",
        **{
            field: "/private/fixture/" + field
            for field in (
                "member_policy_file",
                "enrollment_policy_file",
                "authority_policy_file",
                "membership_trust_file",
                "revoked_memberships_file",
                "enrollment_database_file",
                "secs_binary",
                "issuer_key_file",
            )
        },
        "secs_binary_sha256": "a" * 64,
    }
    assert ReadRuntimeConfiguration.model_validate(values).access_operator_policy_file is None
    for field in ("access_operator_policy_file", "access_inbox_database_file"):
        with pytest.raises(ValueError):
            ReadRuntimeConfiguration.model_validate({**values, field: "/private/fixture/" + field})
        with pytest.raises(ValueError):
            ReadRuntimeConfiguration.model_validate({**values, field: None})
    values["schema"] = "devgraph.signed-read-runtime.v4"
    with pytest.raises(ValueError):
        ReadRuntimeConfiguration.model_validate(values)
    values.update(
        access_operator_policy_file="/private/fixture/operators.json",
        access_inbox_database_file="/private/fixture/inbox.sqlite",
    )
    assert ReadRuntimeConfiguration.model_validate(values).access_inbox_database_file.is_absolute()
    with pytest.raises(ValueError):
        ReadRuntimeConfiguration.model_validate(
            {**values, "access_inbox_database_file": values["enrollment_database_file"]}
        )


def test_read_activation_requires_explicit_runtime_v5():
    from devgraph.signed_read_runtime import ReadRuntimeConfiguration

    values = {
        "schema": "devgraph.signed-read-runtime.v5",
        **{
            field: "/private/fixture/" + field
            for field in (
                "member_policy_file",
                "access_operator_policy_file",
                "access_inbox_database_file",
                "enrollment_policy_file",
                "authority_policy_file",
                "membership_trust_file",
                "revoked_memberships_file",
                "enrollment_database_file",
                "secs_binary",
                "issuer_key_file",
            )
        },
        "secs_binary_sha256": "a" * 64,
    }
    assert (
        ReadRuntimeConfiguration.model_validate(values).schema_ == "devgraph.signed-read-runtime.v5"
    )
    with pytest.raises(ValueError):
        ReadRuntimeConfiguration.model_validate({**values, "access_operator_policy_file": None})
    with pytest.raises(ValueError):
        ReadRuntimeConfiguration.model_validate(
            {**values, "access_inbox_database_file": values["enrollment_database_file"]}
        )


@pytest.mark.parametrize("version", ["v6", "v7"])
def test_v6_write_admission_requires_explicit_private_native_policy(version):
    from devgraph.signed_read_runtime import ReadRuntimeConfiguration

    values = {
        "schema": "devgraph.signed-read-runtime." + version,
        "secs_binary_sha256": "a" * 64,
        **{
            field: "/private/fixture/" + field
            for field in (
                "member_policy_file",
                "access_operator_policy_file",
                "access_inbox_database_file",
                "enrollment_policy_file",
                "authority_policy_file",
                "membership_trust_file",
                "revoked_memberships_file",
                "enrollment_database_file",
                "secs_binary",
                "issuer_key_file",
            )
        },
    }
    with pytest.raises(ValueError):
        ReadRuntimeConfiguration.model_validate(values)
    values["write_native_policy_file"] = "/private/fixture/native-policy.json"
    assert ReadRuntimeConfiguration.model_validate(values).write_native_policy_file.is_absolute()
    with pytest.raises(ValueError):
        ReadRuntimeConfiguration.model_validate(
            {**values, "schema": "devgraph.signed-read-runtime.v5"}
        )
