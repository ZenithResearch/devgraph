from __future__ import annotations

from devgraph.model.work import Handoff, Issue, ReviewPacket, Task
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage


def test_handoff_edges_preserve_history_for_work_objects():
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)

    issue = Issue(id="issue-handoff", title="Relationship handoffs")
    first_handoff = Handoff(id="handoff-1", title="Implementation handoff")
    second_handoff = Handoff(id="handoff-2", title="Review handoff")

    for work_object in (issue, first_handoff, second_handoff):
        graph.add_work_object(work_object)

    first_edge = graph.add_handoff(issue, first_handoff)
    second_edge = graph.add_handoff(issue, second_handoff)

    assert first_edge.relationship == "HAS_HANDOFF"
    assert first_edge.from_label == "Issue"
    assert first_edge.to_label == "Handoff"
    assert second_edge.relationship == "HAS_HANDOFF"
    assert graph.handoffs_for(issue) == [first_handoff, second_handoff]
    assert graph.work_objects_for_handoff(first_handoff) == [issue]


def test_review_packet_edges_are_traversable_without_implying_approval():
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)

    task = Task(id="task-review", title="Review packet traversal")
    packet = ReviewPacket(id="packet-1", title="Evidence bundle")

    graph.add_work_object(task)
    graph.add_work_object(packet)

    edge = graph.add_review_packet(task, packet)

    assert edge.relationship == "HAS_REVIEW_PACKET"
    assert edge.from_label == "Task"
    assert edge.from_id == task.id
    assert edge.to_label == "ReviewPacket"
    assert edge.to_id == packet.id
    assert graph.review_packets_for(task) == [packet]
    assert graph.work_objects_for_review_packet(packet) == [task]


def test_handoff_and_review_packet_edges_reject_invalid_source_and_target_types():
    storage = MemoryGraphStorage()
    graph = RelationshipGraph(storage)

    task = Task(id="task-1", title="Valid work")
    handoff = Handoff(id="handoff-1", title="Valid handoff")
    packet = ReviewPacket(id="packet-1", title="Valid packet")

    for work_object in (task, handoff, packet):
        graph.add_work_object(work_object)

    try:
        graph.add_handoff(handoff, handoff)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "HAS_HANDOFF relationships must start from Todo-derived work" in str(exc)
    else:
        raise AssertionError("HAS_HANDOFF should reject non-Todo source nodes")

    try:
        graph.add_handoff(task, packet)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "HAS_HANDOFF relationships must target Handoff" in str(exc)
    else:
        raise AssertionError("HAS_HANDOFF should reject non-Handoff targets")

    try:
        graph.add_review_packet(packet, packet)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "HAS_REVIEW_PACKET relationships must start from Todo-derived work" in str(
            exc
        )
    else:
        raise AssertionError("HAS_REVIEW_PACKET should reject non-Todo source nodes")

    try:
        graph.add_review_packet(task, handoff)  # type: ignore[arg-type]
    except ValueError as exc:
        assert "HAS_REVIEW_PACKET relationships must target ReviewPacket" in str(exc)
    else:
        raise AssertionError("HAS_REVIEW_PACKET should reject non-ReviewPacket targets")
