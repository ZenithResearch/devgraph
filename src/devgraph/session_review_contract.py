"""Unsigned candidate review/result bindings; never verifies or grants authority.

Trusted provider/holder inputs must come from authenticated native IPC. Native
secS/Core still validate permissions, keys, policy, signatures and actual effects.
No production route calls this adapter until that separate integration is ready.
"""

import hashlib
import re

from devgraph.auth.delegated_contract import canonical_json

SAFE = 9007199254740991
RESULT_STATES = frozenset(
    {
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
)


class SessionReviewError(ValueError):
    """Closed public error, without request/credential content."""


def _require(condition):
    if not condition:
        raise SessionReviewError("invalid_session_review_binding")


def _fields(value, keys):
    _require(isinstance(value, dict) and set(value) == set(keys.split()))


def _integer(value):
    _require(type(value) is int and 0 <= value <= SAFE)


def _text(value, pattern):
    _require(isinstance(value, str) and re.fullmatch(pattern, value) is not None)


def _canonical(value, depth=0):
    _require(depth <= 24)
    if isinstance(value, dict):
        _require(all(isinstance(key, str) for key in value))
        for item in value.values():
            _canonical(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _canonical(item, depth + 1)
    elif type(value) is int:
        _require(-SAFE <= value <= SAFE)
    else:
        _require(value is None or type(value) in (str, bool))
    raw = canonical_json(value)
    _require(len(raw) <= 65536)
    return raw


def check_review(review, *, provider, holder, expiry_limits, now):
    """Check snapshot coherence, not whether the proposed permissions are allowed."""
    _integer(now)
    _canonical(review)
    _fields(review, "manifest review_digest_sha256 state request_handle")
    _text(review["request_handle"], r"[A-Za-z0-9._:-]{1,128}")
    _require(review["state"] == "awaiting_human")
    manifest = review["manifest"]
    _fields(
        manifest,
        "schema request_id review_revision provider holder permissions "
        "not_before expires_at review_deadline challenge_id display",
    )
    _require(manifest["schema"] == "zenith.session-access-review.v1")
    _text(manifest["request_id"], r"[0-9a-f]{32}")
    _text(manifest["challenge_id"], r"[0-9a-f]{32}")
    _fields(
        manifest["provider"],
        "service issuer key_id public_key profile_digest_sha256 policy_binding",
    )
    _fields(manifest["provider"]["policy_binding"], "policy_id policy_version policy_digest_sha256")
    binding = manifest["provider"]["policy_binding"]
    _integer(binding["policy_version"])
    _require(binding["policy_version"] >= 1)
    for field in ("public_key", "profile_digest_sha256"):
        _text(manifest["provider"][field], r"[0-9a-f]{64}")
    _text(binding["policy_digest_sha256"], r"[0-9a-f]{64}")
    _require(_canonical(manifest["provider"]) == _canonical(provider))
    _fields(manifest["holder"], "session_handle public_key installation_id")
    _require(_canonical(manifest["holder"]) == _canonical(holder))
    _text(holder["public_key"], r"[0-9a-f]{64}")
    _text(holder["installation_id"], r"[0-9a-f]{32}")
    _text(holder["session_handle"], r"[A-Za-z0-9._:-]{1,128}")
    _require(isinstance(manifest["permissions"], list) and 1 <= len(manifest["permissions"]) <= 64)
    _require(all(isinstance(item, dict) for item in manifest["permissions"]))
    # Permission interpretation stays in the shared native contract, not Python.
    _fields(manifest["display"], "session_label purpose")
    for value in manifest["display"].values():
        _require(isinstance(value, str) and 1 <= len(value) <= 512)
        _require(
            not any(
                ord(c) < 32
                or 127 <= ord(c) <= 159
                or ord(c)
                in {0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}
                for c in value
            )
        )
    _fields(expiry_limits, "minimum_seconds default_seconds maximum_seconds")
    for value in expiry_limits.values():
        _integer(value)
    _require(
        0
        < expiry_limits["minimum_seconds"]
        <= expiry_limits["default_seconds"]
        <= expiry_limits["maximum_seconds"]
    )
    for field in ("review_revision", "not_before", "expires_at", "review_deadline"):
        _integer(manifest[field])
    _require(manifest["review_revision"] >= 1)
    _require(
        manifest["not_before"]
        <= now
        < manifest["review_deadline"]
        <= min(manifest["expires_at"], now + 120)
    )
    duration = manifest["expires_at"] - manifest["not_before"]
    _require(expiry_limits["minimum_seconds"] <= duration <= expiry_limits["maximum_seconds"])
    _require(review["review_digest_sha256"] == hashlib.sha256(_canonical(manifest)).hexdigest())


def check_expiry_update(
    previous, updated, *, requested_expiry, provider, holder, expiry_limits, now
):
    """Before submission only. An original unknown submission is never edited."""
    _integer(requested_expiry)
    # The old snapshot may have timed out while the user changed the expiry.
    _fields(previous, "manifest review_digest_sha256 state request_handle")
    _require(isinstance(previous["manifest"], dict))
    _require("review_deadline" in previous["manifest"])
    _integer(previous["manifest"]["review_deadline"])
    check_review(
        previous,
        provider=provider,
        holder=holder,
        expiry_limits=expiry_limits,
        now=min(now, previous["manifest"]["review_deadline"] - 1),
    )
    check_review(updated, provider=provider, holder=holder, expiry_limits=expiry_limits, now=now)
    _require(previous["request_handle"] == updated["request_handle"])
    before, after = previous["manifest"], updated["manifest"]
    mutable = {"review_revision", "expires_at", "challenge_id", "review_deadline"}
    _require(
        _canonical({k: v for k, v in before.items() if k not in mutable})
        == _canonical({k: v for k, v in after.items() if k not in mutable})
    )
    _require(after["expires_at"] == requested_expiry)
    _require(after["review_revision"] > before["review_revision"])
    _require(after["challenge_id"] != before["challenge_id"])
    _require(updated["review_digest_sha256"] != previous["review_digest_sha256"])


def check_result(review, result, *, now):
    """Reject substituted or ambiguous model-visible outcomes; not issuer evidence."""
    _integer(now)
    _canonical(result)
    _fields(
        result,
        "schema request_handle request_id review_digest_sha256 provider_service "
        "holder_public_key state credential_handle expires_at reconcile_required",
    )
    _require(result["schema"] == "zenith.session-access-result.v1")
    manifest = review["manifest"]  # A previously checked and retained review.
    _require(result["request_handle"] == review["request_handle"])
    _require(result["request_id"] == manifest["request_id"])
    _require(result["review_digest_sha256"] == review["review_digest_sha256"])
    _require(result["provider_service"] == manifest["provider"]["service"])
    _require(result["holder_public_key"] == manifest["holder"]["public_key"])
    _require(isinstance(result["state"], str) and result["state"] in RESULT_STATES)
    _require(result["reconcile_required"] is (result["state"] == "outcome_unknown"))
    if result["state"] == "issued":
        _text(result["credential_handle"], r"[A-Za-z0-9._:-]{1,128}")
        _integer(result["expires_at"])
        _require(
            result["expires_at"] == manifest["expires_at"]
            and manifest["not_before"] <= now < result["expires_at"]
        )
    else:
        _require(result["credential_handle"] is None and result["expires_at"] is None)
