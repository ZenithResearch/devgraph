from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import local
from typing import Any, Literal
from uuid import uuid4

from devgraph.model.validation import (
    CANONICAL_MODEL_PROPERTIES,
    CANONICAL_WORK_KINDS,
    validate_canonical_work_object_properties,
    validate_page_limit,
    validate_version,
    validate_work_object_id,
)
from devgraph.storage.base import (
    BootstrapConstraint,
    EdgeRecord,
    EventReceiptClaim,
    HealthStatus,
    MigrationJournal,
    MigrationOwner,
    NodeRecord,
    SchemaObject,
    StorageUnavailable,
    WorkContainment,
)
from devgraph.storage.containment import validate_arena_page, validate_containment_subject
from devgraph.storage.supporting_material import (
    MAX_PROVENANCE,
    SUPPORTING_KINDS,
    SUPPORTING_PROPERTIES,
    SupportingReference,
    validate_supporting_keys,
    validate_supporting_read,
)

try:  # Optional dependency: required only for canonical Neo4j runtime/smoke tests.
    from neo4j import GraphDatabase
    from neo4j.exceptions import Neo4jError, ServiceUnavailable
except Exception:  # pragma: no cover - exercised by environments without neo4j installed.
    GraphDatabase = None  # type: ignore[assignment]
    Neo4jError = Exception  # type: ignore[assignment]
    ServiceUnavailable = Exception  # type: ignore[assignment]


_NODE_LABEL_PATTERN = re.compile(r"[A-Z][A-Za-z0-9]{0,127}")
_RELATIONSHIP_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,127}")
_GENERIC_JSON_PREFIX = "__devgraph_generic_json_v1__:"
_GENERIC_ENCODING_PROPERTY = "__devgraph_generic_encoding"
_GENERIC_ENCODING_VALUE = "json-v1"
_CREATE_NONCE_PROPERTY = "__devgraph_create_nonce"
_EDGE_IDENTITY_PROPERTY = "__devgraph_edge_identity"


def _containment_projection() -> str:
    # Bound before collect, separately per relationship and per selected node.
    # DISTINCT would hide duplicate edges, which must remain ambiguity witnesses.
    branches = []
    for relationship, field in (("HAS_CHILD", "parents"), ("CONTAINS_WORK", "memberships")):
        branches.append(
            f"CALL {{ WITH n MATCH (source)-[edge:{relationship}]->(n) "
            "WITH n, source, edge LIMIT 2 "
            "RETURN collect({from_labels: labels(source), from_id: source.id, "
            "relationship: type(edge), to_labels: labels(n), to_id: n.id, "
            f"properties: properties(edge)}}) AS {field} }} "
        )
    return "".join(branches) + (
        "RETURN labels(n) AS labels, n.id AS id, n.archived AS archived, "
        "properties(n) AS properties, parents, memberships "
        "ORDER BY labels(n)[0], n.id"
    )


def neo4j_available() -> bool:
    return GraphDatabase is not None


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str
    user: str
    password: str
    database: str | None = None

    @classmethod
    def from_env(cls) -> Neo4jConfig:
        uri = os.environ.get("NEO4J_URI")
        user = os.environ.get("NEO4J_USER")
        password = os.environ.get("NEO4J_PASSWORD")
        password_file = os.environ.get("NEO4J_PASSWORD_FILE")
        if password is None and password_file:
            password = open(password_file, encoding="utf-8").read().strip()
        missing = [
            name
            for name, value in {
                "NEO4J_URI": uri,
                "NEO4J_USER": user,
                "NEO4J_PASSWORD or NEO4J_PASSWORD_FILE": password,
            }.items()
            if not value
        ]
        if missing:
            raise StorageUnavailable(f"missing Neo4j config: {', '.join(missing)}")
        return cls(
            uri=uri or "",
            user=user or "",
            password=password or "",
            database=os.environ.get("NEO4J_DATABASE"),
        )


class Neo4jGraphStorage:
    """Canonical parameterized Neo4j graph-storage adapter."""

    def __init__(self, config: Neo4jConfig) -> None:
        if GraphDatabase is None:
            raise StorageUnavailable("neo4j Python driver is not installed")
        self._config = config
        self._driver = GraphDatabase.driver(config.uri, auth=(config.user, config.password))
        self._initialize_transaction_state()

    def _initialize_transaction_state(self) -> None:
        self._transaction_state = local()

    @staticmethod
    def _node_label(label: object) -> str:
        if not isinstance(label, str) or _NODE_LABEL_PATTERN.fullmatch(label) is None:
            raise StorageUnavailable("invalid node label")
        return label

    @staticmethod
    def _is_work_label(label: str) -> bool:
        return label in CANONICAL_WORK_KINDS

    @classmethod
    def _is_canonical_payload(cls, label: str, properties: object) -> bool:
        return cls._is_work_label(label) and isinstance(properties, dict) and "kind" in properties

    @staticmethod
    def _validate_generic_json_value(value: object) -> None:
        if value is None or type(value) in {str, bool, int, float}:
            return
        if isinstance(value, list):
            for item in value:
                Neo4jGraphStorage._validate_generic_json_value(item)
            return
        if isinstance(value, dict) and all(isinstance(key, str) for key in value):
            for item in value.values():
                Neo4jGraphStorage._validate_generic_json_value(item)
            return
        raise TypeError("generic property is not JSON-compatible")

    @classmethod
    def _generic_properties(cls, properties: object) -> dict[str, Any]:
        if properties is None:
            return {}
        if (
            not isinstance(properties, dict)
            or not all(isinstance(key, str) for key in properties)
            or set(properties).intersection(
                {
                    "id",
                    "archived",
                    _GENERIC_ENCODING_PROPERTY,
                    _CREATE_NONCE_PROPERTY,
                }
            )
        ):
            raise StorageUnavailable("invalid generic node properties")
        try:
            encoded: dict[str, str] = {}
            for key, value in properties.items():
                cls._validate_generic_json_value(value)
                encoded[key] = _GENERIC_JSON_PREFIX + json.dumps(
                    value,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            encoded[_GENERIC_ENCODING_PROPERTY] = _GENERIC_ENCODING_VALUE
            return encoded
        except (TypeError, ValueError) as exc:
            raise StorageUnavailable("invalid generic node properties") from exc

    @staticmethod
    def _decode_generic_property(value: object) -> object:
        if not isinstance(value, str) or not value.startswith(_GENERIC_JSON_PREFIX):
            return value
        try:
            return json.loads(value[len(_GENERIC_JSON_PREFIX) :])
        except (TypeError, ValueError) as exc:
            raise StorageUnavailable("malformed generic node property") from exc

    def _run_graph(self, query: str, **parameters: object) -> list[dict[str, Any]]:
        try:
            active = getattr(self._transaction_state, "transaction", None)
            if active is not None:
                return [record.data() for record in active.run(query, **parameters)]
            with self._session() as session:
                return [record.data() for record in session.run(query, **parameters)]
        except (Neo4jError, ServiceUnavailable) as exc:
            raise StorageUnavailable("canonical storage operation failed") from exc

    @contextmanager
    def transaction(self) -> Iterator[None]:
        active = getattr(self._transaction_state, "transaction", None)
        if active is not None:
            try:
                yield
            except BaseException:
                self._transaction_state.rollback_only = True
                raise
            return
        session = None
        transaction = None
        state_set = False
        try:
            session = self._session()
            timeout = getattr(self._transaction_state, "work_timeout", None)
            transaction = (
                session.begin_transaction(timeout=timeout)
                if timeout is not None
                else session.begin_transaction()
            )
            self._transaction_state.transaction = transaction
            self._transaction_state.rollback_only = False
            state_set = True
            yield
            if self._transaction_state.rollback_only:
                raise StorageUnavailable("nested transaction failed")
            transaction.commit()
        except BaseException as exc:
            driver_failure = isinstance(exc, (Neo4jError, ServiceUnavailable))
            rollback_failure = None
            if transaction is not None:
                try:
                    transaction.rollback()
                except (Neo4jError, ServiceUnavailable) as rollback_exc:
                    rollback_failure = rollback_exc
            if driver_failure or rollback_failure is not None:
                cause = rollback_failure if rollback_failure is not None else exc
                raise StorageUnavailable("canonical storage transaction failed") from cause
            raise
        finally:
            if state_set:
                del self._transaction_state.transaction
                del self._transaction_state.rollback_only
            for resource in (transaction, session):
                if resource is not None:
                    try:
                        resource.close()
                    except (Neo4jError, ServiceUnavailable):
                        pass

    @contextmanager
    def work_mutation_transaction(self) -> Iterator[None]:
        """Serialize named Work graph changes across processes, within the DB transaction."""
        prior = getattr(self._transaction_state, "work_timeout", None)
        self._transaction_state.work_timeout = 10.0
        try:
            with self.transaction():
                self._lock_named_work()
                yield
        finally:
            self._transaction_state.work_timeout = prior

    def _lock_named_work(self):
        constraints = self._run_graph(
            "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties "
            "WHERE name = 'work_mutation_guard_id' "
            "RETURN type, labelsOrTypes, properties"
        )
        if constraints != [
            {"type": "UNIQUENESS", "labelsOrTypes": ["WorkMutationGuard"], "properties": ["id"]}
        ]:
            raise StorageUnavailable("named Work mutation schema unavailable")
        self._run_graph(
            "MERGE (guard:WorkMutationGuard {id: 'named-work-v1'}) "
            "ON CREATE SET guard.revision = 0 "
            "SET guard.revision = guard.revision + 1 RETURN guard.revision AS revision"
        )

    @staticmethod
    def _canonical_properties(label: str, node_id: str, properties: object) -> dict[str, Any]:
        archived = isinstance(properties, dict) and properties.get("status") == "archived"
        try:
            return validate_canonical_work_object_properties(
                label,
                node_id,
                properties,
                archived=archived,
            )
        except (TypeError, ValueError) as exc:
            raise StorageUnavailable("malformed canonical work object") from exc

    def _replace_generic_node(
        self,
        node_label: str,
        node_id: str,
        properties: dict[str, Any],
        *,
        archived: bool | None = None,
    ) -> list[dict[str, Any]]:
        current_rows = self._run_graph(
            f"MATCH (n:`{node_label}` {{id: $node_id}}) "
            "RETURN labels(n) AS labels, n.id AS id, n.archived AS archived, "
            "properties(n) AS properties",
            node_id=node_id,
        )
        if len(current_rows) != 1:
            raise KeyError(f"missing or stale {node_label}: {node_id}")
        current_row = current_rows[0]
        current = self._node_from_row(current_row, node_label, expected_id=node_id)
        merged = {**current.properties, **properties}
        encoded = self._generic_properties(merged)
        replacement = {
            "id": node_id,
            "archived": current.archived if archived is None else archived,
            **encoded,
        }
        canonical_label_guard = " AND n.kind IS NULL" if self._is_work_label(node_label) else ""
        return self._run_graph(
            f"MATCH (n:`{node_label}` {{id: $node_id}}) "
            f"WHERE properties(n) = $expected_properties{canonical_label_guard} "
            "SET n = $replacement_properties "
            "RETURN labels(n) AS labels, n.id AS id, n.archived AS archived, "
            "properties(n) AS properties",
            node_id=node_id,
            expected_properties=current_row["properties"],
            replacement_properties=replacement,
        )

    def create_node(
        self, label: str, node_id: str, properties: dict[str, Any] | None = None
    ) -> NodeRecord:
        node_label = self._node_label(label)
        validate_work_object_id(node_id)
        if self._is_canonical_payload(node_label, properties):
            node_properties = self._canonical_properties(node_label, node_id, properties)
            if node_properties["status"] == "archived":
                raise StorageUnavailable("malformed canonical work object")
        else:
            node_properties = self._generic_properties(properties)
        create_nonce = uuid4().hex
        rows = self._run_graph(
            f"MERGE (n:`{node_label}` {{id: $node_id}}) "
            "ON CREATE SET n.archived = false, n += $properties, "
            f"n.{_CREATE_NONCE_PROPERTY} = $create_nonce "
            f"WITH n, coalesce(n.{_CREATE_NONCE_PROPERTY} = $create_nonce, false) "
            "AS created "
            f"REMOVE n.{_CREATE_NONCE_PROPERTY} "
            "WITH n, created WHERE created "
            "RETURN labels(n) AS labels, n.id AS id, n.archived AS archived, "
            "properties(n) AS properties",
            node_id=node_id,
            properties=node_properties,
            create_nonce=create_nonce,
        )
        if not rows:
            raise KeyError(f"already exists {node_label}: {node_id}")
        if len(rows) != 1:
            raise StorageUnavailable("node create failed")
        return self._node_from_row(rows[0], node_label, expected_id=node_id)

    def update_node(self, label: str, node_id: str, properties: dict[str, Any]) -> NodeRecord:
        node_label = self._node_label(label)
        validate_work_object_id(node_id)
        if self._is_canonical_payload(node_label, properties):
            node_properties = self._canonical_properties(node_label, node_id, properties)
            new_version = validate_version(node_properties["version"])
            if new_version == 1:
                raise StorageUnavailable("canonical update requires version advancement")
            archive_assignment = (
                ", n.archived = true" if node_properties["status"] == "archived" else ""
            )
            rows = self._run_graph(
                f"MATCH (n:`{node_label}` {{id: $node_id}}) "
                "WHERE n.kind = $properties.kind AND n.created_at = $properties.created_at "
                "AND n.status <> 'archived' AND n.archived = false "
                "AND n.version = $expected_version "
                f"SET n += $properties{archive_assignment} "
                "RETURN labels(n) AS labels, n.id AS id, n.archived AS archived, "
                "properties(n) AS properties",
                node_id=node_id,
                expected_version=new_version - 1,
                properties=node_properties,
            )
        else:
            rows = self._replace_generic_node(node_label, node_id, properties)
        if len(rows) != 1:
            raise KeyError(f"missing or stale {node_label}: {node_id}")
        return self._node_from_row(rows[0], node_label, expected_id=node_id)

    def archive_node(
        self,
        label: str,
        node_id: str,
        properties: dict[str, Any] | None = None,
    ) -> NodeRecord:
        node_label = self._node_label(label)
        validate_work_object_id(node_id)
        canonical_payload = self._is_canonical_payload(node_label, properties)
        if properties is None:
            canonical_label_guard = (
                "WHERE n.kind IS NULL OR n.status = 'archived' "
                if self._is_work_label(node_label)
                else ""
            )
            rows = self._run_graph(
                f"MATCH (n:`{node_label}` {{id: $node_id}}) "
                f"{canonical_label_guard}"
                "SET n.archived = true "
                "RETURN labels(n) AS labels, n.id AS id, n.archived AS archived, "
                "properties(n) AS properties",
                node_id=node_id,
            )
        elif not canonical_payload:
            rows = self._replace_generic_node(node_label, node_id, properties, archived=True)
        else:
            canonical = self._canonical_properties(node_label, node_id, properties)
            if canonical["status"] != "archived":
                raise StorageUnavailable("malformed canonical archive")
            new_version = validate_version(canonical["version"])
            if new_version == 1:
                raise StorageUnavailable("canonical archive requires version advancement")
            rows = self._run_graph(
                f"MATCH (n:`{node_label}` {{id: $node_id}}) "
                "WHERE n.kind = $properties.kind "
                "AND n.created_at = $properties.created_at "
                "AND n.version = $expected_version "
                "AND n.status <> 'archived' AND n.archived = false "
                "SET n += $properties, n.archived = true "
                "RETURN labels(n) AS labels, n.id AS id, n.archived AS archived, "
                "properties(n) AS properties",
                node_id=node_id,
                expected_version=new_version - 1,
                properties=canonical,
            )
        if len(rows) != 1:
            raise KeyError(f"missing, stale, or inconsistent {node_label}: {node_id}")
        return self._node_from_row(rows[0], node_label, expected_id=node_id)

    @staticmethod
    def _relationship_type(relationship: str) -> str:
        if (
            not isinstance(relationship, str)
            or _RELATIONSHIP_PATTERN.fullmatch(relationship) is None
        ):
            raise StorageUnavailable("invalid relationship")
        return relationship

    @classmethod
    def _edge_from_row(cls, row: dict[str, Any]) -> EdgeRecord:
        try:
            from_labels = row["from_labels"]
            to_labels = row["to_labels"]
            relationship = cls._relationship_type(row["relationship"])
            from_id = validate_work_object_id(row["from_id"])
            to_id = validate_work_object_id(row["to_id"])
            properties = row["properties"]
            if (
                not isinstance(from_labels, list)
                or len(from_labels) != 1
                or not isinstance(to_labels, list)
                or len(to_labels) != 1
                or not isinstance(properties, dict)
            ):
                raise ValueError("edge")
            from_label = cls._node_label(from_labels[0])
            to_label = cls._node_label(to_labels[0])
            edge_identity = properties.get(_EDGE_IDENTITY_PROPERTY)
            if edge_identity is not None and (
                not isinstance(edge_identity, str)
                or re.fullmatch(r"[0-9a-f]{64}", edge_identity) is None
            ):
                raise ValueError("edge identity")
            public_properties = {
                key: value for key, value in properties.items() if key != _EDGE_IDENTITY_PROPERTY
            }
            return EdgeRecord(
                from_label,
                from_id,
                relationship,
                to_label,
                to_id,
                public_properties,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageUnavailable("malformed canonical relationship") from exc

    def create_edge(
        self,
        from_label: str,
        from_id: str,
        relationship: str,
        to_label: str,
        to_id: str,
        properties: dict[str, Any] | None = None,
    ) -> EdgeRecord:
        canonical_from_label = self._node_label(from_label)
        canonical_to_label = self._node_label(to_label)
        validate_work_object_id(from_id)
        validate_work_object_id(to_id)
        relationship_type = self._relationship_type(relationship)
        edge_properties = {} if properties is None else properties
        if (
            not isinstance(edge_properties, dict)
            or not all(isinstance(key, str) for key in edge_properties)
            or _EDGE_IDENTITY_PROPERTY in edge_properties
        ):
            raise StorageUnavailable("invalid relationship properties")
        try:
            edge_identity = hashlib.sha256(
                json.dumps(
                    edge_properties,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
        except (TypeError, ValueError) as exc:
            raise StorageUnavailable("invalid relationship properties") from exc
        rows = self._run_graph(
            f"MATCH (source:`{canonical_from_label}` {{id: $from_id}}), "
            f"(target:`{canonical_to_label}` {{id: $to_id}}) "
            f"MERGE (source)-[edge:`{relationship_type}` "
            f"{{{_EDGE_IDENTITY_PROPERTY}: $edge_identity}}]->(target) "
            "ON CREATE SET edge += $properties "
            "RETURN labels(source) AS from_labels, source.id AS from_id, "
            "type(edge) AS relationship, labels(target) AS to_labels, "
            "target.id AS to_id, properties(edge) AS properties",
            from_id=from_id,
            to_id=to_id,
            properties=dict(edge_properties),
            edge_identity=edge_identity,
        )
        if len(rows) != 1:
            raise KeyError("missing canonical relationship endpoint")
        return self._edge_from_row(rows[0])

    def delete_edge(
        self, from_label: str, from_id: str, relationship: str, to_label: str, to_id: str
    ) -> None:
        source = self._node_label(from_label)
        target = self._node_label(to_label)
        edge_type = self._relationship_type(relationship)
        validate_work_object_id(from_id)
        validate_work_object_id(to_id)
        self._run_graph(
            f"MATCH (source:`{source}` {{id: $from_id}})-[edge:`{edge_type}`]->"
            f"(target:`{target}` {{id: $to_id}}) DELETE edge",
            from_id=from_id,
            to_id=to_id,
        )

    def related_work_nodes(
        self,
        label: str,
        node_id: str,
        relationship: str,
        *,
        incoming: bool,
        after_resource: str | None = None,
        limit: int = 50,
    ) -> list[NodeRecord]:
        from devgraph.model.validation import CANONICAL_WORK_KINDS

        validate_page_limit(limit)
        validate_work_object_id(node_id)
        if (
            label not in CANONICAL_WORK_KINDS
            or relationship not in {"HAS_CHILD", "DEPENDS_ON", "BLOCKS"}
            or type(incoming) is not bool
        ):
            raise ValueError("invalid relationship read")
        arrow = f"<-[:{relationship}]-" if incoming else f"-[:{relationship}]->"
        rows = self._run_graph(
            f"MATCH (source:`{label}` {{id: $node_id}}){arrow}(n) "
            "WHERE size(labels(n)) = 1 AND labels(n)[0] IN $kinds "
            "AND ($after_resource IS NULL OR labels(n)[0] + '/' + n.id > $after_resource) "
            "WITH DISTINCT n ORDER BY labels(n)[0], n.id LIMIT $limit "
            "RETURN labels(n) AS labels, n.id AS id, n.archived AS archived, "
            "properties(n) AS properties",
            node_id=node_id,
            kinds=sorted(CANONICAL_WORK_KINDS),
            after_resource=after_resource,
            limit=limit,
        )
        return [self._node_from_row(row, row["labels"][0], expected_id=row["id"]) for row in rows]

    @classmethod
    def _containment_from_row(cls, row: dict[str, Any]) -> WorkContainment:
        labels = row.get("labels")
        if (
            not isinstance(labels, list) or len(labels) != 1
            or labels[0] not in CANONICAL_WORK_KINDS
        ):
            raise StorageUnavailable("malformed containment target")
        node = cls._node_from_row(row, labels[0])
        groups = []
        for field, relationship in (("parents", "HAS_CHILD"), ("memberships", "CONTAINS_WORK")):
            values = row.get(field)
            if (
                not isinstance(values, list) or len(values) > 2
                or any(not isinstance(value, dict) for value in values)
            ):
                raise StorageUnavailable("malformed containment edges")
            edges = tuple(cls._edge_from_row(value) for value in values)
            if any(
                (edge.to_label, edge.to_id, edge.relationship)
                != (node.label, node.id, relationship) for edge in edges
            ):
                raise StorageUnavailable("mismatched containment edges")
            groups.append(edges)
        return WorkContainment(node, *groups)

    def work_containment(self, label: str, node_id: str) -> WorkContainment | None:
        validate_containment_subject(label, node_id)
        rows = self._run_graph(
            f"MATCH (n:`{label}` {{id: $node_id}}) WITH n LIMIT 2 " + _containment_projection(),
            node_id=node_id,
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise StorageUnavailable("ambiguous containment target")
        result = self._containment_from_row(rows[0])
        if (result.node.label, result.node.id) != (label, node_id):
            raise StorageUnavailable("mismatched containment target")
        return result

    def arena_member_page(
        self, arena_id: str, *, after_resource: str | None = None, limit: int = 50
    ) -> list[WorkContainment]:
        validate_arena_page(arena_id, after_resource, limit)
        rows = self._run_graph(
            "MATCH (:Arena {id: $arena_id})-[:CONTAINS_WORK]->(n) "
            "WHERE n.id IS NULL OR size(labels(n)) <> 1 OR $after_resource IS NULL "
            "OR labels(n)[0] + '/' + n.id > $after_resource "
            "WITH DISTINCT n ORDER BY labels(n)[0], n.id LIMIT $limit "
            + _containment_projection(),
            arena_id=arena_id, after_resource=after_resource, limit=limit,
        )
        result = [self._containment_from_row(row) for row in rows]
        keys = [(item.node.label, item.node.id) for item in result]
        if (
            len(result) > limit or keys != sorted(set(keys))
            or any(after_resource is not None and f"{kind}/{work_id}" <= after_resource
                   for kind, work_id in keys)
        ):
            raise StorageUnavailable("invalid containment page")
        return result

    def supporting_material_references(
        self,
        label: str,
        node_id: str,
        *,
        artifact_ids: tuple[str, ...],
        external_link_ids: tuple[str, ...],
        after_resource: str | None = None,
        target_resource: str | None = None,
        limit: int = 50,
    ) -> list[SupportingReference]:
        validate_supporting_read(
            label, node_id, artifact_ids, external_link_ids, limit, after_resource, target_resource
        )
        # Each MATCH is anchored to this parent. UNION also includes dangling field references.
        rows = self._run_graph(
            "CALL { UNWIND $artifact_ids AS id "
            "RETURN 'Artifact' AS kind, id, 'field/artifact_ids' AS path "
            "UNION UNWIND $external_link_ids AS id "
            "RETURN 'ExternalLink' AS kind, id, 'field/external_link_ids' AS path "
            f"UNION MATCH (source:`{label}` {{id: $node_id}})-[edge:HAS_ARTIFACT|"
            "HAS_EXTERNAL_LINK|HAS_REQUIREMENT|HAS_ACCEPTANCE_CRITERION]->(target) "
            "WHERE size(labels(target)) = 1 AND "
            "((type(edge) = 'HAS_ARTIFACT' AND target:Artifact) OR "
            "(type(edge) = 'HAS_EXTERNAL_LINK' AND target:ExternalLink) OR "
            "(type(edge) = 'HAS_REQUIREMENT' AND target:Requirement) OR "
            "(type(edge) = 'HAS_ACCEPTANCE_CRITERION' AND target:AcceptanceCriterion)) "
            "RETURN labels(target)[0] AS kind, target.id AS id, 'edge/' + type(edge) AS path "
            f"UNION MATCH (source:`{label}` {{id: $node_id}})-[:HAS_REQUIREMENT]->"
            "(requirement:Requirement)-[:HAS_ACCEPTANCE_CRITERION]->(target:AcceptanceCriterion) "
            "WHERE labels(requirement) = ['Requirement'] "
            "AND labels(target) = ['AcceptanceCriterion'] "
            "RETURN 'AcceptanceCriterion' AS kind, target.id AS id, "
            "'requirement/' + requirement.id AS path } "
            "WITH kind, id, path WHERE ($after_resource IS NULL "
            "OR kind + '/' + id > $after_resource) "
            "AND ($target_resource IS NULL OR kind + '/' + id = $target_resource) "
            "WITH DISTINCT kind, id, path ORDER BY kind, id, path "
            "WITH kind, id, collect(path) AS paths ORDER BY kind, id LIMIT $limit "
            "RETURN kind, id, paths[..$max_provenance] AS paths, "
            "size(paths) > $max_provenance AS paths_truncated",
            node_id=node_id,
            artifact_ids=list(artifact_ids),
            external_link_ids=list(external_link_ids),
            after_resource=after_resource,
            target_resource=target_resource,
            limit=limit,
            max_provenance=MAX_PROVENANCE,
        )
        references = []
        for row in rows:
            kind, item_id = row.get("kind"), row.get("id")
            validate_supporting_keys([(kind, item_id)])
            paths = row.get("paths")
            if (
                not isinstance(paths, list)
                or not 1 <= len(paths) <= MAX_PROVENANCE
                or not all(isinstance(path, str) for path in paths)
                or type(row.get("paths_truncated")) is not bool
            ):
                raise StorageUnavailable("malformed supporting reference")
            references.append(
                SupportingReference(kind, item_id, tuple(paths), row["paths_truncated"])
            )
        if len(references) > limit:
            raise StorageUnavailable("supporting reference bound exceeded")
        return references


    def supporting_material_nodes(self, keys: list[tuple[str, str]]) -> list[NodeRecord]:
        validate_supporting_keys(keys)
        if not keys:
            return []
        # Four fixed labels preserve indexed identity reads; no caller-supplied Cypher.
        branches = [
            f"WITH ref MATCH (n:`{kind}` {{id: ref.id}}) WHERE ref.kind = '{kind}' RETURN n"
            for kind in sorted(SUPPORTING_KINDS)
        ]
        # Project only readable metadata, never payload/credential properties from these records.
        projected = sorted(SUPPORTING_PROPERTIES | {"id", "archived", _GENERIC_ENCODING_PROPERTY})
        projection = "n { " + ", ".join("." + name for name in projected) + " }"
        rows = self._run_graph(
            "UNWIND $keys AS ref CALL { " + " UNION ".join(branches) + " } "
            "RETURN labels(n) AS labels, n.id AS id, n.archived AS archived, "
            f"{projection} AS properties LIMIT $limit",
            keys=[{"kind": kind, "id": item_id} for kind, item_id in keys],
            limit=len(keys) + 1,
        )
        found = {}
        requested = set(keys)
        for row in rows:
            labels, item_id = row.get("labels"), row.get("id")
            if (
                not isinstance(labels, list)
                or len(labels) != 1
                or (labels[0], item_id) not in requested
            ):
                raise StorageUnavailable("malformed supporting identity")
            key = (labels[0], item_id)
            # Map projection represents absent Neo4j properties as null. Preserve encoded nulls
            # (strings until decoded) but omit truly absent fields, matching memory storage.
            if isinstance(row.get("properties"), dict):
                row = {
                    **row,
                    "properties": {
                        name: value
                        for name, value in row["properties"].items()
                        if value is not None
                    },
                }
            try:
                node = self._node_from_row(
                    row, key[0], expected_id=key[1], validate_canonical=False
                )
            except StorageUnavailable:
                node = NodeRecord(*key, {"__supporting_invalid": True})
            if key in found:
                node = NodeRecord(*key, {"__supporting_invalid": True})
            found[key] = node
        if len(rows) > len(keys):
            raise StorageUnavailable("ambiguous supporting identity")
        return list(found.values())

    def list_edges(
        self, relationship: str | None = None, *, limit: int | None = None
    ) -> list[EdgeRecord]:
        if relationship is not None:
            self._relationship_type(relationship)
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 10_001):
            raise StorageUnavailable("invalid edge limit")
        limit_clause = "" if limit is None else " LIMIT $limit"
        parameters = {"relationship": relationship}
        if limit is not None:
            parameters["limit"] = limit
        rows = self._run_graph(
            "MATCH (source)-[edge]->(target) "
            "WHERE $relationship IS NULL OR type(edge) = $relationship "
            "RETURN labels(source) AS from_labels, source.id AS from_id, "
            "type(edge) AS relationship, labels(target) AS to_labels, "
            f"target.id AS to_id, properties(edge) AS properties{limit_clause}",
            **parameters,
        )
        edges = [self._edge_from_row(row) for row in rows]
        return sorted(
            edges,
            key=lambda edge: (
                edge.from_label,
                edge.from_id,
                edge.relationship,
                edge.to_label,
                edge.to_id,
            ),
        )

    def get_node(self, label: str, node_id: str) -> NodeRecord | None:
        node_label = self._node_label(label)
        validate_work_object_id(node_id)
        rows = self._run_graph(
            f"MATCH (n:`{node_label}` {{id: $node_id}}) "
            "RETURN labels(n) AS labels, n.id AS id, n.archived AS archived, "
            "properties(n) AS properties",
            node_id=node_id,
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise StorageUnavailable("malformed node")
        return self._node_from_row(rows[0], node_label, expected_id=node_id)

    def query(
        self,
        label: str | None = None,
        archived: bool | None = None,
        *,
        descending: bool = False,
        after_id: str | None = None,
        limit: int | None = None,
    ) -> list[NodeRecord]:
        node_label = None if label is None else self._node_label(label)
        if archived is not None and not isinstance(archived, bool):
            raise StorageUnavailable("invalid archive filter")
        if not isinstance(descending, bool):
            raise StorageUnavailable("invalid order")
        if after_id is not None:
            if node_label is None:
                raise StorageUnavailable("after_id requires a node label")
            validate_work_object_id(after_id)
        if limit is not None:
            validate_page_limit(limit)
        comparison = "<" if descending else ">"
        direction = "DESC" if descending else "ASC"
        match = "MATCH (n)" if node_label is None else f"MATCH (n:`{node_label}`)"
        cursor = "" if after_id is None else f" AND n.id {comparison} $after_id"
        order = (
            f"labels(n)[0] {direction}, n.id {direction}"
            if node_label is None
            else f"n.id {direction}"
        )
        limit_clause = "" if limit is None else " LIMIT $limit"
        parameters: dict[str, object] = {
            "archived": archived,
            "after_id": after_id,
        }
        if limit is not None:
            parameters["limit"] = limit
        application_guard = (
            "n.id IS NOT NULL AND n.archived IS NOT NULL AND " if node_label is None else ""
        )
        rows = self._run_graph(
            f"{match} WHERE {application_guard}"
            f"($archived IS NULL OR n.archived = $archived){cursor} "
            "RETURN labels(n) AS labels, n.id AS id, n.archived AS archived, "
            f"properties(n) AS properties ORDER BY {order}{limit_clause}",
            **parameters,
        )
        records: list[NodeRecord] = []
        for row in rows:
            expected_label = node_label
            if expected_label is None:
                labels = row.get("labels")
                if not isinstance(labels, list) or len(labels) != 1:
                    raise StorageUnavailable("malformed node")
                expected_label = self._node_label(labels[0])
            records.append(self._node_from_row(row, expected_label))
        return records

    def claim_event_receipt(
        self,
        node_id: str,
        idempotency_claim_digest: str,
        properties: dict[str, Any],
    ) -> EventReceiptClaim:
        validate_work_object_id(node_id)
        if properties.get("idempotency_claim_digest") != idempotency_claim_digest:
            raise StorageUnavailable("invalid idempotency claim")
        encoded = self._generic_properties(properties)
        encoded_digest = encoded.get("idempotency_claim_digest")
        if not isinstance(encoded_digest, str):
            raise StorageUnavailable("invalid idempotency claim")
        create_nonce = uuid4().hex
        rows = self._run_graph(
            "MERGE (n:`EventReceipt` {idempotency_claim_digest: $claim_digest}) "
            "ON CREATE SET n = $properties, n.id = $node_id, n.archived = false, "
            f"n.{_CREATE_NONCE_PROPERTY} = $create_nonce "
            f"WITH n, coalesce(n.{_CREATE_NONCE_PROPERTY} = $create_nonce, false) "
            "AS created "
            f"REMOVE n.{_CREATE_NONCE_PROPERTY} "
            "RETURN labels(n) AS labels, n.id AS id, n.archived AS archived, "
            "properties(n) AS properties, created AS created",
            claim_digest=encoded_digest,
            node_id=node_id,
            properties=encoded,
            create_nonce=create_nonce,
        )
        if len(rows) != 1 or not isinstance(rows[0].get("created"), bool):
            raise StorageUnavailable("idempotency claim failed")
        return EventReceiptClaim(
            rows[0]["created"],
            self._node_from_row(rows[0], "EventReceipt"),
        )

    def inspect_canonical_persistence(self) -> None:
        rows = self._run_graph(
            "MATCH (n) WHERE any(candidate IN labels(n) WHERE candidate IN $labels) "
            "RETURN labels(n) AS labels, n.id AS id, n.archived AS archived, "
            "properties(n) AS properties",
            labels=list(CANONICAL_WORK_KINDS),
        )
        try:
            for row in rows:
                labels = row.get("labels")
                node_id = row.get("id")
                archived = row.get("archived")
                properties = row.get("properties")
                if (
                    not isinstance(labels, list)
                    or len(labels) != 1
                    or labels[0] not in CANONICAL_WORK_KINDS
                    or not isinstance(properties, dict)
                    or properties.get("kind") != labels[0]
                    or properties.get("id") != node_id
                    or properties.get("archived") is not archived
                ):
                    raise ValueError("identity")
                validate_work_object_id(node_id)
                if not isinstance(archived, bool):
                    raise TypeError("archived")
                validate_canonical_work_object_properties(
                    labels[0],
                    node_id,
                    properties,
                    archived=archived,
                    allow_unknown=True,
                )
        except (TypeError, ValueError) as exc:
            raise StorageUnavailable("malformed canonical persistence") from exc

    @classmethod
    def _node_from_row(
        cls, row: dict[str, Any], expected_label: str, *, expected_id: str | None = None,
        validate_canonical: bool = True,
    ) -> NodeRecord:
        canonical_payload = False
        try:
            labels = row["labels"]
            node_id = row["id"]
            archived = row["archived"]
            raw_properties = row["properties"]
            canonical_payload = cls._is_canonical_payload(expected_label, raw_properties)
            if labels != [expected_label]:
                raise ValueError("labels")
            validate_work_object_id(node_id)
            if expected_id is not None and node_id != expected_id:
                raise ValueError("id")
            if not isinstance(archived, bool) or not isinstance(raw_properties, dict):
                raise TypeError("row")
            if (
                raw_properties.get("id") != node_id
                or raw_properties.get("archived") is not archived
            ):
                raise ValueError("identity")
            if canonical_payload and validate_canonical:
                properties = {
                    key: raw_properties[key]
                    for key in CANONICAL_MODEL_PROPERTIES
                    if key in raw_properties
                }
                properties = validate_canonical_work_object_properties(
                    expected_label,
                    node_id,
                    properties,
                    archived=archived,
                )
            else:
                encoding = raw_properties.get(_GENERIC_ENCODING_PROPERTY)
                if encoding is None:
                    properties = {
                        key: value
                        for key, value in raw_properties.items()
                        if key not in {"id", "archived"}
                    }
                elif encoding == _GENERIC_ENCODING_VALUE:
                    properties = {
                        key: cls._decode_generic_property(value)
                        for key, value in raw_properties.items()
                        if key not in {"id", "archived", _GENERIC_ENCODING_PROPERTY}
                    }
                else:
                    raise ValueError("generic encoding")
                if not all(isinstance(key, str) for key in properties):
                    raise TypeError("properties")
            return NodeRecord(expected_label, node_id, properties, archived)
        except (KeyError, TypeError, ValueError) as exc:
            message = "malformed canonical work object" if canonical_payload else "malformed node"
            raise StorageUnavailable(message) from exc

    def health(self) -> HealthStatus:
        try:
            self._driver.verify_connectivity()
        except (Neo4jError, ServiceUnavailable):
            return HealthStatus(live=True, ready=False, detail="Neo4j storage unavailable")
        return HealthStatus(live=True, ready=True, detail="Neo4j storage ready")

    def close(self) -> None:
        self._driver.close()

    def create_cypher_read_runner(self):
        """Create an isolated bounded pool; reads cannot exhaust the Work pool."""
        from devgraph.storage.cypher_read import Neo4jCypherReadRunner

        driver = GraphDatabase.driver(
            self._config.uri, auth=(self._config.user, self._config.password),
            max_connection_pool_size=2, connection_timeout=2.0,
            connection_acquisition_timeout=2.0, max_transaction_retry_time=0.0,
        )
        try:
            return Neo4jCypherReadRunner(driver, database=self._config.database or "neo4j")
        except BaseException:
            driver.close()
            raise

    def _session(self) -> Any:
        if self._config.database:
            return self._driver.session(database=self._config.database)
        return self._driver.session()


class Neo4jMigrationStore:
    """Neo4j translation of the dedicated migration journal protocol."""

    def __init__(self, storage: Neo4jGraphStorage) -> None:
        self._storage = storage

    def _run(self, query: str, **parameters: Any) -> list[dict[str, Any]]:
        try:
            with self._storage._session() as session:
                return [record.data() for record in session.run(query, **parameters)]
        except (Neo4jError, ServiceUnavailable) as exc:
            raise StorageUnavailable("migration store operation failed") from exc

    def inspect_bootstrap_constraint(self) -> BootstrapConstraint | None:
        rows = self._constraint_rows("devgraph_migration_version_unique")
        if not rows:
            return None
        if len(rows) != 1:
            return ("ambiguous",)
        row = rows[0]
        return BootstrapConstraint(
            row["name"],
            row["type"],
            tuple(row["labelsOrTypes"])[0],
            tuple(row["properties"]),
            self._normalized_create(row["createStatement"]),
        )

    def execute_bootstrap(self, statement: str) -> None:
        self._run(statement)

    def inspect_owner(self) -> MigrationOwner | None:
        rows = self._run(
            "MATCH (m:DevgraphMigration {version: 0}) "
            "RETURN m.owner_attempt_id AS owner, m.state AS state, "
            "m.runner_schema_version AS runner"
        )
        if not rows:
            return None
        if len(rows) != 1 or rows[0]["runner"] != 1:
            raise StorageUnavailable("malformed migration owner")
        owner = rows[0]["owner"]
        state = rows[0]["state"]
        if not isinstance(state, str) or (
            (state == "owned" and not isinstance(owner, str))
            or (state == "clean" and owner is not None)
            or state not in {"owned", "clean"}
        ):
            raise StorageUnavailable("malformed migration owner")
        return MigrationOwner(owner, state, rows[0]["runner"])

    def acquire_owner(self, attempt_id: str) -> bool:
        rows = self._run(
            "MERGE (m:DevgraphMigration {version: 0}) "
            "ON CREATE SET m.owner_attempt_id = $attempt, m.runner_schema_version = 1, "
            "m.state = 'owned' WITH m WHERE m.owner_attempt_id = $attempt OR ("
            "m.owner_attempt_id IS NULL AND m.state = 'clean' AND "
            "m.runner_schema_version = 1) "
            "SET m.owner_attempt_id = $attempt, m.state = 'owned' "
            "RETURN count(m) AS acquired",
            attempt=attempt_id,
        )
        return bool(rows and rows[0]["acquired"] == 1)

    def recover_owner(
        self,
        expected_owner_attempt_id: str,
        new_attempt_id: str,
        journal: MigrationJournal | None,
    ) -> bool:
        if journal is None:
            rows = self._run(
                "MATCH (owner:DevgraphMigration {version: 0, owner_attempt_id: $expected}) "
                "WHERE owner.state = 'owned' AND owner.runner_schema_version = 1 "
                "AND NOT EXISTS { MATCH (j:DevgraphMigration) WHERE j.version >= 1 "
                "AND (j.state IS NULL OR NOT j.state IN "
                "['applied', 'recoverable_ddl_not_applied']) } "
                "SET owner.owner_attempt_id = $new_attempt "
                "RETURN count(owner) AS recovered",
                expected=expected_owner_attempt_id,
                new_attempt=new_attempt_id,
            )
            return bool(rows and rows[0]["recovered"] == 1)
        rows = self._run(
            "MATCH (owner:DevgraphMigration {version: 0, owner_attempt_id: $expected}) "
            "MATCH (j:DevgraphMigration {version: $version, owner_attempt_id: $expected}) "
            "WHERE owner.state = 'owned' AND owner.runner_schema_version = 1 "
            "AND j.state = 'ddl_started' AND j.name = $name AND j.checksum = $checksum "
            "AND j.schema_object_name = $schema_object_name "
            "AND j.schema_object_type = $schema_object_type "
            "AND j.schema_object_definition = $definition AND j.started_at = $started_at "
            "AND j.completed_at IS NULL AND j.runner_schema_version = $runner_schema_version "
            "SET owner.owner_attempt_id = $new_attempt, j.owner_attempt_id = $new_attempt "
            "RETURN count(j) AS recovered",
            expected=expected_owner_attempt_id,
            new_attempt=new_attempt_id,
            version=journal.version,
            name=journal.name,
            checksum=journal.checksum,
            schema_object_name=journal.schema_object_name,
            schema_object_type=journal.schema_object_type,
            definition=journal.schema_object_definition,
            started_at=journal.started_at,
            runner_schema_version=journal.runner_schema_version,
        )
        return bool(rows and rows[0]["recovered"] == 1)

    def release_owner(self, attempt_id: str) -> bool:
        rows = self._run(
            "MATCH (m:DevgraphMigration {version: 0, owner_attempt_id: $attempt}) "
            "WHERE m.state = 'owned' AND m.runner_schema_version = 1 "
            "AND NOT EXISTS { MATCH (j:DevgraphMigration) WHERE j.version >= 1 "
            "AND (j.state IS NULL OR NOT j.state IN "
            "['applied', 'recoverable_ddl_not_applied']) } "
            "REMOVE m.owner_attempt_id SET m.state = 'clean' RETURN count(m) AS released",
            attempt=attempt_id,
        )
        return bool(rows and rows[0]["released"] == 1)

    def inspect_journal(self) -> tuple[MigrationJournal, ...]:
        rows = self._run(
            "MATCH (m:DevgraphMigration) WHERE m.version >= 1 "
            "RETURN properties(m) AS data ORDER BY m.version"
        )
        return tuple(MigrationJournal(**row["data"]) for row in rows)

    def compare_and_set_journal(
        self,
        before: MigrationJournal | None,
        after: MigrationJournal,
        attempt_id: str,
    ) -> bool:
        from dataclasses import asdict

        data = asdict(after)  # type: ignore[arg-type]
        before_data = asdict(before) if before is not None else None  # type: ignore[arg-type]
        if before_data is not None and before_data["version"] != data["version"]:
            return False
        rows = self._run(
            "MATCH (:DevgraphMigration {version: 0, owner_attempt_id: $attempt}) "
            "OPTIONAL MATCH (m:DevgraphMigration {version: $before_version}) "
            "WITH m WHERE ($before_state IS NULL AND m IS NULL) OR ("
            "m.state = $before_state AND "
            "m.owner_attempt_id = $before_owner_attempt_id AND "
            "m.name = $before_name AND m.checksum = $before_checksum AND "
            "m.schema_object_name = $before_schema_object_name AND "
            "m.schema_object_type = $before_schema_object_type AND "
            "m.schema_object_definition = $before_schema_object_definition AND "
            "m.started_at = $before_started_at AND "
            "((m.completed_at IS NULL AND $before_completed_at IS NULL) OR "
            "m.completed_at = $before_completed_at) AND "
            "m.runner_schema_version = $before_runner_schema_version) "
            "MERGE (next:DevgraphMigration {version: $version}) SET next = $data "
            "RETURN count(next) AS changed",
            attempt=attempt_id,
            version=data["version"],
            before_version=data["version"] if before_data is None else before_data["version"],
            before_state=None if before_data is None else before_data["state"],
            before_owner_attempt_id=None
            if before_data is None
            else before_data["owner_attempt_id"],
            before_name=None if before_data is None else before_data["name"],
            before_checksum=None if before_data is None else before_data["checksum"],
            before_schema_object_name=(
                None if before_data is None else before_data["schema_object_name"]
            ),
            before_schema_object_type=(
                None if before_data is None else before_data["schema_object_type"]
            ),
            before_schema_object_definition=(
                None if before_data is None else before_data["schema_object_definition"]
            ),
            before_started_at=None if before_data is None else before_data["started_at"],
            before_completed_at=None if before_data is None else before_data["completed_at"],
            before_runner_schema_version=(
                None if before_data is None else before_data["runner_schema_version"]
            ),
            data=data,
        )
        return bool(rows and rows[0]["changed"] == 1)

    def apply_transactional_data(self, migration: Any, attempt_id: str, completed_at: str) -> bool:
        from dataclasses import asdict

        if (
            getattr(migration, "version", None) != 23
            or getattr(migration, "name", None) != "canonical_work_object_persistence_v1"
            or getattr(migration, "kind", None) != "transactional_data"
        ):
            raise StorageUnavailable("unsupported transactional migration")
        journal = MigrationJournal.transactional_applied(migration, attempt_id, completed_at)
        backfills: list[tuple[str, str, bool]] = []
        try:
            with self._storage.transaction():
                for label in CANONICAL_WORK_KINDS:
                    rows = self._storage._run_graph(
                        f"MATCH (n:`{label}`) RETURN labels(n) AS labels, n.id AS id, "
                        "properties(n) AS properties ORDER BY n.id"
                    )
                    for row in rows:
                        raw_properties = row.get("properties")
                        if not isinstance(raw_properties, dict):
                            raise ValueError("properties")
                        if raw_properties.get("kind") != label:
                            raise StorageUnavailable("canonical persistence preflight failed")
                        node_id = row.get("id")
                        archived_missing = "archived" not in raw_properties
                        archived = (
                            raw_properties.get("status") == "archived"
                            if archived_missing
                            else raw_properties.get("archived")
                        )
                        hydrated_row = {
                            "labels": row.get("labels"),
                            "id": node_id,
                            "archived": archived,
                            "properties": {**raw_properties, "archived": archived},
                        }
                        try:
                            Neo4jGraphStorage._node_from_row(
                                hydrated_row, label, expected_id=node_id
                            )
                        except StorageUnavailable as exc:
                            raise StorageUnavailable(
                                "canonical persistence preflight failed"
                            ) from exc
                        validated_id = validate_work_object_id(node_id)
                        if not isinstance(archived, bool):
                            raise ValueError("archived")
                        if archived_missing:
                            backfills.append((label, validated_id, archived))
                for label, node_id, archived in backfills:
                    changed = self._storage._run_graph(
                        f"MATCH (n:`{label}` {{id: $node_id}}) "
                        "WHERE n.archived IS NULL SET n.archived = $archived "
                        "RETURN count(n) AS changed",
                        node_id=node_id,
                        archived=archived,
                    )
                    if not changed or changed[0].get("changed") != 1:
                        raise StorageUnavailable("canonical persistence backfill failed")
                rows = self._storage._run_graph(
                    "MATCH (owner:DevgraphMigration {version: 0, owner_attempt_id: $attempt}) "
                    "WHERE owner.state = 'owned' AND owner.runner_schema_version = 1 "
                    "MATCH (prior:DevgraphMigration) "
                    "WHERE prior.version >= 1 AND prior.version <= 22 AND prior.state = 'applied' "
                    "WITH owner, count(prior) AS prior_count "
                    "WHERE prior_count = 22 AND NOT EXISTS { "
                    "MATCH (:DevgraphMigration {version: 23}) } "
                    "CREATE (journal:DevgraphMigration {version: 23}) SET journal = $data "
                    "RETURN count(journal) AS applied",
                    attempt=attempt_id,
                    data=asdict(journal),
                )
                if len(rows) != 1 or rows[0].get("applied") != 1:
                    raise StorageUnavailable("canonical persistence journal marker failed")
                return True
        except StorageUnavailable:
            raise
        except Exception as exc:
            raise StorageUnavailable("canonical persistence preflight failed") from exc

    def execute_ddl(self, payload: bytes) -> None:
        self._run(payload.decode("utf-8"))

    def inspect_schema_object(self, name: str) -> SchemaObject | Literal[False] | None:
        rows = self._constraint_rows(name)
        if not rows:
            return False
        if len(rows) != 1:
            return None
        row = rows[0]
        return SchemaObject(row["type"], self._normalized_create(row["createStatement"]))

    def _constraint_rows(self, name: str) -> list[dict[str, Any]]:
        return self._run(
            "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties, createStatement "
            "WHERE name = $name RETURN name, type, labelsOrTypes, properties, createStatement",
            name=name,
        )

    @staticmethod
    def _normalized_create(value: object) -> str:
        normalized = " ".join(str(value).replace("`", "").split())
        normalized = re.sub(r" OPTIONS \{indexProvider: ['\"]?range-1\.0['\"]?\}$", "", normalized)
        match = re.fullmatch(
            r"CREATE CONSTRAINT (\S+)(?: IF NOT EXISTS)? FOR \(\w+:(\w+)\) "
            r"REQUIRE \(?\w+\.(\w+)\)? IS UNIQUE",
            normalized,
        )
        if match is None:
            return normalized
        name, label, property_name = match.groups()
        return (
            f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (m:{label}) "
            f"REQUIRE m.{property_name} IS UNIQUE"
        )
