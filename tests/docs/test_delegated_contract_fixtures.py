from __future__ import annotations

import hashlib
import json
from pathlib import Path

from devgraph.auth.delegated_contract import DENIAL_REASONS

CONTRACT = Path(__file__).parents[2] / "docs" / "contracts" / "devgraph-delegated-read" / "v1"


def test_contract_documents_are_json_and_denials_match_runtime() -> None:
    for path in sorted(CONTRACT.rglob("*.json")):
        json.loads(path.read_text())

    denials = json.loads((CONTRACT / "denials/reason-codes.json").read_text())
    assert denials["schema"] == "devgraph.delegated-denial-reasons.v1"
    assert tuple(denials["reasons"]) == DENIAL_REASONS


def test_contract_schemas_are_strict() -> None:
    for name in (
        "request-profile.schema.json",
        "credential-manifest.schema.json",
    ):
        schema = json.loads((CONTRACT / name).read_text())
        assert schema["additionalProperties"] is False
    grant = json.loads((CONTRACT / "read-grant.schema.json").read_text())
    assert all(branch["additionalProperties"] is False for branch in grant["oneOf"])


def test_integration_manifest_pins_contracts_and_released_dregg_evidence() -> None:
    manifest = json.loads((CONTRACT / "integration-manifest.json").read_text())
    for relative, expected in manifest["contract_digests_sha256"].items():
        assert hashlib.sha256((CONTRACT / relative).read_bytes()).hexdigest() == expected

    dregg = manifest["dregg_sdk"]
    assert dregg["package"] == "@dregg/sdk"
    assert dregg["version"] == "0.3.0"
    assert dregg["runtime_dependency"] is False
    assert dregg["capability_matrix"]["self_attenuating_child_capability"].startswith("deferred")
