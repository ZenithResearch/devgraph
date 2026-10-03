"""UI/API filter parity, bounded reads, and both signed read profiles."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from test_app_scaffold import FAKE_CREDENTIAL, build_services

from devgraph.api import create_app
from devgraph.auth.scopes import SCOPE_READ
from devgraph.auth.secs_issue_create import SecSVerifierKey, SecSVerifierKeyRegistry
from devgraph.auth.secs_monitor_topology import (
    SecSMonitorTopologyAdapter,
    SecSMonitorTopologyVerifier,
)
from devgraph.auth.secs_monitor_view_read import (
    SecSMonitorViewReadPolicyBinding,
    SecSMonitorViewReadReplayCache,
    SecSMonitorViewReadVerifier,
    SecSMonitorViewReadVerifierConfig,
    _canonical_json,
)
from devgraph.topology import InvalidTopologyFilter, TopologyFilter, filter_projection

ORIGIN = "http://127.0.0.1:8080"
NOW = 1_800_000_000


def fixture_services():
    services = build_services(frozenset({SCOPE_READ}))
    for kind, identity, title, status in [
        ("Issue", "alpha", "Alpha problem", "draft"),
        ("Task", "beta", "Beta step", "accepted"),
        ("Task", "gamma", "Gamma step", "review"),
        ("Proposal", "delta", "Delta idea", "draft"),
    ]:
        services.storage.create_node(kind, identity, {"title": title, "status": status})
    services.storage.create_edge("Issue", "alpha", "HAS_CHILD", "Task", "beta")
    services.storage.create_edge("Task", "beta", "DEPENDS_ON", "Task", "gamma")
    return services


def client(services=None):
    return TestClient(create_app(services or fixture_services()), base_url=ORIGIN)


def read(api, query=""):
    return api.get(
        "/monitor/topology/v1" + ("?" + query if query else ""),
        headers={"Authorization": "Bearer " + FAKE_CREDENTIAL},
    )


def test_default_projection_and_filter_counts_are_consistent():
    api = client()
    full = read(api).json()
    assert full["complete"] is True
    assert len(full["graph_nodes"]) == 4
    filtered = read(api, "work_kind=Task&work_status=accepted").json()
    assert [n["id"] for n in filtered["graph_nodes"]] == ["beta"]
    assert filtered["graph_edges"] == []
    assert filtered["counts"]["matching_nodes"] == 1
    assert filtered["facets"]["work_status"] == {"accepted": 1, "review": 1}
    assert filtered["total_work"] == 4  # Global dashboard summary remains global.
    assert filtered["revision"] == full["revision"]
    assert read(api).headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "query",
    [
        "category=none",
        "work_kind=none",
        "work_status=none",
        "q=no-match",
        "arena=Arena:missing",
        "anchor=Task:missing",
    ],
)
def test_none_and_missing_scope_do_not_silently_expand(query):
    result = read(client(), query).json()
    assert result["graph_nodes"] == []
    if "missing" in query:
        assert result["scope_error"] in ("arena_unavailable", "anchor_unavailable")


def test_relationship_filter_keeps_isolates_and_neighborhood_uses_unhidden_edges():
    api = client()
    result = read(api, "anchor=Task:beta&work_kind=Task&relationship=none").json()
    assert {n["id"] for n in result["graph_nodes"]} == {"beta", "gamma"}
    assert result["graph_edges"] == []
    assert result["counts"]["matching_nodes"] == 2


def test_limits_disclose_partial_result_and_never_emit_dangling_edges():
    result = read(client(), "node_limit=2&edge_limit=1").json()
    assert result["complete"] is False
    assert result["counts"]["matching_nodes"] == 4
    assert result["counts"]["returned_nodes"] == 2
    keys = {n["key"] for n in result["graph_nodes"]}
    assert all(e["source"] in keys and e["target"] in keys for e in result["graph_edges"])


@pytest.mark.parametrize(
    "query",
    [
        "bad=1",
        "category=banana",
        "category=none&category=work",
        "category=work&category=work",
        "q=a&q=b",
        "node_limit=5001",
        "node_limit=0",
        "q=%GG",
        "q=%FF",
        "q=%00",
        "archived=true",
        "relationship=HAS_CHILD%20RETURN",
        "work_status=done",
        "q=" + "x" * 2049,
    ],
)
def test_invalid_filters_fail_closed(query):
    response = read(client(), query)
    assert response.status_code == 400
    assert response.json()["detail"] == ""


def test_authorization_precedes_read_and_filter_evaluation():
    api = client()
    assert api.get("/monitor/topology/v1?bad=1").status_code == 401
    assert read(client(build_services())).status_code == 403


def test_source_read_is_bounded_and_legacy_snapshot_is_not_called():
    services = fixture_services()
    services.storage.list_edges = lambda *a, **k: pytest.fail("unbounded legacy read")
    services.storage.query = lambda *a, **k: pytest.fail("unbounded legacy read")
    assert read(client(services)).status_code == 200


def test_public_observation_projection_uses_artifact_role_without_other_artifacts():
    services = fixture_services()
    for identity, role in [("observation", "initiative_observation"), ("private", "other")]:
        services.storage.create_node(
            "Artifact",
            identity,
            {
                "role": role,
                "title": identity,
                "claim_status": "unclaimed",
            },
        )
    result = read(client(services), "category=observation").json()
    assert [n["key"] for n in result["graph_nodes"]] == ["Artifact:observation"]
    assert result["observation_count"] == 1


def test_arena_reachability_precedes_facets():
    nodes = [
        {
            "key": k,
            "kind": kind,
            "category": cat,
            "id": k,
            "title": k,
            "status": "draft",
            "archived": False,
        }
        for k, kind, cat in [
            ("Arena:a", "Arena", "arena"),
            ("Issue:b", "Issue", "work"),
            ("Task:c", "Task", "work"),
            ("Task:outside", "Task", "work"),
        ]
    ]
    edges = [
        {"source": "Arena:a", "target": "Issue:b", "relationship": "CONTAINS_WORK"},
        {"source": "Issue:b", "target": "Task:c", "relationship": "HAS_CHILD"},
    ]
    result = filter_projection(
        {"graph_nodes": nodes, "graph_edges": edges},
        TopologyFilter.parse("arena=Arena:a&category=work&work_kind=Task"),
    )
    assert [n["key"] for n in result["graph_nodes"]] == ["Task:c"]


def signed_fixture(services, *, profile=SecSMonitorTopologyVerifier, target=None):
    secs_key, page_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()

    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    config = SecSMonitorViewReadVerifierConfig(
        audience="devgraph://receiver-local",
        origin=ORIGIN,
        stable_issuer="test:secs",
        policy_binding=SecSMonitorViewReadPolicyBinding("topology-test", 1, "12" * 32),
        key_registry=SecSVerifierKeyRegistry(
            [
                SecSVerifierKey("test-topology", secs_key.public_key().public_bytes_raw()),
            ]
        ),
        replay_cache=SecSMonitorViewReadReplayCache(),
        clock=lambda: NOW,
    )
    session = {
        "actor_id": "pubkey:sha256:" + "42" * 32,
        "actor_signature_suite": "Ed25519",
        "audience": config.audience,
        "expires_at": NOW + 300,
        "issued_at": NOW,
        "nonce": "AAECAwQFBgcICQoL",
        "operation": profile.operation,
        "origin": ORIGIN,
        "page_public_key_base64url": b64(page_key.public_key().public_bytes_raw()),
        "receiver_policy_digest_sha256": "12" * 32,
        "receiver_policy_id": "topology-test",
        "receiver_policy_version": 1,
        "schema": profile.session_schema,
        "schema_version": profile.version,
        "secs_context_id": "ctx:sha256:" + "24" * 32,
        "secs_verifier_key_id": "test-topology",
        "secs_verifier_signature_suite": "Ed25519",
        "session_id": "AAECAwQFBgcICQoLDA0ODw",
        "wallet_presentation_digest_sha256": "19" * 32,
    }
    session["secs_verifier_signature"] = b64(
        secs_key.sign(profile.session_signature_domain + _canonical_json(session))
    )
    target = target or TopologyFilter.parse("work_kind=Task").target()
    proof = {
        "body_digest_sha256": hashlib.sha256(b"").hexdigest(),
        "method": "GET",
        "nonce": "EBESExQVFhcYGRob",
        "operation": profile.operation,
        "origin": ORIGIN,
        "path_query": target,
        "schema": profile.proof_schema,
        "schema_version": profile.version,
        "session_digest_sha256": hashlib.sha256(
            profile.session_digest_domain + _canonical_json(session)
        ).hexdigest(),
        "session_id": session["session_id"],
        "signature_suite": "Ed25519",
        "timestamp": NOW,
    }
    proof["signature"] = b64(page_key.sign(profile.proof_signature_domain + _canonical_json(proof)))
    adapter = SecSMonitorTopologyAdapter(
        verifier=SecSMonitorTopologyVerifier(config),
        storage=services.storage,
        audit_log=services.authorized_graph._audit_log,
    )
    return (
        adapter,
        target,
        {
            "SecS-Devgraph-Monitor-Origin": ORIGIN,
            "SecS-Devgraph-Monitor-Session": b64(_canonical_json(session)),
            "SecS-Devgraph-Monitor-Proof": b64(_canonical_json(proof)),
        },
    )


def test_signed_v2_equals_bearer_and_replay_fails():
    services = fixture_services()
    adapter, target, headers = signed_fixture(services)
    api = client(replace(services, monitor_topology_read=adapter))
    bearer = read(api, target.partition("?")[2]).json()
    signed = api.get(target, headers=headers)
    assert signed.status_code == 200
    for key in ("graph_nodes", "graph_edges", "facets", "counts", "revision", "applied_filters"):
        assert signed.json()[key] == bearer[key]
    assert api.get(target, headers=headers).status_code == 401


def test_v1_authority_cannot_authorize_topology():
    services = fixture_services()
    adapter, _, headers = signed_fixture(
        services, profile=SecSMonitorViewReadVerifier, target="/monitor/snapshot"
    )
    api = client(replace(services, monitor_topology_read=adapter))
    assert api.get("/monitor/topology/v1", headers=headers).status_code == 401


@pytest.mark.parametrize("change", ["work_kind=Issue", "work_kind=%54ask", "work_kind=Task&bad=1"])
def test_signed_query_tampering_and_noncanonical_encoding_fail(change):
    services = fixture_services()
    adapter, _, headers = signed_fixture(services)
    api = client(replace(services, monitor_topology_read=adapter))
    assert api.get("/monitor/topology/v1?" + change, headers=headers).status_code == 401


def test_query_roundtrip_and_explicit_empty_selection():
    value = TopologyFilter.parse("work_kind=Task&work_kind=Issue&category=work&relationship=none")
    assert value.target().endswith("category=work&relationship=none&work_kind=Issue&work_kind=Task")
    assert TopologyFilter.parse(value.query()) == value
    with pytest.raises(InvalidTopologyFilter):
        TopologyFilter.parse("q=%")


def test_shipped_javascript_page_producer_interoperates_with_python_receiver():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node required for cross-runtime signed proof qualification")
    script = Path(__file__).parents[1] / "frontend/fixtures/topology_proof.mjs"
    fixture = json.loads(
        subprocess.run(
            [node, str(script)], check=True, capture_output=True, text=True, timeout=15
        ).stdout
    )
    services = fixture_services()
    config = SecSMonitorViewReadVerifierConfig(
        audience="devgraph://receiver-local",
        origin=ORIGIN,
        stable_issuer="test:secs",
        policy_binding=SecSMonitorViewReadPolicyBinding("topology-test", 1, "12" * 32),
        key_registry=SecSVerifierKeyRegistry(
            [SecSVerifierKey("test-topology", base64.urlsafe_b64decode(fixture["publicKey"] + "="))]
        ),
        replay_cache=SecSMonitorViewReadReplayCache(),
        clock=lambda: fixture["now"],
    )
    adapter = SecSMonitorTopologyAdapter(
        verifier=SecSMonitorTopologyVerifier(config),
        storage=services.storage,
        audit_log=services.authorized_graph._audit_log,
    )
    api = client(replace(services, monitor_topology_read=adapter))
    response = api.get(fixture["target"], headers=fixture["headers"])
    assert response.status_code == 200
    assert {n["kind"] for n in response.json()["graph_nodes"]} == {"Task"}


def test_shared_ui_query_fixtures_and_openapi_filter_discovery():
    fixtures = json.loads(
        (Path(__file__).parents[1] / "frontend/fixtures/topology_queries.json").read_text()
    )
    for fixture in fixtures:
        assert TopologyFilter.parse(fixture["query"]).query() == fixture["query"]
    params = (
        client().get("/openapi.json").json()["paths"]["/monitor/topology/v1"]["get"]["parameters"]
    )
    assert {p["name"] for p in params} == set(TopologyFilter.__dataclass_fields__)


def test_packaged_topology_assets_are_allowlisted_and_contain_no_graph_data():
    api = client()
    for name in (
        "core.js",
        "canvas.js",
        "surface.js",
        "reader.js",
        "theme.js",
        "theme.css",
        "zenith-tokens.css",
        "worker.js",
        "style.css",
        "proof.js",
    ):
        response = api.get("/monitor/topology-assets/" + name)
        assert response.status_code == 200
        assert FAKE_CREDENTIAL not in response.text
        assert response.headers["x-content-type-options"] == "nosniff"
    assert api.get("/monitor/topology-assets/not-public.js").status_code == 404


def test_topology_can_be_imported_without_auth_import_order():
    subprocess.run(
        [
            __import__("sys").executable,
            "-c",
            "from devgraph.topology import TopologyFilter; assert not TopologyFilter().query()",
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
