"""Closed Cypher read v1: parse a small language, then compile fresh read-only Cypher.

Submitted query text never reaches Neo4j. This compiler is the authorization
boundary for Community Edition; driver READ_ACCESS is only defense in depth.
The grammar has no recursion, expressions, procedures, arbitrary identifiers,
unlabelled patterns, dynamic properties, or generic node/map projections.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

REQUEST_SCHEMA = "devgraph.cypher-read-request.v1"
RESULT_SCHEMA = "devgraph.cypher-read-result.v1"
MAX_REQUEST_BYTES = 32_768
MAX_QUERY_BYTES = 8_192
MAX_RESULT_BYTES = 262_144
MAX_STRING_CHARACTERS = 16_384
MAX_PARAMETERS = 32
MAX_TERMS = 16
MAX_ROWS = 100
MAX_INTEGER = 9_223_372_036_854_775_807
MIN_INTEGER = -9_223_372_036_854_775_808
PUBLIC_KINDS = frozenset({"Proposal", "Initiative", "Project", "Issue", "Task"})
PUBLIC_RELATIONSHIPS = frozenset({"HAS_CHILD", "DEPENDS_ON", "BLOCKS"})
PROPERTY_TYPES = {
    "id": str, "kind": str, "title": str, "description": str, "status": str,
    "created_at": str, "updated_at": str, "version": int, "priority": int,
    "archived": bool,
}
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}\Z", re.ASCII)
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*|[0-9]+|->|<-|<=|>=|<>|[():,.\[\]*$=<>-]")
_KEYWORDS = frozenset({
    "MATCH", "WHERE", "AND", "RETURN", "AS", "ORDER", "BY", "ASC", "DESC", "LIMIT",
    "COUNT", "CONTAINS", "STARTS", "ENDS", "WITH",
})


class CypherReadError(RuntimeError):
    """A fixed redaction-safe failure, never carrying user input or a DB error."""

    def __init__(self, code: str = "unsupported_cypher_read", status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for name, value in pairs:
        if name in result:
            raise CypherReadError("invalid_cypher_read_request")
        result[name] = value
    return result


def _invalid_number(value: str) -> None:
    raise CypherReadError("invalid_cypher_read_request")


def _strict_json(raw: bytes, *, maximum: int, depth: int) -> Any:
    if type(raw) is not bytes or not raw or len(raw) > maximum:
        raise CypherReadError("cypher_request_too_large", 413)
    # Bound structure before invoking the recursive JSON parser. Braces in
    # strings are data; escaped quotes do not change the string state.
    level, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            level += 1
            if level > depth:
                raise CypherReadError("invalid_cypher_read_request")
        elif byte in (93, 125):
            level -= 1
    try:
        result = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=_invalid_number, parse_float=_invalid_number,
        )
        # Also rejects lone surrogate escapes accepted by json.loads.
        _json_bytes(result)
        return result
    except (UnicodeError, ValueError, RecursionError):
        raise CypherReadError("invalid_cypher_read_request") from None


@dataclass(frozen=True)
class CompiledCypherRead:
    statement: str
    parameters: dict[str, str | int | bool] = field(repr=False)
    columns: tuple[str, ...]
    types: tuple[type, ...]
    limit: int
    canonical_request: bytes = field(repr=False)

    @property
    def physical_columns(self) -> tuple[str, ...]:
        return (*[f"_dg_c{i}" for i in range(len(self.columns))], "_dg_invalid", "_dg_large")


class _Parser:
    def __init__(self, query: str, parameters: dict[str, Any]):
        if (
            not query or not query.isascii() or len(query) > MAX_QUERY_BYTES
            or any(ord(char) < 32 and char not in "\t\r\n" for char in query)
        ):
            raise CypherReadError()
        self.tokens = []
        offset = 0
        while offset < len(query):
            if query[offset] in " \t\r\n":
                offset += 1
                continue
            found = _TOKEN.match(query, offset)
            if found is None or len(self.tokens) >= 512:
                raise CypherReadError()
            self.tokens.append(found.group())
            offset = found.end()
        self.offset = 0
        self.input_parameters = parameters
        self.used_parameters: set[str] = set()
        self.parameters: dict[str, str | int | bool] = {}
        self.nodes: dict[str, tuple[str, str]] = {}

    def peek(self, token: str) -> bool:
        return self.offset < len(self.tokens) and self.tokens[self.offset].upper() == token

    def take(self, token: str) -> None:
        if not self.peek(token):
            raise CypherReadError()
        self.offset += 1

    def name(self, *, keyword: bool = False) -> str:
        if self.offset >= len(self.tokens):
            raise CypherReadError()
        value = self.tokens[self.offset]
        if _NAME.fullmatch(value) is None or (not keyword and value.upper() in _KEYWORDS):
            raise CypherReadError()
        self.offset += 1
        return value

    def bind(self, value: str | int | bool) -> str:
        name = f"_dg_p{len(self.parameters)}"
        self.parameters[name] = value
        return f"${name}"

    def node(self) -> str:
        self.take("(")
        name = self.name()
        self.take(":")
        kind = self.name()
        self.take(")")
        if kind not in PUBLIC_KINDS or name in self.nodes:
            raise CypherReadError()
        generated = f"_dg_n{len(self.nodes)}"
        self.nodes[name] = (generated, kind)
        return f"({generated}:`{kind}`)"

    def property(self) -> tuple[str, type]:
        node = self.name()
        self.take(".")
        prop = self.name(keyword=True)
        if node not in self.nodes or prop not in PROPERTY_TYPES:
            raise CypherReadError()
        return f"{self.nodes[node][0]}.`{prop}`", PROPERTY_TYPES[prop]

    def predicate(self) -> str:
        expression, expected_type = self.property()
        if self.offset >= len(self.tokens):
            raise CypherReadError()
        operator = self.tokens[self.offset].upper()
        self.offset += 1
        if operator in {"STARTS", "ENDS"}:
            self.take("WITH")
            operator += " WITH"
        if operator not in {"=", "<>", "<", "<=", ">", ">=", "CONTAINS", "STARTS WITH",
                            "ENDS WITH"}:
            raise CypherReadError()
        if expected_type is bool and operator not in {"=", "<>"}:
            raise CypherReadError()
        if operator in {"CONTAINS", "STARTS WITH", "ENDS WITH"} and expected_type is not str:
            raise CypherReadError()
        self.take("$")
        name = self.name()
        if (
            name not in self.input_parameters
            or type(self.input_parameters[name]) is not expected_type
        ):
            raise CypherReadError("invalid_cypher_read_parameters")
        self.used_parameters.add(name)
        return f"{expression} {operator} {self.bind(self.input_parameters[name])}"

    def compile(self, canonical: bytes) -> CompiledCypherRead:
        self.take("MATCH")
        pattern = self.node()
        if self.peek("-") or self.peek("<-"):
            incoming = self.peek("<-")
            self.take("<-" if incoming else "-")
            self.take("[")
            self.take(":")
            relation = self.name()
            self.take("]")
            self.take("-" if incoming else "->")
            if relation not in PUBLIC_RELATIONSHIPS:
                raise CypherReadError()
            target = self.node()
            pattern += (
                f"<-[:`{relation}`]-" if incoming else f"-[:`{relation}`]->"
            ) + target
        predicates = []
        if self.peek("WHERE"):
            self.take("WHERE")
            while True:
                if len(predicates) == MAX_TERMS:
                    raise CypherReadError()
                predicates.append(self.predicate())
                if not self.peek("AND"):
                    break
                self.take("AND")
        for generated, kind in self.nodes.values():
            predicates.extend([
                f"size(labels({generated})) = 1", f"{generated}.kind = {self.bind(kind)}",
            ])
        if self.used_parameters != set(self.input_parameters):
            raise CypherReadError("invalid_cypher_read_parameters")

        self.take("RETURN")
        columns, types, projections, invalid, large = [], [], [], [], []
        aggregate = self.peek("COUNT")
        while True:
            if len(columns) == MAX_TERMS:
                raise CypherReadError()
            if aggregate:
                self.take("COUNT")
                self.take("(")
                self.take("*")
                self.take(")")
                expression, expected_type = "count(*)", int
            else:
                expression, expected_type = self.property()
            self.take("AS")
            column = self.name()
            if column in columns:
                raise CypherReadError()
            columns.append(column)
            types.append(expected_type)
            type_name = {str: "STRING", int: "INTEGER", bool: "BOOLEAN"}[expected_type]
            valid_type = f"({expression} IS :: {type_name} NOT NULL)"
            if not aggregate:
                invalid.append(f"NOT {valid_type}")
            if expected_type is str:
                bound = self.bind(MAX_STRING_CHARACTERS)
                large.append(
                    f"CASE WHEN {valid_type} THEN size({expression}) > {bound} ELSE false END"
                )
                # Bound each value before Bolt materializes it; malformed list
                # properties never cross the wire in a nominal string column.
                projected = (
                    f"CASE WHEN {valid_type} THEN CASE WHEN size({expression}) <= {bound} "
                    f"THEN {expression} ELSE null END ELSE null END"
                )
            else:
                projected = expression if aggregate else (
                    f"CASE WHEN {valid_type} THEN {expression} ELSE null END"
                )
            projections.append(f"{projected} AS _dg_c{len(columns) - 1}")
            if aggregate or not self.peek(","):
                break
            self.take(",")

        order = []
        if self.peek("ORDER"):
            self.take("ORDER")
            self.take("BY")
            while True:
                name = self.name()
                if name not in columns or len(order) == MAX_TERMS:
                    raise CypherReadError()
                direction = "ASC"
                if self.peek("ASC") or self.peek("DESC"):
                    direction = self.tokens[self.offset].upper()
                    self.offset += 1
                order.append(f"_dg_c{columns.index(name)} {direction}")
                if not self.peek(","):
                    break
                self.take(",")
        self.take("LIMIT")
        if self.offset >= len(self.tokens):
            raise CypherReadError()
        number = self.tokens[self.offset]
        if not number.isascii() or not number.isdigit() or len(number) > 3:
            raise CypherReadError()
        limit = int(number)
        self.offset += 1
        if not 1 <= limit <= MAX_ROWS or self.offset != len(self.tokens):
            raise CypherReadError()
        projections.extend([
            f"({' OR '.join(invalid) if invalid else 'false'}) AS _dg_invalid",
            f"({' OR '.join(large) if large else 'false'}) AS _dg_large",
        ])
        statement = (
            f"MATCH {pattern} WHERE " + " AND ".join(f"({part})" for part in predicates)
            + " RETURN " + ", ".join(projections)
            + (" ORDER BY " + ", ".join(order) if order else "")
            + f" LIMIT {self.bind(limit)}"
        )
        return CompiledCypherRead(
            statement, self.parameters, tuple(columns), tuple(types), limit, canonical,
        )


def parse_cypher_request(raw: bytes) -> CompiledCypherRead:
    value = _strict_json(raw, maximum=MAX_REQUEST_BYTES, depth=2)
    if (
        type(value) is not dict or set(value) != {"schema", "query", "parameters"}
        or value["schema"] != REQUEST_SCHEMA or type(value["query"]) is not str
        or type(value["parameters"]) is not dict or len(value["parameters"]) > MAX_PARAMETERS
    ):
        raise CypherReadError("invalid_cypher_read_request")
    for name, parameter in value["parameters"].items():
        if _NAME.fullmatch(name) is None or type(parameter) not in {str, bool, int}:
            raise CypherReadError("invalid_cypher_read_parameters")
        if type(parameter) is int and not MIN_INTEGER <= parameter <= MAX_INTEGER:
            raise CypherReadError("invalid_cypher_read_parameters")
        if type(parameter) is str and len(parameter.encode("utf-8")) > MAX_QUERY_BYTES:
            raise CypherReadError("invalid_cypher_read_parameters")
    return _Parser(value["query"], value["parameters"]).compile(_json_bytes(value))


def validate_cypher_result(value: Any, compiled: CompiledCypherRead) -> dict[str, Any]:
    """Validate the response at both server and client boundaries without coercion."""
    if (
        type(value) is not dict
        or set(value) != {"schema", "columns", "rows", "row_count", "limit"}
        or value["schema"] != RESULT_SCHEMA or value["columns"] != list(compiled.columns)
        or type(value["rows"]) is not list or type(value["limit"]) is not int
        or value["limit"] != compiled.limit or type(value["row_count"]) is not int
        or value["row_count"] != len(value["rows"]) or len(value["rows"]) > compiled.limit
    ):
        raise CypherReadError("invalid_cypher_read_result", 503)
    for row in value["rows"]:
        if type(row) is not list or len(row) != len(compiled.columns):
            raise CypherReadError("invalid_cypher_read_result", 503)
        for item, expected_type in zip(row, compiled.types, strict=True):
            if type(item) is not expected_type:
                raise CypherReadError("invalid_cypher_read_result", 503)
            if type(item) is int and not MIN_INTEGER <= item <= MAX_INTEGER:
                raise CypherReadError("invalid_cypher_read_result", 503)
            if type(item) is str and len(item) > MAX_STRING_CHARACTERS:
                raise CypherReadError("cypher_result_too_large", 413)
    try:
        if len(_json_bytes(value)) > MAX_RESULT_BYTES:
            raise CypherReadError("cypher_result_too_large", 413)
    except (ValueError, UnicodeError):
        raise CypherReadError("invalid_cypher_read_result", 503) from None
    return value
