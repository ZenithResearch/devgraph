import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY = ROOT / "ontology"
TERMS = {
    "ZenithLocalSpace",
    "LocalResourceRegistry",
    "LocalResourceRegistration",
    "ServiceInstance",
}


def test_local_space_is_independent_namespace_and_only_typed_registry_is_public():
    profile = json.loads((ONTOLOGY / "zenith-local-space.json").read_text())
    assert profile["namespace"]["folder_name"] == ".zenith"
    assert profile["namespace"]["default_path"] == "~/.zenith"
    assert profile["namespace"]["nested_under_castalia"] is False
    assert profile["registry"]["relative_path"] == "resources/v1"
    assert profile["registry"]["descriptor_schema"] == "zenith.local-resource.v1"
    assert profile["registry"]["live_heartbeat"] is False
    assert not any(profile["authority"].values())
    assert not any(profile["privacy"].values())
    text = (ONTOLOGY / "zenith-local-space.md").read_text()
    assert "The whole `.zenith` space is not public" in text
    assert "Castalia retains Wallet identity/custody" in text


def test_descriptive_terms_do_not_expand_runtime_kinds_operations_or_constraints():
    current = json.loads((ONTOLOGY / "ontology.jsonld").read_text())["@graph"]
    previous = json.loads((ONTOLOGY / "releases/v0.8.0/ontology.jsonld").read_text())["@graph"]
    by_id = {item["@id"]: item for item in current}
    for term in TERMS:
        assert by_id["zn:" + term]["runtimeLabel"] is False
        assert "subClassOf" not in by_id["zn:" + term]
    assert {item["@id"] for item in current if item.get("runtimeLabel")} == {
        item["@id"] for item in previous if item.get("runtimeLabel")
    }
    assert by_id["zn:HAS_LOCAL_REGISTRY"]["domain"] == "zn:ZenithLocalSpace"
    assert by_id["zn:HAS_LOCAL_REGISTRATION"]["range"] == "zn:LocalResourceRegistration"
    assert by_id["zn:DESCRIBES_SERVICE"]["range"] == "zn:ServiceInstance"
    for filename in ["operations.json", "arena-contract.json", "repository-contract.json"]:
        old = json.loads((ONTOLOGY / "releases/v0.8.0" / filename).read_text())
        new = json.loads((ONTOLOGY / filename).read_text())
        assert old.pop("ontology_version") == "0.8.0"
        assert new.pop("ontology_version") == "0.10.0"
        assert old == new
    assert (ONTOLOGY / "neo4j/constraints.cypher").read_bytes() == (
        ONTOLOGY / "releases/v0.8.0/constraints.cypher"
    ).read_bytes()


def test_profile_is_in_new_canonical_and_packaged_bundles_and_v08_stays_pinned():
    for root in (ONTOLOGY / "releases", ROOT / "src/devgraph/resources/ontology-releases"):
        manifest = json.loads((root / "v0.9.0/manifest.json").read_text())
        assert manifest["version"] == "0.9.0"
        assert {"zenith-local-space.md", "zenith-local-space.json"} <= {
            item["path"] for item in manifest["files"]
        }
        for name in ["zenith-local-space.md", "zenith-local-space.json"]:
            old = (root / "v0.9.0" / name).read_bytes()
            new = (ONTOLOGY / name).read_bytes()
            if name.endswith(".json"):
                a, b = json.loads(old), json.loads(new)
                a.pop("ontology_version")
                b.pop("ontology_version")
                assert a == b
            else:
                assert old == new
        assert hashlib.sha256((root / "v0.8.0/manifest.json").read_bytes()).hexdigest() == (
            "0f8e8eebc3e6c3eac45cd2be5e9d181d1174b250987fd855fec9138b277fca8d"
        )
