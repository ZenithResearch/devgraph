from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from devgraph.auth.delegated_contract import (
    ALL_DELEGATED_SCOPES,
    CredentialManifest,
    CredentialRequestProfile,
    canonical_json,
    validate_manifest_subset,
)

CONTRACT = Path(__file__).parents[2] / "docs" / "contracts" / "devgraph-delegated-read" / "v1"


def load(name: str) -> dict:
    return json.loads((CONTRACT / name).read_text())


def test_canonical_examples_validate_and_are_stable() -> None:
    profile = CredentialRequestProfile.model_validate(
        load("examples/request-profile-castalia.json")
    )
    manifest = CredentialManifest.model_validate(load("examples/credential-manifest-castalia.json"))

    validate_manifest_subset(profile, manifest)
    assert canonical_json(profile).startswith(b'{"audience":"devgraph"')
    assert canonical_json(manifest) == canonical_json(
        CredentialManifest.model_validate_json(canonical_json(manifest))
    )


def test_scope_registry_is_closed_and_complete() -> None:
    assert ALL_DELEGATED_SCOPES == frozenset(
        {
            "devgraph.graph.read",
            "devgraph.work.read",
            "devgraph.arena.read",
            "devgraph.observation.read",
            "devgraph.material.read",
            "devgraph.document.read",
            "devgraph.query.read",
        }
    )


def test_unknown_fields_empty_selectors_and_duplicate_grants_fail_closed() -> None:
    profile = load("examples/request-profile-castalia.json")
    profile["unknown"] = True
    with pytest.raises(ValidationError):
        CredentialRequestProfile.model_validate(profile)

    profile = load("examples/request-profile-castalia.json")
    profile["requested_grants"][0]["arena_ids"] = []
    with pytest.raises(ValidationError):
        CredentialRequestProfile.model_validate(profile)

    profile = load("examples/request-profile-castalia.json")
    profile["requested_grants"].append(dict(profile["requested_grants"][-1]))
    with pytest.raises(ValidationError, match="duplicate canonical grant"):
        CredentialRequestProfile.model_validate(profile)


def test_manifest_cannot_amplify_requested_authority() -> None:
    profile = CredentialRequestProfile.model_validate(
        load("examples/request-profile-castalia.json")
    )
    manifest_value = load("examples/credential-manifest-castalia.json")
    manifest_value["read_grants"][0]["include_archived"] = True
    manifest = CredentialManifest.model_validate(manifest_value)

    with pytest.raises(ValueError, match="grant_amplification"):
        validate_manifest_subset(profile, manifest)


def test_query_scope_requires_unrestricted_complete_work_grant() -> None:
    manifest_value = load("examples/credential-manifest-castalia.json")
    manifest_value["granted_scopes"].append("devgraph.query.read")
    manifest_value["read_grants"].append({"scope": "devgraph.query.read"})

    with pytest.raises(ValidationError, match="query scope"):
        CredentialManifest.model_validate(manifest_value)


def test_every_scope_has_a_grant_and_every_grant_has_a_scope() -> None:
    manifest_value = load("examples/credential-manifest-castalia.json")
    manifest_value["granted_scopes"].remove("devgraph.document.read")

    with pytest.raises(ValidationError, match="scope and grant sets differ"):
        CredentialManifest.model_validate(manifest_value)
