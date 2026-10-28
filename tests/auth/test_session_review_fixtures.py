"""Unsigned application-interface examples, not installed session acceptance."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.selector_grants import permission_v2
from devgraph.session_review_contract import (
    SessionReviewError,
    check_expiry_update,
    check_result,
    check_review,
)

FIXTURES = (
    Path(__file__).resolve().parents[2] / "docs/contracts/session-access-review/v1/fixtures.json"
)


def bundle():
    return json.loads(FIXTURES.read_bytes())


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def test_session_contract_is_explicitly_synthetic_and_source_pinned():
    data = bundle()
    assert data["candidate_only"] is True and data["simulated"] is True
    assert data["sources"] == {
        "devgraph": "009e91b4be47768dc65206e5bf69bf17afabc19a",
        "secs": "61e8d6c4a134be4287cc55284f472a624c378181",
        "wallet": "d3731fc0855f5a997b42161193554d555b4e54a2",
    }


def test_review_reuses_the_provider_selector_bridge_without_a_new_permission_model():
    manifest = bundle()["initial_review"]["manifest"]
    permission = manifest["permissions"][0]
    unscoped = {key: value for key, value in permission.items() if key != "roles"}
    assert permission_v2(unscoped, manifest["provider"]["service"]) == permission
    selector = permission["roles"][0]["selector"]
    assert selector["selection"] == {
        "mode": "objects",
        "refs": [{"kind": "Task", "id": "example-task"}],
    }


def test_missing_session_integration_is_explicitly_unavailable_and_writes_stay_off():
    data = bundle()
    unsupported = data["unsupported"]
    for field in (
        "session_key_enrollment",
        "session_read_issuance",
        "session_write_issuance",
        "session_request_execution",
    ):
        assert unsupported[field] is False
    assert data["simulated_read_qualified"]["session_write_issuance"] is False
    assert data["write_rejection"] == {
        "error": "session_write_issuance_unavailable",
        "authority_changed": False,
    }


def test_changed_expiry_creates_a_new_snapshot_and_invalidates_the_stale_review():
    data = bundle()
    first, updated = data["initial_review"], data["expiry_adjusted_review"]
    for value in (first, updated):
        assert value["review_digest_sha256"] == digest(value["manifest"])
    before, after = first["manifest"], updated["manifest"]
    assert before["request_id"] == after["request_id"]
    assert before["holder"] == after["holder"]
    assert before["permissions"] == after["permissions"]
    assert before["expires_at"] != after["expires_at"]
    assert after["review_revision"] == before["review_revision"] + 1
    assert before["challenge_id"] != after["challenge_id"]
    assert first["review_digest_sha256"] != updated["review_digest_sha256"]
    assert (
        data["stale_review_rejection"]["expected_review_digest_sha256"]
        == updated["review_digest_sha256"]
    )


def test_every_authority_bearing_review_field_participates_in_snapshot_binding():
    review = bundle()["initial_review"]
    for field, value in (
        ("expires_at", 2000001000),
        ("holder", {}),
        ("provider", {}),
        ("permissions", []),
        ("review_revision", 2),
        ("challenge_id", "ff" * 16),
    ):
        changed = copy.deepcopy(review["manifest"])
        changed[field] = value
        assert digest(changed) != review["review_digest_sha256"]


def test_issued_is_the_only_outcome_with_a_usable_handle_and_unknown_requires_reconcile():
    data = bundle()
    results = {value["state"]: value for value in data["results"]}
    assert set(results) == {
        "prepared",
        "awaiting_human",
        "submitting",
        "issued",
        "denied",
        "cancelled",
        "expired",
        "revoked",
        "outcome_unknown",
    }
    for state, result in results.items():
        assert result["request_id"] == data["initial_review"]["manifest"]["request_id"]
        assert result["reconcile_required"] is (state == "outcome_unknown")
        if state == "issued":
            assert result["credential_handle"] and result["expires_at"] is not None
        else:
            assert result["credential_handle"] is None and result["expires_at"] is None
        assert set(result) == {
            "schema",
            "request_handle",
            "request_id",
            "review_digest_sha256",
            "provider_service",
            "holder_public_key",
            "state",
            "credential_handle",
            "expires_at",
            "reconcile_required",
        }  # No seed, bearer, grant, presentation or signature enters model results.


def test_routes_do_not_enter_the_authority_manifest():
    data = bundle()
    assert len(set(data["route_aliases"])) == 2
    raw = canonical_json(data["initial_review"]["manifest"])
    assert all(route.encode() not in raw for route in data["route_aliases"])
    assert data["unsupported"]["provider"] == data["initial_review"]["manifest"]["provider"]


def test_review_deadline_is_distinct_from_finite_approved_expiry():
    data = bundle()
    limits = data["unsupported"]["expiry_limits"]
    for name in ("initial_review", "expiry_adjusted_review"):
        manifest = data[name]["manifest"]
        assert manifest["not_before"] < manifest["review_deadline"] < manifest["expires_at"]
        assert (
            limits["minimum_seconds"]
            <= manifest["expires_at"] - manifest["not_before"]
            <= limits["maximum_seconds"]
        )


def checked_inputs():
    data = bundle()
    return dict(
        provider=data["unsupported"]["provider"],
        holder=data["initial_review"]["manifest"]["holder"],
        expiry_limits=data["unsupported"]["expiry_limits"],
        now=2000000000,
    )


def test_candidate_adapter_checks_review_update_and_all_sanitized_outcomes():
    data = bundle()
    for name in ("initial_review", "expiry_adjusted_review", "minimum_expiry_review"):
        check_review(data[name], **checked_inputs())
    check_expiry_update(
        data["initial_review"],
        data["expiry_adjusted_review"],
        requested_expiry=2000001800,
        **checked_inputs(),
    )
    for result in data["results"]:
        check_result(data["expiry_adjusted_review"], result, now=2000000000)
    for invalid in data["invalid_results"]:
        with pytest.raises(SessionReviewError):
            check_result(data["expiry_adjusted_review"], invalid["result"], now=2000000000)


@pytest.mark.parametrize("duration", [59, 3601])
def test_expiry_outside_provider_ceiling_is_rejected_even_with_matching_digest(duration):
    value = bundle()["initial_review"]
    value["manifest"]["expires_at"] = 2000000000 + duration
    value["manifest"]["review_deadline"] = min(2000000120, 2000000000 + duration)
    value["review_digest_sha256"] = digest(value["manifest"])
    with pytest.raises(SessionReviewError):
        check_review(value, **checked_inputs())


def test_minimum_expiry_accepts_clipped_deadline_and_rejects_expired_challenge():
    value = bundle()["minimum_expiry_review"]
    check_review(value, **checked_inputs())
    with pytest.raises(SessionReviewError):
        check_review(value, **dict(checked_inputs(), now=2000000060))


@pytest.mark.parametrize("change", ["policy", "holder", "permissions", "request", "handle"])
def test_expiry_edit_cannot_silently_change_another_binding(change):
    data = bundle()
    value = data["expiry_adjusted_review"]
    if change == "policy":
        value["manifest"]["provider"]["policy_binding"]["policy_version"] += 1
    elif change == "holder":
        value["manifest"]["holder"]["public_key"] = "ff" * 32
    elif change == "permissions":
        value["manifest"]["permissions"][0]["operation"] = "devgraph.work.patch.v2"
    elif change == "request":
        value["manifest"]["request_id"] = "ff" * 16
    else:
        value["request_handle"] = "request_other"
    value["review_digest_sha256"] = digest(value["manifest"])
    with pytest.raises(SessionReviewError):
        check_expiry_update(
            data["initial_review"], value, requested_expiry=2000001800, **checked_inputs()
        )


def test_result_adapter_rejects_unknown_fields_and_expired_issued_handles():
    data = bundle()
    result = next(r for r in data["results"] if r["state"] == "issued")
    with pytest.raises(SessionReviewError):
        check_result(data["expiry_adjusted_review"], result, now=result["expires_at"])
    with pytest.raises(SessionReviewError):
        check_result(data["expiry_adjusted_review"], result, now=1999999999)
    result["signature"] = "never-export-presentations"
    with pytest.raises(SessionReviewError):
        check_result(data["expiry_adjusted_review"], result, now=2000000000)
