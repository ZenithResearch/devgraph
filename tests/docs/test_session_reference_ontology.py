from __future__ import annotations

import hashlib
import json
from pathlib import Path

from devgraph.model.validation import CANONICAL_WORK_KINDS
from devgraph.observability.session_ref import APPLICATION_TYPES, SESSION_REF_LABEL

ROOT = Path(__file__).parents[2]
ONTOLOGY = ROOT / "ontology"


def test_session_specializations_agree_with_common_source_owned_contract():
    graph = json.loads((ONTOLOGY / "ontology.jsonld").read_text())["@graph"]
    terms = {term["@id"]: term for term in graph}
    profile = json.loads((ONTOLOGY / "session-reference-contract.json").read_text())
    assert profile["identity_fields"] == [
        "application",
        "source_instance_id",
        "external_session_id",
    ]
    assert profile["specializations"] == APPLICATION_TYPES
    for name in APPLICATION_TYPES.values():
        assert terms["zn:" + name]["subClassOf"] == "zn:SessionRef"
        if name != "HermesSessionRef":
            assert terms["zn:" + name]["runtimeLabel"] is False
    assert terms["zn:SessionRef"]["runtimeLabel"] is True
    assert terms["zn:SessionLink"]["runtimeLabel"] is True
    assert SESSION_REF_LABEL not in CANONICAL_WORK_KINDS
    assert not any(profile["authority"].values())
    assert profile["http_operations"] == []
    assert terms["zn:SESSION_LINK_ORIGIN"]["domain"] == "zn:SessionLink"
    assert terms["zn:SESSION_LINK_TARGET"]["range"] == "zn:SessionRef"


def test_generalization_preserves_legacy_bundle_and_operational_contracts():
    previous = ONTOLOGY / "releases/v0.10.0"
    assert hashlib.sha256((previous / "manifest.json").read_bytes()).hexdigest() == (
        "4d6b556256ef6e815d474a7a80edcece49c2317f51c92bb2b97918c097074953"
    )
    for item in json.loads((previous / "manifest.json").read_text())["files"]:
        assert hashlib.sha256((previous / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    for name in ("operations.json", "arena-contract.json", "repository-contract.json"):
        old = json.loads((previous / name).read_text())
        new = json.loads((ONTOLOGY / name).read_text())
        assert old.pop("ontology_version") == "0.10.0"
        assert new.pop("ontology_version") == "0.11.0"
        assert new == old
    prior_terms = {
        x["@id"]: x for x in json.loads((previous / "ontology.jsonld").read_text())["@graph"]
    }
    new_terms = {
        x["@id"]: x for x in json.loads((ONTOLOGY / "ontology.jsonld").read_text())["@graph"]
    }
    for key, old in prior_terms.items():
        if key == "https://zenith-research.ca/ontology":
            continue
        new = dict(new_terms[key])
        if key == "zn:HermesSessionRef":
            assert new.pop("subClassOf") == "zn:SessionRef"
        assert old == new
