"""Authenticated terminal reviews and canonical lock/crypto deadline races."""

from contextlib import contextmanager
from datetime import timedelta
from sqlite3 import IntegrityError
from threading import Event, Thread

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.native_read.test_holder_grants import alias
from tests.native_read.test_holder_session_exchange import setup, status
from tests.native_read.test_signed_authority import NOW

from devgraph.api.holder_sessions import register_holder_sessions
from devgraph.auth import holder_sessions as module
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.holder_sessions import HolderSessionHost


def retained(service):
    with service.host.provider._database() as db:
        return db.execute(
            "SELECT authorization_digest,credential FROM holder_sessions"
        ).fetchone(), db.execute(
            "SELECT COUNT(*) FROM holder_session_expired_reviews"
        ).fetchone()[0]


def test_status_before_and_at_deadline_is_terminal_across_restart_and_rollback(tmp_path):
    service, auth, key, _, _, _ = setup(tmp_path)
    challenge = auth["challenge"]
    before, deadline = NOW + timedelta(seconds=119), NOW + timedelta(seconds=120)
    assert service.status(status(key, challenge, now=before), now=before)["state"] == "pending"
    expired = service.status(status(key, challenge, nonce="78" * 16, now=deadline), now=deadline)
    assert expired["state"] == "expired"
    assert expired["credential"] is None
    restarted = HolderSessionHost(service.host, service.policy)
    assert restarted.status(
        status(key, challenge, nonce="79" * 16, now=before), now=before
    )["state"] == "expired"
    assert restarted.exchange(auth, now=before)["state"] == "expired"
    assert retained(service) == ((None, None), 1)


def test_issued_result_remains_recoverable_after_review_deadline(tmp_path):
    service, auth, key, _, _, _ = setup(tmp_path)
    original = service.exchange(auth, now=NOW)
    later = NOW + timedelta(seconds=121)
    recovered = service.status(status(key, auth["challenge"], now=later), now=later)
    assert recovered == original
    assert retained(service)[1] == 0


def test_existing_issued_store_gains_terminal_table_without_changing_original_result(tmp_path):
    service, auth, _, _, _, _ = setup(tmp_path)
    original = service.exchange(auth, now=NOW)
    with service.host.provider._database() as db:
        db.execute("DROP TABLE holder_session_expired_reviews")
    restarted = HolderSessionHost(service.host, service.policy)
    assert restarted.exchange(auth, now=NOW + timedelta(seconds=121)) == original
    assert retained(restarted)[1] == 0


def test_terminal_review_is_shared_by_another_registered_alias(tmp_path):
    service, auth, _, _, provider, registry = setup(tmp_path)
    assert service.exchange(auth, now=NOW + timedelta(seconds=120))["state"] == "expired"
    remote_host, _ = alias(tmp_path / "alias", provider, registry)
    remote = HolderSessionHost(remote_host, service.policy)
    assert remote.exchange(auth, now=NOW + timedelta(seconds=119))["state"] == "expired"
    assert retained(remote) == ((None, None), 1)


def test_database_rejects_a_stale_writer_that_ignores_terminal_review(tmp_path):
    service, auth, _, _, _, _ = setup(tmp_path)
    assert service.exchange(auth, now=NOW + timedelta(seconds=120))["state"] == "expired"
    # A process running the old UPDATE path must not resurrect the credential
    # using an admission timestamp captured before it waited for the DB lock.
    with service.host.provider._database() as db:
        with pytest.raises(IntegrityError, match="review_expired"):
            db.execute(
                "UPDATE holder_sessions SET authorization_digest=?,credential=? "
                "WHERE service=? AND request_id=? AND credential IS NULL",
                ("old-auth", "{}", service.host.service, auth["challenge"]["request_id"]),
            )
    assert retained(service) == ((None, None), 1)


def test_http_authenticated_status_returns_existing_expired_envelope(tmp_path):
    service, auth, key, _, _, _ = setup(tmp_path)
    later = NOW + timedelta(seconds=120)
    app = FastAPI()
    register_holder_sessions(app, host=service.host, now=lambda: later)
    with TestClient(app) as client:
        response = client.post(
            "/holder-sessions/v1/status", json=status(key, auth["challenge"], now=later)
        )
    assert response.status_code == 200
    assert response.json() == {
        "schema": "secs.holder-session-result.v1",
        "request_id": auth["challenge"]["request_id"],
        "challenge_id": auth["challenge"]["challenge_id"],
        "state": "expired",
        "credential": None,
        "expires_at": auth["challenge"]["expires_at"],
    }


def test_unauthenticated_status_cannot_expire_a_review(tmp_path):
    service, auth, _, _, _, _ = setup(tmp_path)
    later = NOW + timedelta(seconds=120)
    bad = status(Ed25519PrivateKey.generate(), auth["challenge"], now=later)
    with pytest.raises(UnauthenticatedError):
        service.status(bad, now=later)
    assert retained(service) == ((None, None), 0)
    assert service.exchange(auth, now=NOW)["state"] == "issued"


def test_crypto_finishing_at_deadline_does_not_retain_or_disclose_a_token(tmp_path, monkeypatch):
    service, auth, _, _, _, _ = setup(tmp_path)
    clock = [auth["challenge"]["issued_at"]]
    monkeypatch.setattr(module, "_seconds", lambda _now: clock[0])
    original = service.host.invoke

    def issue(operation, **fields):
        result = original(operation, **fields)
        if operation == "issue_holder_session":
            clock[0] = auth["challenge"]["review_deadline"]
        return result

    monkeypatch.setattr(service.host, "invoke", issue)
    expired = service.exchange(auth)
    assert expired["state"] == "expired"
    assert expired["credential"] is None
    assert retained(service) == ((None, None), 1)


def test_final_parent_check_crossing_deadline_cannot_commit_a_token(tmp_path, monkeypatch):
    service, auth, _, _, _, _ = setup(tmp_path)
    clock = [auth["challenge"]["issued_at"]]
    monkeypatch.setattr(module, "_seconds", lambda _now: clock[0])
    parent = service._parent
    checks = []

    def delayed_parent(*arguments):
        result = parent(*arguments)
        checks.append(True)
        if len(checks) == 2:
            clock[0] = auth["challenge"]["review_deadline"]
        return result

    monkeypatch.setattr(service, "_parent", delayed_parent)
    assert service.exchange(auth)["state"] == "expired"
    assert len(checks) == 2
    assert retained(service) == ((None, None), 1)


def test_status_proof_expiring_during_crypto_does_not_disclose_original(tmp_path, monkeypatch):
    service, auth, key, _, _, _ = setup(tmp_path)
    service.exchange(auth, now=NOW)
    proof = status(key, auth["challenge"])
    clock = [auth["challenge"]["issued_at"]]
    monkeypatch.setattr(module, "_seconds", lambda _now: clock[0])
    original = module.invoke_native

    def verify(*args, **fields):
        result = original(*args, **fields)
        clock[0] = proof["expires_at"]
        return result

    monkeypatch.setattr(module, "invoke_native", verify)
    with pytest.raises(UnauthenticatedError, match="status_expired"):
        service.status(proof)
    with service.host.provider._database() as db:
        assert db.execute("SELECT COUNT(*) FROM holder_session_status_replay").fetchone() == (0,)


@pytest.mark.parametrize("operation", ["exchange", "status"])
def test_waiting_for_canonical_lock_uses_time_after_lock_acquisition(
    tmp_path, monkeypatch, operation
):
    service, auth, key, _, provider, _ = setup(tmp_path)
    clock = [auth["challenge"]["issued_at"]]
    monkeypatch.setattr(module, "_seconds", lambda _now: clock[0])
    waiting, finished = Event(), Event()
    result, errors = [], []
    database = provider._database

    class Connection:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *arguments):
            if sql == "BEGIN IMMEDIATE":
                waiting.set()
            return self.db.execute(sql, *arguments)

        def __getattr__(self, name):
            return getattr(self.db, name)

    @contextmanager
    def delayed_database():
        with database() as db:
            yield Connection(db)

    def worker():
        try:
            result.append(
                service.exchange(auth)
                if operation == "exchange"
                else service.status(status(key, auth["challenge"]))
            )
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()

    with database() as lock:
        lock.execute("BEGIN IMMEDIATE")
        monkeypatch.setattr(provider, "_database", delayed_database)
        thread = Thread(target=worker, daemon=True)
        thread.start()
        try:
            assert waiting.wait(5), "worker did not reach canonical lock"
            clock[0] += 120 if operation == "exchange" else 60
        finally:
            lock.execute("COMMIT")
        assert finished.wait(5), "worker did not finish after lock release"
        thread.join(1)
    if operation == "exchange":
        assert not errors
        assert result[0]["state"] == "expired"
        assert retained(service) == ((None, None), 1)
    else:
        assert not result
        assert len(errors) == 1 and isinstance(errors[0], UnauthenticatedError)
        assert retained(service) == ((None, None), 0)
