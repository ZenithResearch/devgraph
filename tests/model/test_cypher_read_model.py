from __future__ import annotations

import json

import pytest

from devgraph.cypher_read import (
    MAX_REQUEST_BYTES,
    PUBLIC_KINDS,
    REQUEST_SCHEMA,
    RESULT_SCHEMA,
    CypherReadError,
    parse_cypher_request,
    validate_cypher_result,
)


def request(query="MATCH (n:Issue) RETURN n.id AS id LIMIT 10", parameters=None):
    return json.dumps({
        "schema": REQUEST_SCHEMA, "query": query,
        "parameters": {} if parameters is None else parameters,
    }).encode()


@pytest.mark.parametrize("kind", sorted(PUBLIC_KINDS))
def test_public_kind_projects_only_a_whitelisted_property(kind):
    compiled = parse_cypher_request(request(f"MATCH (n:{kind}) RETURN n.id AS id LIMIT 10"))
    assert compiled.columns == ("id",)
    assert compiled.limit == 10
    assert compiled.types == (str,)
    assert "size(labels(_dg_n0)) = 1" in compiled.statement
    assert "_dg_n0.kind = $_dg_p0" in compiled.statement
    assert compiled.parameters["_dg_p0"] == kind
    assert "CASE WHEN (_dg_n0.`id` IS :: STRING NOT NULL)" in compiled.statement
    assert "properties(" not in compiled.statement


def test_query_values_are_parameters_not_syntax_or_generated_names():
    attack = "x') DELETE n // secret-param"
    compiled = parse_cypher_request(request(
        "MATCH (other:Issue) WHERE other.title = $title "
        "RETURN other.id AS ident, other.priority AS priority ORDER BY priority DESC LIMIT 3",
        {"title": attack},
    ))
    assert attack not in compiled.statement
    assert "$title" not in compiled.statement and "other" not in compiled.statement
    assert attack in compiled.parameters.values()
    assert attack not in repr(compiled)
    assert "ORDER BY _dg_c1 DESC" in compiled.statement


@pytest.mark.parametrize("arrow,expected", [
    ("-[:HAS_CHILD]->", "-[:`HAS_CHILD`]->"),
    ("<-[:DEPENDS_ON]-", "<-[:`DEPENDS_ON`]-"),
    ("-[:BLOCKS]->", "-[:`BLOCKS`]->"),
])
def test_one_directed_typed_hop(arrow, expected):
    compiled = parse_cypher_request(request(
        f"MATCH (a:Task){arrow}(b:Task) WHERE a.id = $id "
        "RETURN a.id AS source, b.title AS target ORDER BY target LIMIT 100", {"id": "a"},
    ))
    assert expected in compiled.statement
    assert "size(labels(_dg_n1)) = 1" in compiled.statement
    assert compiled.columns == ("source", "target")


@pytest.mark.parametrize("operator", ["=", "<>", "<", "<=", ">", ">=", "CONTAINS",
                                      "STARTS WITH", "ENDS WITH"])
def test_string_predicates(operator):
    parse_cypher_request(request(
        f"MATCH (n:Issue) WHERE n.title {operator} $value RETURN n.id AS id LIMIT 1",
        {"value": "hello"},
    ))


def test_count_and_case_insensitive_keywords():
    compiled = parse_cypher_request(request(
        "match (n:Task) where n.archived = $archived and n.priority >= $minimum "
        "return count(*) as total order by total desc limit 1",
        {"archived": False, "minimum": 0},
    ))
    assert "count(*) AS _dg_c0" in compiled.statement
    assert compiled.types == (int,)
    assert "false" in compiled.statement


@pytest.mark.parametrize("query", [
    "MATCH (n) RETURN n.id AS id LIMIT 1",
    "MATCH (n:EventReceipt) RETURN n.id AS id LIMIT 1",
    "MATCH (n:WorkMutationGuard) RETURN n.id AS id LIMIT 1",
    "MATCH (n:Decision) RETURN n.id AS id LIMIT 1",
    "MATCH (n:Todo) RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue:EventReceipt) RETURN n.id AS id LIMIT 1",
    "MATCH (n:`Issue`) RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue) RETURN n LIMIT 1",
    "MATCH (n:Issue) RETURN properties(n) AS props LIMIT 1",
    "MATCH (n:Issue) RETURN labels(n) AS labels LIMIT 1",
    "MATCH (n:Issue) RETURN n.__devgraph_create_nonce AS nonce LIMIT 1",
    "MATCH (n:Issue) RETURN n.credential AS credential LIMIT 1",
    "MATCH (n:Issue) RETURN n['id'] AS id LIMIT 1",
    "MATCH (n:Issue) RETURN n.artifact_ids AS artifacts LIMIT 1",
    "MATCH (n:Issue) RETURN n.id AS id LIMIT 1; CREATE (:Issue)",
    "MATCH (n:Issue) RETURN n.id AS id LIMIT 1 // ignored",
    "MATCH (n:Issue) /* allowed? */ RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue) SET n.title = $title RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue) DELETE n RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue) DETACH DELETE n",
    "MATCH (n:Issue) RETURN n.id AS id UNION MATCH (m:Issue) RETURN m.id AS id LIMIT 1",
    "CALL dbms.listConfig() YIELD name RETURN name AS name LIMIT 1",
    "CALL { MATCH (n:Issue) RETURN n } RETURN n.id AS id LIMIT 1",
    "LOAD CSV FROM $url AS line RETURN line AS line LIMIT 1",
    "USE system MATCH (n:Issue) RETURN n.id AS id LIMIT 1",
    "EXPLAIN MATCH (n:Issue) RETURN n.id AS id LIMIT 1",
    "PROFILE MATCH (n:Issue) RETURN n.id AS id LIMIT 1",
    "CYPHER runtime=slotted MATCH (n:Issue) RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue), (m:Task) RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue)-[:HAS_CHILD*]->(m:Task) RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue)-[:HAS_CHILD]->(m:Task)-[:BLOCKS]->(t:Task) RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue)-[r:HAS_CHILD]->(m:Task) RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue)-[:HAS_CHILD]-(m:Task) RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue)-[:HAS_SECRET]->(m:Task) RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue)-[:HAS_CHILD]->(n:Task) RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue) WHERE n.id = 'literal' RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue) WHERE n.id = $id OR n.id = $id RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue) WHERE EXISTS { MATCH (n)-->() } RETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue) RETURN n.id AS id, n.title AS id LIMIT 1",
    "MATCH (n:Issue) RETURN n.id AS id, count(*) AS total LIMIT 1",
    "MATCH (n:Issue) RETURN count(*) AS total, n.id AS id LIMIT 1",
    "MATCH (n:Issue) RETURN n.id AS id ORDER BY n.title LIMIT 1",
    "MATCH (n:Issue) RETURN n.id AS id ORDER BY other LIMIT 1",
    "MATCH (n:Issue) RETURN n.id AS id",
    "MATCH (n:Issue) RETURN n.id AS id LIMIT 0",
    "MATCH (n:Issue) RETURN n.id AS id LIMIT 101",
    "MATCH (n:Issue) RETURN n.id AS id LIMIT -1",
    "MATCH (n:Issue) RETURN n.id AS id LIMIT $limit",
    "MATCH (n:Issue) RETURN n.id AS id LIMIT true",
    "MATCH (n:Issue) RETURN n.id AS id LIMIT 1 SKIP 1",
    "MATCH (n:Issue) RETURN n.id AS id LIMIT 1\x00",
    "MATCH (n:Issue)\u200bRETURN n.id AS id LIMIT 1",
    "MATCH (n:Issue) RETURN n.id AS id LIMIT 1;",
])
def test_unsupported_syntax_is_rejected(query):
    with pytest.raises(CypherReadError):
        parse_cypher_request(request(query))


@pytest.mark.parametrize("parameters", [
    {"id": []}, {"id": {}}, {"id": None}, {"id": 1.0}, {"id": True},
    {"id": "ok", "unused": "extra"}, {}, {"id": "x" * 8193},
])
def test_parameter_shapes_types_and_exact_bindings(parameters):
    with pytest.raises(CypherReadError):
        parse_cypher_request(request(
            "MATCH (n:Issue) WHERE n.id = $id RETURN n.id AS id LIMIT 1", parameters,
        ))


@pytest.mark.parametrize("raw", [
    b'{"schema":"devgraph.cypher-read-request.v1","query":"x","query":"y","parameters":{}}',
    b'{"schema":"devgraph.cypher-read-request.v1","query":"x","parameters":{"x":1,"x":2}}',
    b'{"schema":"devgraph.cypher-read-request.v1","query":"x","parameters":{"x":NaN}}',
    b'{"schema":"devgraph.cypher-read-request.v1","query":"x","parameters":{"x":Infinity}}',
    b'{"schema":"devgraph.cypher-read-request.v1","query":"x","parameters":{"x":"\\ud800"}}',
    b'[' * 10000 + b']' * 10000,
    b'{"schema":"future","query":"x","parameters":{}}',
    b'null', b'[]', b'"text"', b'{}', b'\xff', b'',
    b' ' * (MAX_REQUEST_BYTES + 1),
])
def test_strict_bounded_json(raw):
    with pytest.raises(CypherReadError):
        parse_cypher_request(raw)


def test_nested_syntax_in_string_parameter_does_not_count_as_json_structure():
    parse_cypher_request(request(
        "MATCH (n:Issue) WHERE n.title = $title RETURN n.id AS id LIMIT 1",
        {"title": '{"quoted": [[[[[]]]]]} \\"'},
    ))


def test_term_and_parameter_limits():
    with pytest.raises(CypherReadError):
        parse_cypher_request(request(
            "MATCH (n:Issue) RETURN "
            + ", ".join(f"n.id AS id{i}" for i in range(17)) + " LIMIT 1",
        ))
    with pytest.raises(CypherReadError):
        parse_cypher_request(request(
            "MATCH (n:Issue) WHERE " + " AND ".join("n.id = $id" for _ in range(17))
            + " RETURN n.id AS id LIMIT 1", {"id": "id"},
        ))
    with pytest.raises(CypherReadError):
        parse_cypher_request(request(parameters={f"p{i}": "x" for i in range(33)}))


def test_typed_response_and_result_bounds():
    compiled = parse_cypher_request(request())
    response = {
        "schema": RESULT_SCHEMA, "columns": ["id"], "rows": [["x"]],
        "row_count": 1, "limit": 10,
    }
    assert validate_cypher_result(response, compiled) == response
    for change in [
        {"rows": [[None]]}, {"rows": [[{}]]}, {"rows": [[1]]}, {"rows": [["x", "y"]]},
        {"columns": ["secret"]}, {"row_count": True}, {"limit": True}, {"extra": "x"},
        {"rows": [["x" * 16385]]}, {"rows": [["x"]] * 11, "row_count": 11},
    ]:
        with pytest.raises(CypherReadError):
            validate_cypher_result({**response, **change}, compiled)


def test_encoded_output_budget_accounts_for_unicode_bytes():
    compiled = parse_cypher_request(request(
        "MATCH (n:Issue) RETURN n.description AS text LIMIT 10",
    ))
    response = {
        "schema": RESULT_SCHEMA, "columns": ["text"],
        "rows": [["\U0001f600" * 16384]] * 4, "row_count": 4, "limit": 10,
    }
    with pytest.raises(CypherReadError) as error:
        validate_cypher_result(response, compiled)
    assert error.value.status == 413
