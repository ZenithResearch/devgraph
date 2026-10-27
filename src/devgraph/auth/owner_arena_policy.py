"""Existing-owner endorsement of an exact additive Arena metadata ceiling.

This is distinct from first-owner bootstrap and ordinary member grants. The
original signed owner registration and every Work/write rule remain intact.
"""

import copy
import hashlib
import json
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from devgraph.auth.access_approval import verify_wallet_approval
from devgraph.auth.access_policy import _Closed, _draft_validity
from devgraph.auth.arena_access import ArenaOperatorPolicy
from devgraph.auth.delegated_contract import ArenaReadGrant, canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.standing_access import StandingOperatorPolicy
from devgraph.model.validation import validate_work_object_id


def checksum(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def arena_identifiers(value):
    identifiers = ArenaReadGrant(scope="devgraph.arena.read", arena_ids=value).arena_ids
    if len(identifiers) > 256:
        raise ValueError("too many Arena identifiers")
    for identifier in identifiers:
        validate_work_object_id(identifier)
    return identifiers


class OwnerArenaUpgradeAction(_Closed):
    schema_: Literal["devgraph.access-operator-action.v10"] = Field(alias="schema")
    action: Literal["upgrade_arena_policy"]
    action_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    resource_id: str
    owner_grant_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_receiver: str
    selected_receiver: str
    affected_receivers: tuple[str, ...] = Field(min_length=1, max_length=32)
    previous_primary_policy_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_selected_policy_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    new_primary_policy_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    arena_scope: Literal["selected", "all_current_and_future"] = "selected"
    create_arenas: bool = Field(default=False, strict=True)
    arena_read_ids: tuple[str, ...] = Field(min_length=1, max_length=256)
    proposal_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("arena_read_ids", mode="before")
    @classmethod
    def identifiers(cls, value):
        return arena_identifiers(value)

    @model_validator(mode="after")
    def bound(self):
        if str(UUID(self.resource_id)) != self.resource_id:
            raise ValueError("invalid resource identity")
        if tuple(sorted(set(self.affected_receivers))) != self.affected_receivers or not {
            self.primary_receiver,
            self.selected_receiver,
        } <= set(self.affected_receivers):
            raise ValueError("exact affected receivers required")
        for value in self.affected_receivers:
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or value != "https://" + parsed.netloc
                or parsed.username
                or parsed.password
            ):
                raise ValueError("an exact HTTPS receiver is required")
        return self

    def wire(self):
        return self.model_dump(mode="json", by_alias=True)


def upgraded_policy(original, identifiers, *, arena_scope="selected", create_arenas=False):
    old = StandingOperatorPolicy.model_validate(original)
    raw = json.loads(canonical_json(old))
    raw.update(schema="devgraph.access-operator-policy.v5", revision=old.revision + 1)
    raw["ceiling"]["schema"] = "devgraph.access-draft.v6"
    raw["ceiling"]["read_grants"].append(
        dict(scope="devgraph.arena.read", arena_ids=arena_identifiers(identifiers))
    )
    if arena_scope == "all_current_and_future":
        from devgraph.auth.arena_access import OwnerArenaOperatorPolicy

        raw["schema"] = "devgraph.access-operator-policy.v6"
        raw["ceiling"]["schema"] = "devgraph.access-draft.v7"
        raw["ceiling"]["read_grants"][-1] = dict(scope="devgraph.arena.read", all_arenas=True)
        return OwnerArenaOperatorPolicy.model_validate(raw)
    if create_arenas:
        raise ValueError("Arena creation requires the all-Arena owner role")
    return ArenaOperatorPolicy.model_validate(raw)


def verify_endorsement(value, *, review, actual_policy):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "original_policy", "challenge", "signature", "approved_at"}
        or value["schema"] != "devgraph.owner-arena-endorsement.v1"
        or type(value["approved_at"]) is not int
    ):
        raise ValueError("invalid owner Arena endorsement")
    old = StandingOperatorPolicy.model_validate(value["original_policy"])
    owner = review["owner_public_key"]
    if (
        old.operator_public_keys != (owner,)
        or checksum(value["original_policy"]) != review["staged"]["authority/operators.json"]
    ):
        raise ValueError("original signed owner policy changed")
    challenge = value["challenge"]
    try:
        details = challenge["details"]
        if set(details) != {"action", "policy_revision", "policy_digest_sha256"}:
            raise ValueError("invalid owner upgrade details")
        action = OwnerArenaUpgradeAction.model_validate(details["action"])
        parsed = verify_wallet_approval(
            challenge,
            value["signature"],
            issuer=old.ceiling.issuer,
            receiver=action.selected_receiver,
            current=value["approved_at"],
        )
    except (UnauthenticatedError, KeyError, TypeError) as error:
        raise ValueError("invalid owner Arena approval") from error
    selected = copy.deepcopy(value["original_policy"])
    selected["ceiling"]["receiver"] = action.selected_receiver
    selected = StandingOperatorPolicy.model_validate(selected)
    expected = upgraded_policy(
        value["original_policy"],
        action.arena_read_ids,
        arena_scope=action.arena_scope,
        create_arenas=action.create_arenas,
    )
    if (
        parsed.subject_public_key != owner
        or parsed.operation != "access.decide"
        or action.resource_id != review["resource_id"]
        or action.owner_grant_id != checksum(review)
        or action.primary_receiver != old.ceiling.receiver
        or action.previous_primary_policy_digest_sha256 != old.digest
        or action.previous_selected_policy_digest_sha256 != selected.digest
        or details["policy_revision"] != selected.revision
        or details["policy_digest_sha256"] != selected.digest
        or action.new_primary_policy_digest_sha256 != expected.digest
        or not _draft_validity(old.ceiling).contains(value["approved_at"])
    ):
        raise ValueError("owner Arena upgrade binding changed")
    actual = type(expected).model_validate(actual_policy)
    if canonical_json(actual) != canonical_json(expected):
        raise ValueError("owner Arena endorsement does not authorize this policy")
    return expected
