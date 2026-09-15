from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from devgraph.cypher_read import REQUEST_SCHEMA, CypherReadError, parse_cypher_request
from devgraph.storage.cypher_read import Neo4jCypherReadRunner


def compiled():
    return parse_cypher_request(json.dumps({
        "schema": REQUEST_SCHEMA, "query": "MATCH (n:Issue) RETURN n.id AS id LIMIT 2",
        "parameters": {},
    }).encode())


def summary(query_type="r", updates=False, system_updates=False):
    return SimpleNamespace(
        query_type=query_type,
        counters=SimpleNamespace(contains_updates=updates, contains_system_updates=system_updates),
    )


class Result:
    def __init__(self, rows=None, *, result_summary=None, keys=None):
        self.rows = rows or []
        self.result_summary = result_summary or summary()
        self._keys = keys or compiled().physical_columns

    def keys(self):
        return self._keys

    def __iter__(self):
        return iter(self.rows)

    def consume(self):
        return self.result_summary


def runner(*, explain=None, result=None):
    transaction = Mock()
    transaction.run.side_effect = [explain or Result(), result or Result([
        {"_dg_c0": "issue-1", "_dg_invalid": False, "_dg_large": False},
    ])]
    session = Mock()
    session.begin_transaction.return_value = transaction
    driver = Mock()
    driver.session.return_value = session
    return Neo4jCypherReadRunner(driver, database="neo4j"), transaction, session, driver


def test_success_recompiles_then_explains_executes_and_rolls_back():
    read, transaction, session, driver = runner()
    parsed = compiled()
    # Even an internally forged compiled statement is never executed.
    result = read.execute(replace(parsed, statement="MATCH (n) DETACH DELETE n"))
    assert result["rows"] == [["issue-1"]]
    driver.session.assert_called_once_with(
        database="neo4j", default_access_mode="READ", fetch_size=1,
    )
    session.begin_transaction.assert_called_once_with(
        timeout=5.0, metadata={"devgraph_operation": "cypher-read.v1"},
    )
    assert transaction.run.call_args_list[0].args == (
        "EXPLAIN " + parsed.statement, parsed.parameters,
    )
    assert transaction.run.call_args_list[1].args == (parsed.statement, parsed.parameters)
    transaction.commit.assert_not_called()
    transaction.rollback.assert_called_once()
    session.close.assert_called_once()


@pytest.mark.parametrize("bad_summary", [
    summary("rw"), summary("w"), summary("s"), summary(None), summary("r", True),
    summary("r", False, True), SimpleNamespace(query_type="r"),
])
def test_bad_explain_never_executes(bad_summary):
    read, transaction, session, _ = runner(explain=Result(result_summary=bad_summary))
    with pytest.raises(CypherReadError, match="verification_failed"):
        read.execute(compiled())
    assert transaction.run.call_count == 1
    transaction.rollback.assert_called_once()
    transaction.commit.assert_not_called()
    session.close.assert_called_once()


def test_unexpected_execution_update_rolls_back_without_response():
    read, transaction, _, _ = runner(result=Result(result_summary=summary("rw", True)))
    with pytest.raises(CypherReadError, match="verification_failed"):
        read.execute(compiled())
    transaction.rollback.assert_called_once()
    transaction.commit.assert_not_called()


@pytest.mark.parametrize("result", [
    Result(keys=["secret"]),
    Result([{ "_dg_c0": None, "_dg_invalid": True, "_dg_large": False}]),
    Result([{ "_dg_c0": None, "_dg_invalid": False, "_dg_large": True}]),
    Result([{ "_dg_c0": {"secret": "oops"}, "_dg_invalid": False, "_dg_large": False}]),
    Result([{ "_dg_c0": "x", "_dg_invalid": False, "_dg_large": False}] * 3),
])
def test_result_shape_limits_and_server_flags(result):
    read, transaction, _, _ = runner(result=result)
    with pytest.raises(CypherReadError):
        read.execute(compiled())
    transaction.rollback.assert_called_once()
    transaction.commit.assert_not_called()


def test_driver_error_never_escapes_and_slot_is_released():
    read, transaction, _, _ = runner()
    transaction.run.side_effect = RuntimeError("password and query secret")
    with pytest.raises(CypherReadError) as error:
        read.execute(compiled())
    assert str(error.value) == "cypher_backend_unavailable"
    assert read._slots.acquire(blocking=False)
    assert read._slots.acquire(blocking=False)
    transaction.rollback.assert_called_once()


def test_capacity_rejects_before_opening_a_session():
    read, _, _, driver = runner()
    read._slots.acquire()
    read._slots.acquire()
    with pytest.raises(CypherReadError) as error:
        read.execute(compiled())
    assert error.value.status == 429
    driver.session.assert_not_called()


def test_deadline_during_explain_does_not_execute(monkeypatch):
    read, transaction, _, _ = runner()
    clock = iter([0, 6])
    monkeypatch.setattr("devgraph.storage.cypher_read.time.monotonic", lambda: next(clock))
    with pytest.raises(CypherReadError) as error:
        read.execute(compiled())
    assert error.value.status == 504
    assert transaction.run.call_count == 1
    transaction.rollback.assert_called_once()


def test_database_is_explicit_and_cannot_be_system():
    with pytest.raises(CypherReadError):
        Neo4jCypherReadRunner(Mock(), database="system")
