"""Issuer drafts are permission ceilings, never credentials or signing authority."""

import copy
import hashlib
import json

import pytest
from pydantic import ValidationError

from devgraph.auth.access_policy import AccessDraft, attenuate, compile_read, compile_write
from devgraph.auth.delegated_contract import canonical_json


def draft(**changes):
    value = {
        "schema": "devgraph.access-draft.v1",
        "issuer": "fixture",
        "receiver": "https://work.example.test",
        "write_audience": "devgraph://receiver-local",
        "not_before": 1000,
        "not_after": 10000,
        "lifetime_seconds": 3600,
        "read_grants": [
            {
                "scope": "devgraph.work.read",
                "operations": ["list", "get"],
                "relationship_types": ["parent"],
                "work_kinds": ["Proposal", "Initiative", "Project", "Issue", "Task"],
                "include_archived": True,
            }
        ],
        "write_grants": [
            {
                "operation": "devgraph.work.patch.v2",
                "resources": [
                    {"kind": "Project"},
                    {"kind": "Task"},
                ],
            }
        ],
    }
    value.update(changes)
    return AccessDraft.model_validate(value)


def narrow(parent, **changes):
    value = json.loads(canonical_json(parent))
    value.update(changes)
    value["previous_digest"] = parent.digest
    return attenuate(parent, AccessDraft.model_validate(value))


def test_repeated_narrowing_binds_member_then_cannot_expand_or_rebind():
    root = draft()
    first = narrow(
        root, subject_public_key="a" * 64, installation_id="b" * 32, write_grants=[], not_after=8000
    )
    selected = copy.deepcopy(json.loads(canonical_json(first))["read_grants"])
    selected[0]["arena_ids"] = ["zenith"]
    selected[0]["include_archived"] = False
    second = narrow(first, read_grants=selected, lifetime_seconds=600)
    selected[0]["work_ids"] = ["Task/one"]
    final = narrow(second, read_grants=selected)
    assert final.previous_digest == second.digest
    assert final.read_grants[0].arena_ids == ("zenith",)
    for changes in (
        {"subject_public_key": "c" * 64},
        {"installation_id": "c" * 32},
        {"read_grants": json.loads(canonical_json(root))["read_grants"]},
        {"write_grants": json.loads(canonical_json(root))["write_grants"]},
        {"lifetime_seconds": 3600},
        {"not_after": 10000},
        {"not_before": 999},
        {"receiver": "https://evil.example.test"},
        {"issuer": "other"},
        {"write_audience": "devgraph://other"},
    ):
        with pytest.raises(ValueError):
            narrow(final, **changes)


def test_write_resources_narrow_by_kind_and_id_and_do_not_cross_product():
    root = draft(read_grants=[])
    final = narrow(
        root,
        subject_public_key="a" * 64,
        write_grants=[
            {
                "operation": "devgraph.work.patch.v2",
                "resources": [{"kind": "Task", "ids": ["one", "two"]}],
            }
        ],
    )
    final = narrow(
        final,
        write_grants=[
            {
                "operation": "devgraph.work.patch.v2",
                "resources": [{"kind": "Task", "ids": ["one"]}],
            }
        ],
    )
    policy = compile_write(final, policy_id="fixture", policy_version=2)
    assert policy["audience"] == "devgraph://receiver-local"
    assert policy["rules"] == [
        {
            "actor_id": "pubkey:sha256:" + hashlib.sha256(bytes.fromhex("a" * 64)).hexdigest(),
            "effect": "allow",
            "status": "active",
            "not_before": 1000,
            "not_after": 10000,
            "operation": "devgraph.work.patch.v2",
            "resource": "Task/one",
            "resource_match": "exact",
        }
    ]
    for grants in (
        [{"operation": "devgraph.work.patch.v2", "resources": [{"kind": "Task"}]}],
        [{"operation": "devgraph.work.create.v2", "resources": [{"kind": "Task", "ids": ["one"]}]}],
    ):
        with pytest.raises(ValueError):
            narrow(final, write_grants=grants)


def test_compile_read_preserves_existing_wire_contract_and_never_issues():
    root = draft()
    final = narrow(root, subject_public_key="a" * 64, installation_id="b" * 32, write_grants=[])
    policy = compile_read(final)
    assert policy.schema_ == "devgraph.native-read-policy.v1"
    assert policy.grants == root.read_grants
    assert policy.lifetime_seconds == 3600
    with pytest.raises(ValueError, match="member"):
        compile_read(root)


@pytest.mark.parametrize(
    "changes",
    [
        {"delegation": True},
        {"lifetime_seconds": True},
        {
            "write_grants": [
                {"operation": "devgraph.work.delete.v2", "resources": [{"kind": "Task"}]}
            ]
        },
        {
            "write_grants": [
                {
                    "operation": "devgraph.work.patch.v2",
                    "arena_ids": ["zenith"],
                    "resources": [{"kind": "Task"}],
                }
            ]
        },
        {
            "write_grants": [
                {"operation": "devgraph.work.patch.v2", "resources": [{"kind": "Task", "ids": []}]}
            ]
        },
        {"not_after": 999},
        {"read_grants": [], "write_grants": []},
    ],
)
def test_unknown_or_unsupported_authority_fails_before_consent(changes):
    with pytest.raises(ValidationError):
        draft(**changes)


def test_read_union_cannot_create_cross_product_of_permissions():
    root = draft(
        read_grants=[
            {
                "scope": "devgraph.work.read",
                "operations": ["get"],
                "relationship_types": ["parent"],
                "work_kinds": ["Task"],
                "arena_ids": ["a"],
                "include_archived": False,
            },
            {
                "scope": "devgraph.work.read",
                "operations": ["list"],
                "relationship_types": ["parent"],
                "work_kinds": ["Task"],
                "arena_ids": ["b"],
                "include_archived": False,
            },
        ]
    )
    grants = json.loads(canonical_json(root))["read_grants"][:1]
    grants[0]["operations"] = ["list", "get"]
    grants[0]["arena_ids"] = ["a", "b"]
    with pytest.raises(ValueError):
        narrow(root, read_grants=grants)


def test_unlinked_step_is_rejected():
    with pytest.raises(ValueError, match="previous"):
        attenuate(draft(), draft(subject_public_key="a" * 64))


def test_write_export_does_not_discard_device_restriction():
    final = narrow(draft(), subject_public_key="a" * 64, installation_id="b" * 32)
    with pytest.raises(ValueError, match="installation"):
        compile_write(final, policy_id="test", policy_version=1)


def test_historical_read_decoder_is_not_silently_extended_to_base_todo():
    values = json.loads(canonical_json(draft()))
    values["read_grants"][0]["work_kinds"] = ["Todo"]
    with pytest.raises(ValidationError):
        AccessDraft.model_validate(values)
