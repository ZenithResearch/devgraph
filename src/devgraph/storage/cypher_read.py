"""Dedicated bounded Neo4j pool for compiler-owned read transactions."""

from __future__ import annotations

import threading
import time
from typing import Any

from devgraph.auth.enforcement import AuditLog, require_scope
from devgraph.auth.scopes import CATEGORY_READ
from devgraph.auth.verifier import CredentialVerifier
from devgraph.cypher_read import (
    RESULT_SCHEMA,
    CompiledCypherRead,
    CypherReadError,
    parse_cypher_request,
    validate_cypher_result,
)

QUERY_TIMEOUT_SECONDS = 5.0
MAX_CONCURRENT_QUERIES = 2


class Neo4jCypherReadRunner:
    def __init__(self, driver: Any, *, database: str):
        if not isinstance(database, str) or not database or database == "system":
            raise CypherReadError("cypher_backend_unavailable", 503)
        self._driver, self._database = driver, database
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_QUERIES)

    @staticmethod
    def _check_summary(summary: Any) -> None:
        if (
            getattr(summary, "query_type", None) != "r"
            or getattr(getattr(summary, "counters", None), "contains_updates", None) is not False
            or getattr(getattr(summary, "counters", None), "contains_system_updates", None)
            is not False
        ):
            raise CypherReadError("cypher_read_verification_failed", 503)

    def execute(self, compiled: CompiledCypherRead) -> dict[str, Any]:
        # The public internal entry point re-parses its opaque request snapshot:
        # constructing a dataclass manually cannot introduce raw Cypher here.
        compiled = parse_cypher_request(compiled.canonical_request)
        if not self._slots.acquire(blocking=False):
            raise CypherReadError("cypher_query_capacity_exceeded", 429)
        session = transaction = None
        try:
            session = self._driver.session(
                database=self._database, default_access_mode="READ", fetch_size=1,
            )
            transaction = session.begin_transaction(
                timeout=QUERY_TIMEOUT_SECONDS,
                metadata={"devgraph_operation": "cypher-read.v1"},
            )
            started = time.monotonic()
            # EXPLAIN does not run the statement. Both phases use the same
            # compiled text, parameters, explicit database and transaction.
            explained = transaction.run("EXPLAIN " + compiled.statement, compiled.parameters)
            self._check_summary(explained.consume())
            if time.monotonic() - started >= QUERY_TIMEOUT_SECONDS:
                raise CypherReadError("cypher_query_timeout", 504)
            result = transaction.run(compiled.statement, compiled.parameters)
            if tuple(result.keys()) != compiled.physical_columns:
                raise CypherReadError("invalid_cypher_read_result", 503)
            response = {
                "schema": RESULT_SCHEMA, "columns": list(compiled.columns),
                "rows": [], "row_count": 0, "limit": compiled.limit,
            }
            for record in result:
                if time.monotonic() - started >= QUERY_TIMEOUT_SECONDS:
                    raise CypherReadError("cypher_query_timeout", 504)
                if record["_dg_large"] is not False:
                    raise CypherReadError("cypher_result_too_large", 413)
                if record["_dg_invalid"] is not False:
                    raise CypherReadError("invalid_cypher_read_result", 503)
                response["rows"].append([
                    record[f"_dg_c{i}"] for i in range(len(compiled.columns))
                ])
                response["row_count"] += 1
                validate_cypher_result(response, compiled)
            self._check_summary(result.consume())
            if time.monotonic() - started >= QUERY_TIMEOUT_SECONDS:
                raise CypherReadError("cypher_query_timeout", 504)
            # There is deliberately no commit path, including successful reads.
            transaction.rollback()
            transaction = None
            return validate_cypher_result(response, compiled)
        except CypherReadError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", "")
            if isinstance(code, str) and (
                "TransactionTimedOut" in code or "Transaction.Terminated" in code
            ):
                raise CypherReadError("cypher_query_timeout", 504) from None
            raise CypherReadError("cypher_backend_unavailable", 503) from None
        finally:
            for resource in (transaction, session):
                if resource is not None:
                    try:
                        if resource is transaction:
                            resource.rollback()
                        resource.close()
                    except Exception:
                        pass
            self._slots.release()

    def close(self) -> None:
        self._driver.close()


class CypherReadService:
    def __init__(
        self, runner: Neo4jCypherReadRunner, *, verifier: CredentialVerifier,
        audience: str, audit_log: AuditLog,
    ):
        self._runner = runner
        self._verifier, self._audience, self._audit_log = verifier, audience, audit_log

    def execute(self, *, credential: str | None, request_json: bytes) -> dict[str, Any]:
        context = self._verifier.verify(credential, audience=self._audience)
        require_scope(context, CATEGORY_READ)
        compiled = parse_cypher_request(request_json)
        response = validate_cypher_result(self._runner.execute(compiled), compiled)
        self._audit_log.record(
            actor_id=context.actor_id, session_id=context.session_id,
            correlation_id=context.correlation_id, category=CATEGORY_READ,
            operation="cypher_read_v1", safe_summary={"row_count": response["row_count"]},
        )
        return response

    def close(self) -> None:
        self._runner.close()
