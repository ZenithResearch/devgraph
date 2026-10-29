"""Explicit fresh v2 enrollment; no automatic conversion or permission change."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.native_read.test_holder_grants import credential_request, host
from tests.native_read.test_http import sign
from tests.native_read.test_signed_authority import NOW

from devgraph.api.wallet_monitor import register_wallet_monitor
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.errors import UnauthenticatedError
from devgraph.storage.memory import MemoryGraphStorage


def test_fresh_v2_enrollment_preserves_v1_root_default_permissions_and_original_recovery(tmp_path):
    h, enrollment, owner, args, registry, _, _ = host(tmp_path)
    old = h.issue(**args)
    revision = registry[0].revision
    with pytest.raises(UnauthenticatedError, match="schema"):
        h.issue_v2(**args)
    assert h.issue(**args) == old
    fresh = enrollment.challenge(owner.public_key().public_bytes_raw().hex(), "a" * 32, now=NOW)
    assert fresh != args["challenge"]
    selected = dict(challenge=fresh, proof=args["proof"], signature=sign(owner, fresh), now=NOW)
    new = h.issue_v2(**selected)
    assert new["grant"]["claims"]["schema"] == "secs.holder-grant.v2"
    assert h.issue_v2(**selected) == new
    assert h.grant_schema == "secs.holder-grant.v1" and registry[0].revision == revision
    assert new["grant"]["claims"]["revision"] == old["grant"]["claims"]["revision"]
    assert new["grant"]["claims"]["grant_id"] != old["grant"]["claims"]["grant_id"]
    assert enrollment.verify_request(**credential_request(owner, old["grant"]), now=NOW)
    with enrollment._database() as db:
        roots = [json.loads(row[0]) for row in db.execute("SELECT grant FROM holder_roots")]
    assert {r["claims"]["schema"] for r in roots} == {
        "secs.holder-grant.v1",
        "secs.holder-grant.v2",
    }
    assert old["grant"] in roots


def test_discovery_advertises_explicit_v2_endpoint_without_changing_legacy_selection(tmp_path):
    h, enrollment, _, _, _, _, _ = host(tmp_path)
    app = FastAPI()
    register_wallet_monitor(
        app, storage=MemoryGraphStorage(), enrollment=enrollment, audit_log=AuditLog()
    )
    with TestClient(app, base_url="https://example.test") as client:
        config = client.get("/monitor/wallet-access/v1").json()["holder_grants"]
    assert config["schema"] == "secs.holder-grant.v1"
    assert config["endpoint"] == "/monitor/wallet-access/v1/grants"
    assert config["v2_endpoint"] == "/monitor/wallet-access/v1/grants/v2"
    assert config["supported_schemas"] == ["secs.holder-grant.v1", "secs.holder-grant.v2"]
    assert any(getattr(route, "path", "") == config["v2_endpoint"] for route in app.routes)


def test_bad_fresh_v2_signature_cannot_issue_or_change_existing_grants(tmp_path):
    h, enrollment, owner, args, _, _, _ = host(tmp_path)
    old = h.issue(**args)
    fresh = enrollment.challenge(owner.public_key().public_bytes_raw().hex(), "a" * 32, now=NOW)
    with pytest.raises(UnauthenticatedError):
        h.issue_v2(challenge=fresh, proof=args["proof"], signature="AA", now=NOW)
    assert h.issue(**args) == old
    with enrollment._database() as db:
        assert db.execute("SELECT COUNT(*) FROM holder_roots").fetchone()[0] == 1
