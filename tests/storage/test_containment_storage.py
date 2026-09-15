"""Scoped storage reads retain malformed-edge witnesses and bound returned data."""

from copy import deepcopy

import pytest
from tests.storage.test_supporting_material_storage import neo4j_rows

from devgraph.model.work import Task
from devgraph.storage.base import StorageUnavailable
from devgraph.storage.memory import MemoryGraphStorage
from devgraph.storage.neo4j import Neo4jGraphStorage


def record():
    task = Task(id='task', title='Fixture')
    edge = {'from_labels': ['Arena'], 'from_id': 'gallery', 'relationship': 'CONTAINS_WORK',
            'to_labels': ['Task'], 'to_id': task.id, 'properties': {}}
    return {'labels': ['Task'], 'id': task.id, 'archived': False,
            'properties': {'id': task.id, 'archived': False, **task.to_node_properties()},
            'parents': [], 'memberships': [edge]}


def test_scoped_storage_adapters_agree_and_do_not_hide_duplicate_edges():
    row = record()
    row['memberships'] *= 2
    neo4j, calls = neo4j_rows([row])
    memory = MemoryGraphStorage()
    work_id = row['id']
    properties = {key: value for key, value in row['properties'].items()
                  if key not in {'id', 'archived'}}
    memory.create_node('Task', work_id, properties)
    memory.create_node('Arena', 'gallery')
    edge = memory.create_edge('Arena', 'gallery', 'CONTAINS_WORK', 'Task', work_id)
    memory._edges.extend([edge] * 100)
    actual = neo4j.work_containment('Task', work_id)
    assert actual == memory.work_containment('Task', work_id)
    assert len(actual.memberships) == 2  # Two suffice to prove ambiguity, even for high degree.
    assert neo4j.arena_member_page('gallery') == memory.arena_member_page('gallery') == [actual]
    assert 'MATCH (n:`Task` {id: $node_id})' in calls[0][0]
    page_query, parameters = calls[1]
    assert 'MATCH (:Arena {id: $arena_id})-[:CONTAINS_WORK]->(n)' in page_query
    assert page_query.count('WITH n, source, edge LIMIT 2') == 2
    assert 'WITH DISTINCT n ORDER BY labels(n)[0], n.id LIMIT $limit' in page_query
    assert parameters == {'arena_id': 'gallery', 'after_resource': None, 'limit': 50}


@pytest.mark.parametrize('field,value', [
    ('labels', ['Task', 'Issue']), ('labels', []),
    ('parents', None), ('parents', [{}]), ('memberships', ['bad']),
])
def test_neo4j_containment_rejects_malformed_rows(field, value):
    row = record()
    row[field] = value
    storage, _ = neo4j_rows([row])
    with pytest.raises(StorageUnavailable):
        storage.work_containment('Task', 'task')


@pytest.mark.parametrize('field,value', [
    ('to_id', 'different'), ('to_labels', ['Issue']), ('relationship', 'BLOCKS'),
])
def test_neo4j_containment_edges_must_belong_to_the_requested_record(field, value):
    row = record()
    row['memberships'][0][field] = value
    storage, _ = neo4j_rows([row])
    with pytest.raises(StorageUnavailable):
        storage.work_containment('Task', 'task')


def test_storage_page_rejects_duplicate_records_and_oversized_edge_groups():
    row = record()
    storage, _ = neo4j_rows([row, deepcopy(row)])
    with pytest.raises(StorageUnavailable):
        storage.arena_member_page('gallery')
    row['memberships'] *= 3
    storage, _ = neo4j_rows([row])
    with pytest.raises(StorageUnavailable):
        storage.work_containment('Task', 'task')


@pytest.mark.parametrize('storage', [MemoryGraphStorage(), object.__new__(Neo4jGraphStorage)])
@pytest.mark.parametrize('options', [
    {'limit': 0}, {'limit': 101}, {'limit': True},
    {'after_resource': 'Issue/issue'}, {'after_resource': 'Task/../../escape'},
])
def test_storage_page_inputs_are_bounded_before_database_access(storage, options):
    with pytest.raises(ValueError):
        storage.arena_member_page('gallery', **options)
