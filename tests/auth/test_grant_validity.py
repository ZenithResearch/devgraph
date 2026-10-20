"""Portable standing-grant time bounds; credentials remain independently finite."""

import json
from pathlib import Path

import pytest

from devgraph.auth.grant_validity import GrantValidity

VECTORS = json.loads((Path(__file__).parents[1] / "fixtures/grant-validity-v1.json").read_text())


@pytest.mark.parametrize("case", VECTORS["valid"], ids=lambda c: c["id"])
def test_active_boundaries_and_finite_credential_deadlines(case):
    validity = GrantValidity.parse(json.dumps(case["value"]).encode())
    assert json.loads(validity.to_json()) == case["value"]
    for check in case["checks"]:
        assert validity.contains(check["now"]) == check["active"]
    for check in case["deadlines"]:
        if check["deadline"] is None:
            with pytest.raises(ValueError):
                validity.credential_deadline(check["now"], check["lifetime"])
        else:
            assert (
                validity.credential_deadline(check["now"], check["lifetime"]) == check["deadline"]
            )


@pytest.mark.parametrize("case", VECTORS["invalid"], ids=lambda c: c["id"])
def test_closed_versioned_shape_rejects_ambiguous_or_unsafe_input(case):
    with pytest.raises(ValueError):
        GrantValidity.parse(case["raw"].encode())


@pytest.mark.parametrize("case", VECTORS["subsets"])
def test_child_must_remain_inside_parent_interval(case):
    child = GrantValidity.parse(json.dumps(case["child"]).encode())
    parent = GrantValidity.parse(json.dumps(case["parent"]).encode())
    assert child.contained_by(parent) == case["allowed"]


@pytest.mark.parametrize(
    "now,lifetime",
    [
        (99, 60),
        (200, 60),
        (100, 0),
        (100, 3601),
        (True, 60),
        (100, True),
        (100, 1.0),
        (9007199254740992, 60),
    ],
)
def test_inactive_or_invalid_clock_and_lifetime_cannot_mint(now, lifetime):
    with pytest.raises(ValueError):
        GrantValidity.dated(100, 200).credential_deadline(now, lifetime)


def test_legacy_adapter_keeps_old_wire_fields_and_digest():
    from test_access_policy import draft

    from devgraph.auth.delegated_contract import canonical_json

    before = draft()
    wire = canonical_json(before)
    assert before.validity == GrantValidity.dated(1000, 10000)
    assert canonical_json(before) == wire
    assert b"validity" not in wire
