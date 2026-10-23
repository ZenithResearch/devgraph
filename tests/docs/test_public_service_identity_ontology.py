import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY = ROOT / "ontology"
CLASSES = {
    "ServiceEndpoint",
    "CredentialIssuerEndpoint",
    "MembershipIssuerEndpoint",
    "ReceiverCredentialIssuerEndpoint",
    "PublicVerificationMethod",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_public_service_identity_terms_are_descriptive_and_separated():
    graph = read_json(ONTOLOGY / "ontology.jsonld")["@graph"]
    by_id = {item["@id"]: item for item in graph}

    for name in CLASSES:
        assert by_id[f"zn:{name}"]["runtimeLabel"] is False
    assert by_id["zn:CredentialIssuerEndpoint"]["subClassOf"] == "zn:ServiceEndpoint"
    assert by_id["zn:MembershipIssuerEndpoint"]["subClassOf"] == (
        "zn:CredentialIssuerEndpoint"
    )
    assert by_id["zn:ReceiverCredentialIssuerEndpoint"]["subClassOf"] == (
        "zn:CredentialIssuerEndpoint"
    )
    assert by_id["zn:EXPOSES_ENDPOINT"]["domain"] == "zn:ServiceInstance"
    assert by_id["zn:EXPOSES_ENDPOINT"]["range"] == "zn:ServiceEndpoint"
    assert by_id["zn:USES_VERIFICATION_METHOD"]["domain"] == (
        "zn:CredentialIssuerEndpoint"
    )
    assert by_id["zn:USES_VERIFICATION_METHOD"]["range"] == (
        "zn:PublicVerificationMethod"
    )


def test_public_profile_excludes_secrets_and_denies_authority_inference():
    profile = read_json(ONTOLOGY / "public-service-identities.json")
    assert profile["ontology_version"] == "0.9.0"
    assert profile["public_instance_record"]["schema"] == (
        "zenith.public-service-identity.v1"
    )
    assert not any(profile["separation"].values())
    assert {
        "private_key",
        "seed",
        "oidc_token",
        "deployment_secret",
        "wallet_custody_path",
        "session_token",
    } <= set(profile["forbidden_public_fields"])
    assert not any(profile["runtime"].values())

    text = (ONTOLOGY / "public-service-identities.md").read_text()
    assert "The receiver credential issuer is not the membership issuer" in text
    assert "receiver signing identity" in text
    assert "encryption recipient" in text
    assert "Do not create private Neo4j labels" in text


def test_public_profile_is_in_canonical_and_packaged_bundles():
    for root in (
        ONTOLOGY / "releases",
        ROOT / "src/devgraph/resources/ontology-releases",
    ):
        release = root / "v0.9.0"
        manifest_bytes = (release / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        paths = {item["path"]: item for item in manifest["files"]}
        for name in (
            "public-service-identities.md",
            "public-service-identities.json",
        ):
            assert name in paths
            payload = (release / name).read_bytes()
            assert payload == (ONTOLOGY / name).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == paths[name]["sha256"]


def test_no_runtime_constraint_or_operation_was_added():
    previous = ONTOLOGY / "releases/v0.8.0"
    assert (ONTOLOGY / "neo4j/constraints.cypher").read_bytes() == (
        previous / "constraints.cypher"
    ).read_bytes()
    old_operations = read_json(previous / "operations.json")
    new_operations = read_json(ONTOLOGY / "operations.json")
    assert old_operations.pop("ontology_version") == "0.8.0"
    assert new_operations.pop("ontology_version") == "0.9.0"
    assert old_operations == new_operations
