"""Disposable edit lifecycle; no installed Wallet, native signer, or live data."""

import copy

import pytest
from tests.native_read.test_access_operator import call
from tests.native_read.test_access_provisioning import approve, fields, setup, v2
from tests.native_read.test_path_access_flow import approve as mixed_approve
from tests.native_read.test_path_access_flow import invoke, restart
from tests.native_read.test_path_access_flow import setup as mixed_setup

from devgraph.auth.errors import UnauthenticatedError


def baseline(tmp_path):
    inbox, service, _, public, _, op, key, policy, _, provisioning = setup(tmp_path)
    params = fields(service, op, key, policy, public)
    approve(service, op, key, params)
    result = call(service, op, key, "inbox")
    row = result["active_grants"][0]
    params.update(expected_version=2, previous_assignment_digest=row["assignment_digest"])
    params["selection"] = copy.deepcopy(row["configuration"]["selection"])
    return inbox, service, op, key, provisioning, params, result


def test_open_edit_review_and_cancel_preserve_original_until_final_approval(tmp_path):
    inbox, service, op, key, provisioning, params, result = baseline(tmp_path)
    assert result["inbox"]["total"] == 0
    assert result["activation"]["configure_existing_grants"] == "devgraph.grant-configuration.v1"
    before = provisioning.registry()
    params["selection"]["read"]["read_grants"][0]["operations"] = ["get"]
    review = v2(service, op, key, "review", **params)
    assert review["expected_version"] == 2
    assert review["current_selection"] != review["selection"]
    assert provisioning.registry() == before  # Cancelling here requires no mutation.
    with inbox._database() as db:
        assert db.execute("SELECT count(*) FROM access_requests").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM access_provisioning_audit").fetchone()[0] == 1
    # A request's submission timeout must not prevent editing its still-active grant.
    with inbox._database() as db:
        db.execute("UPDATE access_requests SET expires=1")
    applied = v2(service, op, key, "approve", review_id=review["review_id"], **params)
    assert applied["configured_existing_grant"] is True
    assert provisioning.registry().assignments[0].read_grants[0].operations == ("get",)
    assert provisioning.registry().revision == before.revision + 1
    recovered = call(service, op, key, "status", original_action_id=applied["action_id"])
    assert recovered["result"] == applied
    assert provisioning.registry().revision == before.revision + 1
    with pytest.raises(UnauthenticatedError, match="assignment_changed"):
        v2(service, op, key, "review", **params)


def test_exact_review_and_current_policy_are_still_required(tmp_path):
    _, service, op, key, provisioning, params, _ = baseline(tmp_path)
    before = provisioning.registry()
    review = v2(service, op, key, "review", **params)
    params["selection"]["read"]["read_grants"][0]["operations"] = ["get"]
    with pytest.raises(UnauthenticatedError, match="exact_review_required"):
        v2(service, op, key, "approve", review_id=review["review_id"], **params)
    assert provisioning.registry() == before
    changed = service.policy().model_copy(update={"revision": service.policy().revision + 1})
    service.policy = lambda: changed
    params["selection"] = review["selection"]
    with pytest.raises(UnauthenticatedError, match="exact_review_required"):
        v2(service, op, key, "approve", review_id=review["review_id"], **params)
    assert provisioning.registry() == before


def test_a_newer_approval_invalidates_an_already_reviewed_edit(tmp_path):
    _, service, op, key, provisioning, params, _ = baseline(tmp_path)
    first = copy.deepcopy(params)
    first["selection"]["read"]["read_grants"][0]["operations"] = ["get"]
    reviewed = v2(service, op, key, "review", **first)
    second = copy.deepcopy(params)
    second["selection"]["read"]["read_grants"][0]["operations"] = ["list"]
    approve(service, op, key, second)
    current = provisioning.registry()
    with pytest.raises(UnauthenticatedError, match="assignment_changed"):
        v2(service, op, key, "approve", review_id=reviewed["review_id"], **first)
    assert provisioning.registry() == current


def test_configure_failure_rolls_back_the_original_decision_and_assignment(tmp_path, monkeypatch):
    inbox, service, op, key, provisioning, params, _ = baseline(tmp_path)
    params["selection"]["read"]["read_grants"][0]["operations"] = ["get"]
    review = v2(service, op, key, "review", **params)
    before = provisioning.registry()
    with inbox._database() as db:
        original = db.execute("SELECT * FROM access_decisions").fetchall()
    actual = provisioning.activate

    def fail(*args, **kwargs):
        actual(*args, **kwargs)
        raise RuntimeError("interrupted replacement")

    monkeypatch.setattr(provisioning, "activate", fail)
    with pytest.raises(RuntimeError):
        v2(service, op, key, "approve", review_id=review["review_id"], **params)
    assert provisioning.registry() == before
    with inbox._database() as db:
        assert db.execute("SELECT * FROM access_decisions").fetchall() == original


def test_mixed_edit_requires_both_current_components_and_remains_revocable(tmp_path):
    s = mixed_setup(tmp_path)
    _, params, _ = mixed_approve(s)
    row = invoke(s, "inbox", schema="devgraph.access-operator-action.v4")["active_grants"][0]
    params.update(
        expected_version=2,
        previous_assignment_digest=row["assignment_digest"],
        previous_write_assignment_digest=row["write_assignment_digest"],
        selection=copy.deepcopy(row["configuration"]["selection"]),
    )
    before = s.provisioning.registry()
    review = invoke(s, "review", **params)
    assert s.provisioning.registry() == before
    partial = dict(params, selection={"read": params["selection"]["read"]})
    partial.pop("previous_write_assignment_digest")
    with pytest.raises(UnauthenticatedError, match="configuration_components_changed"):
        invoke(s, "review", schema="devgraph.access-operator-action.v4", **partial)
    params["selection"]["read"]["read_grants"][0]["operations"] = ["get"]
    review = invoke(s, "review", **params)
    result = invoke(s, "approve", review_id=review["review_id"], **params)
    assert result["configured_existing_grant"] is True
    restart(s)
    row = invoke(s, "inbox", schema="devgraph.access-operator-action.v4")["active_grants"][0]
    result = invoke(
        s,
        "revoke",
        request_id=row["request_id"],
        request_digest_sha256=row["request_digest_sha256"],
        expected_version=2,
        previous_assignment_digest=row["assignment_digest"],
        previous_write_assignment_digest=row["write_assignment_digest"],
        target_public_key=row["subject_public_key"],
        target_installation_id=row["installation_id"],
        reason="fixture",
    )
    assert result["status"] == "revoked"
