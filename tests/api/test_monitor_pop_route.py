from __future__ import annotations

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from test_app_scaffold import FAKE_CREDENTIAL, build_services

import devgraph.auth.secs_monitor_view_read as exact
from devgraph.api import create_app
from devgraph.auth.scopes import SCOPE_READ
from devgraph.auth.secs_issue_create import SecSVerifierKey, SecSVerifierKeyRegistry
from devgraph.auth.secs_monitor_view_read import (
    DEVGRAPH_MONITOR_ORIGIN_HEADER_V1,
    DEVGRAPH_MONITOR_REQUEST_PROOF_HEADER_V1,
    DEVGRAPH_MONITOR_SESSION_HEADER_V1,
)
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Project
from devgraph.monitoring import build_monitor_snapshot

ORIGIN = "http://127.0.0.1:8080"
NOW = 1_800_000_000


class _RecordingExactMonitorReceiver:
    def __init__(self, storage) -> None:
        self.storage = storage
        self.calls: list[dict[str, object]] = []

    def execute(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return build_monitor_snapshot(self.storage)


class _BlockingExactMonitorReceiver(_RecordingExactMonitorReceiver):
    def __init__(self, storage, entered: Event, release: Event) -> None:
        super().__init__(storage)
        self.entered = entered
        self.release = release

    def execute(self, **kwargs) -> dict:
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("monitor test release timed out")
        return super().execute(**kwargs)


def _pop_headers() -> dict[str, str]:
    return {
        DEVGRAPH_MONITOR_ORIGIN_HEADER_V1: ORIGIN,
        DEVGRAPH_MONITOR_SESSION_HEADER_V1: "synthetic-session-header",
        DEVGRAPH_MONITOR_REQUEST_PROOF_HEADER_V1: "synthetic-proof-header",
    }


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _encoded(value: dict[str, object]) -> str:
    return _b64url(exact._canonical_json(value))


def _real_receiver_and_headers(services) -> tuple[object, dict[str, str]]:
    secs_key = Ed25519PrivateKey.generate()
    page_key = Ed25519PrivateKey.generate()
    policy_digest = "7c" * 32
    verifier = exact.SecSMonitorViewReadVerifier(
        exact.SecSMonitorViewReadVerifierConfig(
            audience=exact.DEVGRAPH_MONITOR_VIEW_READ_AUDIENCE_V1,
            origin=ORIGIN,
            stable_issuer="secs:devgraph-receiver-local",
            policy_binding=exact.SecSMonitorViewReadPolicyBinding(
                policy_id="devgraph-monitor-view-local-v1",
                policy_version=1,
                policy_digest_sha256=policy_digest,
            ),
            key_registry=SecSVerifierKeyRegistry(
                [
                    SecSVerifierKey(
                        key_id="secs-monitor-http-v1",
                        public_key=secs_key.public_key().public_bytes_raw(),
                    )
                ]
            ),
            replay_cache=exact.SecSMonitorViewReadReplayCache(),
            clock=lambda: NOW,
        )
    )
    session_unsigned: dict[str, object] = {
        "actor_id": f"pubkey:sha256:{'42' * 32}",
        "actor_signature_suite": "Ed25519",
        "audience": exact.DEVGRAPH_MONITOR_VIEW_READ_AUDIENCE_V1,
        "expires_at": NOW + 300,
        "issued_at": NOW,
        "nonce": "AAECAwQFBgcICQoL",
        "operation": exact.DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1,
        "origin": ORIGIN,
        "page_public_key_base64url": _b64url(
            page_key.public_key().public_bytes_raw()
        ),
        "receiver_policy_digest_sha256": policy_digest,
        "receiver_policy_id": "devgraph-monitor-view-local-v1",
        "receiver_policy_version": 1,
        "schema": exact.DEVGRAPH_MONITOR_SESSION_SCHEMA_V1,
        "schema_version": 1,
        "secs_context_id": f"ctx:sha256:{'24' * 32}",
        "secs_verifier_key_id": "secs-monitor-http-v1",
        "secs_verifier_signature_suite": "Ed25519",
        "session_id": "AAECAwQFBgcICQoLDA0ODw",
        "wallet_presentation_digest_sha256": "19" * 32,
    }
    session = {
        **session_unsigned,
        "secs_verifier_signature": _b64url(
            secs_key.sign(
                exact.DEVGRAPH_MONITOR_SESSION_SIGNATURE_DOMAIN_V1
                + exact._canonical_json(session_unsigned)
            )
        ),
    }
    proof_unsigned: dict[str, object] = {
        "body_digest_sha256": hashlib.sha256(b"").hexdigest(),
        "method": "GET",
        "nonce": "EBESExQVFhcYGRob",
        "operation": exact.DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1,
        "origin": ORIGIN,
        "path_query": "/monitor/snapshot",
        "schema": exact.DEVGRAPH_MONITOR_REQUEST_PROOF_SCHEMA_V1,
        "schema_version": 1,
        "session_digest_sha256": hashlib.sha256(
            exact.DEVGRAPH_MONITOR_SESSION_DIGEST_DOMAIN_V1
            + exact._canonical_json(session)
        ).hexdigest(),
        "session_id": session["session_id"],
        "signature_suite": "Ed25519",
        "timestamp": NOW,
    }
    proof = {
        **proof_unsigned,
        "signature": _b64url(
            page_key.sign(
                exact.DEVGRAPH_MONITOR_REQUEST_PROOF_SIGNATURE_DOMAIN_V1
                + exact._canonical_json(proof_unsigned)
            )
        ),
    }
    return (
        exact.SecSMonitorViewReadAdapter(
            verifier=verifier,
            storage=services.storage,
            audit_log=services.authorized_graph._audit_log,
        ),
        {
            DEVGRAPH_MONITOR_ORIGIN_HEADER_V1: ORIGIN,
            DEVGRAPH_MONITOR_SESSION_HEADER_V1: _encoded(session),
            DEVGRAPH_MONITOR_REQUEST_PROOF_HEADER_V1: _encoded(proof),
        },
    )


def test_exact_monitor_headers_select_only_the_pop_receiver_and_bind_raw_target() -> None:
    services = build_services(frozenset({SCOPE_READ}))
    receiver = _RecordingExactMonitorReceiver(services.storage)
    services = replace(services, monitor_view_read=receiver)  # type: ignore[arg-type]
    api = TestClient(
        create_app(services),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )

    response = api.get("/monitor/snapshot", headers=_pop_headers())

    assert response.status_code == 200
    assert len(receiver.calls) == 1
    assert receiver.calls[0] == {
        "signed_session_header": "synthetic-session-header",
        "request_proof_header": "synthetic-proof-header",
        "method": "GET",
        "path_query": "/monitor/snapshot",
        "origin": ORIGIN,
        "body": b"",
    }
    assert services.authorized_graph._audit_log.records == []


def test_real_exact_headers_verify_through_http_and_replay_fails_closed() -> None:
    services = build_services(frozenset())
    WorkObjectRepository(services.storage).create(
        Project(id="project-pop-http", title="PoP HTTP")
    )
    receiver, headers = _real_receiver_and_headers(services)
    services = replace(services, monitor_view_read=receiver)  # type: ignore[arg-type]
    api = TestClient(
        create_app(services),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )

    first = api.get("/monitor/snapshot", headers=headers)
    replay = api.get("/monitor/snapshot", headers=headers)

    assert first.status_code == 200
    assert first.json()["total_work"] == 1
    assert replay.status_code == 401
    assert replay.json()["detail"] == "monitor proof denied"
    assert [record.operation for record in services.authorized_graph._audit_log.records] == [
        exact.DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1
    ]
    assert services.storage.query("EventReceipt") == []


def test_exact_monitor_headers_do_not_authorize_observation_or_work_routes() -> None:
    services = build_services(frozenset())
    receiver, headers = _real_receiver_and_headers(services)
    services = replace(services, monitor_view_read=receiver)  # type: ignore[arg-type]
    api = TestClient(
        create_app(services),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )

    observation = api.get("/initiative-observations", headers=headers)
    work = api.get("/work/Issue", headers=headers)

    assert observation.status_code == 401
    assert work.status_code == 401
    assert services.authorized_graph._audit_log.records == []


def test_bearer_monitor_route_remains_compatible_when_no_pop_headers_exist() -> None:
    services = build_services(frozenset({SCOPE_READ}))
    receiver = _RecordingExactMonitorReceiver(services.storage)
    services = replace(services, monitor_view_read=receiver)  # type: ignore[arg-type]
    api = TestClient(create_app(services), raise_server_exceptions=False)

    response = api.get(
        "/monitor/snapshot",
        headers={"Authorization": f"Bearer {FAKE_CREDENTIAL}"},
    )

    assert response.status_code == 200
    assert receiver.calls == []
    assert [record.operation for record in services.authorized_graph._audit_log.records] == [
        "monitor_snapshot"
    ]


@pytest.mark.parametrize("mode", ["pop", "bearer"])
def test_slow_monitor_snapshot_does_not_stall_live_route(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = build_services(frozenset({SCOPE_READ}))
    entered = Event()
    release = Event()
    headers: dict[str, str]
    if mode == "pop":
        receiver = _BlockingExactMonitorReceiver(services.storage, entered, release)
        services = replace(services, monitor_view_read=receiver)  # type: ignore[arg-type]
        headers = _pop_headers()
    else:
        original_snapshot = services.authorized_graph.monitor_snapshot

        def blocking_snapshot(credential: str | None) -> dict:
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("monitor test release timed out")
            return original_snapshot(credential)

        monkeypatch.setattr(
            services.authorized_graph,
            "monitor_snapshot",
            blocking_snapshot,
        )
        headers = {"Authorization": f"Bearer {FAKE_CREDENTIAL}"}

    with TestClient(
        create_app(services),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    ) as api:
        with ThreadPoolExecutor(max_workers=2) as executor:
            monitor = executor.submit(api.get, "/monitor/snapshot", headers=headers)
            assert entered.wait(timeout=2)
            live = executor.submit(api.get, "/live")
            try:
                live_response = live.result(timeout=2)
            finally:
                release.set()
            monitor_response = monitor.result(timeout=2)

    assert live_response.status_code == 200
    assert live_response.json() == {"live": True}
    assert monitor_response.status_code == 200


def test_missing_receiver_incomplete_headers_and_mixed_bearer_fail_closed() -> None:
    services = build_services(frozenset({SCOPE_READ}))
    receiver = _RecordingExactMonitorReceiver(services.storage)
    with_receiver = replace(services, monitor_view_read=receiver)  # type: ignore[arg-type]
    api = TestClient(
        create_app(with_receiver),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )

    incomplete = api.get(
        "/monitor/snapshot",
        headers={
            DEVGRAPH_MONITOR_ORIGIN_HEADER_V1: ORIGIN,
            DEVGRAPH_MONITOR_SESSION_HEADER_V1: "synthetic-session-header",
        },
    )
    mixed = api.get(
        "/monitor/snapshot",
        headers={
            **_pop_headers(),
            "Authorization": f"Bearer {FAKE_CREDENTIAL}",
        },
    )
    unavailable = TestClient(
        create_app(services),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    ).get("/monitor/snapshot", headers=_pop_headers())

    assert incomplete.status_code == 401
    assert mixed.status_code == 401
    assert unavailable.status_code == 401
    assert incomplete.json()["detail"] == "monitor proof denied"
    assert mixed.json()["detail"] == "monitor proof denied"
    assert unavailable.json()["detail"] == "monitor proof verification unavailable"
    assert receiver.calls == []
    assert services.authorized_graph._audit_log.records == []
    rendered = incomplete.text + mixed.text + unavailable.text
    assert "synthetic-session-header" not in rendered
    assert "synthetic-proof-header" not in rendered


def test_duplicate_exact_or_authorization_headers_deny_before_any_verifier() -> None:
    services = build_services(frozenset({SCOPE_READ}))
    receiver = _RecordingExactMonitorReceiver(services.storage)
    services = replace(services, monitor_view_read=receiver)  # type: ignore[arg-type]
    api = TestClient(
        create_app(services),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )
    exact_headers = list(_pop_headers().items())

    responses = []
    for duplicate_name in (
        DEVGRAPH_MONITOR_ORIGIN_HEADER_V1,
        DEVGRAPH_MONITOR_SESSION_HEADER_V1,
        DEVGRAPH_MONITOR_REQUEST_PROOF_HEADER_V1,
    ):
        duplicate_value = dict(exact_headers)[duplicate_name]
        responses.append(
            api.get(
                "/monitor/snapshot",
                headers=[*exact_headers, (duplicate_name, duplicate_value)],
            )
        )
    responses.append(
        api.get(
            "/monitor/snapshot",
            headers=[
                ("Authorization", f"Bearer {FAKE_CREDENTIAL}"),
                ("Authorization", f"Bearer {FAKE_CREDENTIAL}"),
            ],
        )
    )

    assert [response.status_code for response in responses] == [401, 401, 401, 401]
    assert all(response.json()["detail"] == "monitor proof denied" for response in responses)
    assert receiver.calls == []
    assert services.authorized_graph._audit_log.records == []


def test_exact_route_rejects_body_framing_and_host_aliases_before_verifier() -> None:
    services = build_services(frozenset({SCOPE_READ}))
    receiver = _RecordingExactMonitorReceiver(services.storage)
    services = replace(services, monitor_view_read=receiver)  # type: ignore[arg-type]
    api = TestClient(
        create_app(services),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )

    responses = [
        api.request(
            "GET",
            "/monitor/snapshot",
            headers=_pop_headers(),
            content=b"not-empty",
        ),
        api.get(
            "/monitor/snapshot",
            headers={**_pop_headers(), "Content-Length": "0"},
        ),
        api.get(
            "/monitor/snapshot",
            headers={**_pop_headers(), "Transfer-Encoding": "chunked"},
        ),
        api.get(
            "/monitor/snapshot",
            headers={
                **_pop_headers(),
                "Content-Length": "0",
                "Transfer-Encoding": "chunked",
            },
        ),
        api.get(
            "/monitor/snapshot",
            headers={**_pop_headers(), "Host": "localhost:8080"},
        ),
        api.get(
            "/monitor/snapshot",
            headers={**_pop_headers(), "Host": "127.0.0.1"},
        ),
        api.get(
            "/monitor/snapshot",
            headers=[
                *_pop_headers().items(),
                ("Host", "127.0.0.1:8080"),
                ("Host", "127.0.0.1:8080"),
            ],
        ),
    ]

    assert [response.status_code for response in responses] == [401] * 7
    assert all(response.json()["detail"] == "monitor proof denied" for response in responses)
    assert receiver.calls == []
    assert services.authorized_graph._audit_log.records == []


def test_raw_query_is_forwarded_for_exact_verifier_rejection_not_ignored() -> None:
    services = build_services(frozenset({SCOPE_READ}))
    receiver = _RecordingExactMonitorReceiver(services.storage)
    services = replace(services, monitor_view_read=receiver)  # type: ignore[arg-type]
    api = TestClient(
        create_app(services),
        base_url=ORIGIN,
        raise_server_exceptions=False,
    )

    response = api.get("/monitor/snapshot?unexpected=1", headers=_pop_headers())

    assert response.status_code == 200
    assert receiver.calls[0]["path_query"] == "/monitor/snapshot?unexpected=1"
