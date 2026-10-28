"""Work lock ownership follows the physical transaction, including nested errors."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest

from devgraph.storage.base import StorageUnavailable
from devgraph.storage.neo4j import Neo4jGraphStorage


class Transaction:
    def __init__(self, timeout):
        self.timeout = timeout
        self.guards = 0
        self.schemas = 0
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.fail_guard = False

    def run(self, query, **parameters):
        if query.startswith("SHOW CONSTRAINTS"):
            self.schemas += 1
            return [
                SimpleNamespace(
                    data=lambda: {
                        "type": "UNIQUENESS",
                        "labelsOrTypes": ["WorkMutationGuard"],
                        "properties": ["id"],
                    }
                )
            ]
        if query.startswith("MERGE (guard:"):
            self.guards += 1
            if self.fail_guard:
                raise RuntimeError("guard unavailable")
        return []

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def storage_fixture():
    transactions = []
    lock = Lock()

    class Session:
        def begin_transaction(self, timeout=None):
            tx = Transaction(timeout)
            with lock:
                transactions.append(tx)
            return tx

        def close(self):
            pass

    storage = object.__new__(Neo4jGraphStorage)
    storage._initialize_transaction_state()
    storage._session = Session
    return storage, transactions


def test_nested_and_successive_work_scopes_retain_one_guard_per_transaction():
    storage, transactions = storage_fixture()
    for _ in range(2):
        with storage.work_mutation_transaction():
            with storage.work_mutation_transaction():
                storage._run_graph("RETURN 1")
            with storage.work_mutation_transaction():
                pass
        assert not hasattr(storage._transaction_state, "work_guard_transaction")
    assert len(transactions) == 2
    assert all(
        (tx.schemas, tx.guards, tx.commits, tx.rollbacks, tx.timeout) == (1, 1, 1, 0, 10.0)
        for tx in transactions
    )
    assert all(tx.closed for tx in transactions)


def test_work_in_plain_transaction_acquires_and_retains_guard_until_commit():
    storage, transactions = storage_fixture()
    with storage.transaction():
        assert transactions[0].guards == 0
        with storage.work_mutation_transaction():
            pass
        with storage.work_mutation_transaction():
            pass
        assert transactions[0].commits == 0
        assert transactions[0].guards == 1
    assert transactions[0].commits == 1
    assert transactions[0].timeout is None  # No new transaction or budget reset.


def test_caught_nested_failure_still_prevents_outer_commit_and_clears_guard():
    storage, transactions = storage_fixture()
    with pytest.raises(StorageUnavailable, match="nested transaction failed"):
        with storage.work_mutation_transaction():
            try:
                with storage.work_mutation_transaction():
                    raise RuntimeError("mutation failed")
            except RuntimeError:
                pass
    assert (transactions[0].commits, transactions[0].rollbacks) == (0, 1)
    assert not hasattr(storage._transaction_state, "work_guard_transaction")
    with storage.work_mutation_transaction():
        pass
    assert transactions[1].guards == 1


def test_failed_acquisition_never_confers_guard_ownership():
    storage, transactions = storage_fixture()
    with pytest.raises(StorageUnavailable, match="nested transaction failed"):
        with storage.transaction():
            transactions[0].fail_guard = True
            for _ in range(2):
                with pytest.raises(RuntimeError, match="guard unavailable"):
                    with storage.work_mutation_transaction():
                        pass
                assert not hasattr(storage._transaction_state, "work_guard_transaction")
    assert (transactions[0].guards, transactions[0].commits, transactions[0].rollbacks) == (2, 0, 1)


def test_guard_ownership_is_separate_across_worker_threads():
    storage, transactions = storage_fixture()
    barrier = Barrier(2)

    def run():
        with storage.work_mutation_transaction():
            barrier.wait(timeout=5)
            with storage.work_mutation_transaction():
                pass
        assert not hasattr(storage._transaction_state, "work_guard_transaction")

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: run(), range(2)))
    assert len(transactions) == 2
    assert all((tx.guards, tx.commits) == (1, 1) for tx in transactions)


def test_failure_diagnostics_are_bounded_and_exclude_query_parameters_and_error(caplog):
    storage, _ = storage_fixture()
    with pytest.raises(RuntimeError):
        with storage.work_mutation_transaction():
            with storage.work_mutation_transaction():
                storage._run_graph("RETURN 'private-query-marker'", key="private-key-marker")
                raise RuntimeError("private-error-marker")
    records = [r for r in caplog.records if r.message.startswith("named_work_transaction_failed ")]
    assert len(records) == 1
    assert "private-" not in records[0].message
    diagnostic = json.loads(records[0].message.split(" ", 1)[1])
    assert set(diagnostic) == {"work_scope_ms", "queries"}
    assert set(diagnostic["queries"]) == {"guard_schema", "guard_lock", "graph"}
    assert all(
        set(entry) == {"count", "elapsed_ms"} and entry["count"] == 1 and entry["elapsed_ms"] >= 0
        for entry in diagnostic["queries"].values()
    )
    assert diagnostic["work_scope_ms"] >= 0
    assert not hasattr(storage._transaction_state, "work_timings")


def test_success_does_not_log_diagnostics_and_each_scope_starts_fresh(caplog):
    storage, _ = storage_fixture()
    for _ in range(2):
        with storage.work_mutation_transaction():
            assert set(storage._transaction_state.work_timings) == {"guard_schema", "guard_lock"}
        assert not hasattr(storage._transaction_state, "work_timings")
    assert not caplog.records
