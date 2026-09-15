"""Read-only, redaction-safe Dev Graph monitor projections."""

from __future__ import annotations

from collections import Counter
from typing import Any

from devgraph.events.outbox import EVENT_RECEIPT_LABEL
from devgraph.model.base import TERMINAL_STATUSES, WorkStatus, utc_now
from devgraph.model.initiative_observations import (
    INITIATIVE_OBSERVATION_LABEL,
    INITIATIVE_OBSERVATION_ROLE,
)
from devgraph.policy.redaction import redact_text
from devgraph.storage.base import GraphStorage

MONITORED_WORK_KINDS = ("Proposal", "Initiative", "Project", "Issue", "Task")
WORK_PROGRESS_SCHEMA_VERSION = "devgraph.work-progress.v0"
_WORK_STATUS_VALUES = tuple(status.value for status in WorkStatus)
_TERMINAL_STATUS_VALUES = frozenset(status.value for status in TERMINAL_STATUSES)


def _graph_key(label: str, node_id: str) -> str:
    return f"{label}:{node_id}"


def _graph_node(
    *,
    storage_label: str,
    kind: str,
    node_id: str,
    title: str,
    status: str,
    category: str,
    archived: bool,
) -> dict[str, Any]:
    return {
        "key": _graph_key(storage_label, node_id),
        "id": node_id,
        "kind": kind,
        "title": redact_text(title),
        "status": status,
        "category": category,
        "archived": archived,
    }


def _progress_projection(
    node: dict[str, Any],
    *,
    nodes_by_key: dict[str, dict[str, Any]],
    child_keys_by_parent: dict[str, list[str]],
) -> dict[str, Any] | None:
    """Derive Project/Issue progress from the canonical local Work graph."""

    if node["kind"] not in {"Project", "Issue"}:
        return None

    children = [
        nodes_by_key[key]
        for key in child_keys_by_parent.get(node["key"], [])
        if key in nodes_by_key
    ]
    if node["kind"] == "Issue":
        tracked = [child for child in children if child["kind"] == "Task"]
        basis = "child_tasks" if tracked else "self_status"
    else:
        issues = [child for child in children if child["kind"] == "Issue"]
        tracked = []
        for issue in issues:
            tasks = [
                nodes_by_key[key]
                for key in child_keys_by_parent.get(issue["key"], [])
                if key in nodes_by_key and nodes_by_key[key]["kind"] == "Task"
            ]
            tracked.extend(tasks or [issue])
        basis = "leaf_work" if tracked else "self_status"

    if not tracked:
        tracked = [node]
    status_counts = {
        status: sum(1 for item in tracked if item["status"] == status)
        for status in _WORK_STATUS_VALUES
    }
    completed = sum(status_counts[status] for status in _TERMINAL_STATUS_VALUES)
    total = len(tracked)
    percent = (completed * 100 + total // 2) // total
    return {
        "schema_version": WORK_PROGRESS_SCHEMA_VERSION,
        "basis": basis,
        "completed": completed,
        "total": total,
        "percent": percent,
        "status_counts": status_counts,
    }


def build_monitor_snapshot(storage: GraphStorage) -> dict[str, Any]:
    """Build a safe aggregate without credential or raw payload material."""

    from devgraph.arenas import ArenaRepository

    read_started_at = utc_now().isoformat()
    work_by_kind: Counter[str] = Counter()
    work_by_status: Counter[str] = Counter()
    activity: list[dict[str, str]] = []
    graph_nodes: list[dict[str, Any]] = []
    for node in storage.query("Arena", archived=None, limit=None):
        arena = ArenaRepository._from_node(node)
        status = "archived" if arena.archived else "active"
        activity.append(
            {
                "id": arena.id,
                "type": "arena",
                "label": "Arena",
                "title": redact_text(arena.title),
                "status": status,
                "timestamp": arena.updated_at,
            }
        )
        graph_nodes.append(
            _graph_node(
                storage_label="Arena",
                kind="Arena",
                node_id=arena.id,
                title=arena.title,
                status=status,
                category="arena",
                archived=arena.archived,
            )
        )
    for kind in MONITORED_WORK_KINDS:
        for node in storage.query(kind, archived=None, limit=None):
            work_by_kind[kind] += 1
            status = str(node.properties.get("status", "unknown"))
            work_by_status[status] += 1
            title = str(node.properties.get("title", node.id))
            activity.append(
                {
                    "id": node.id,
                    "type": "work",
                    "label": kind,
                    "title": redact_text(title),
                    "status": status,
                    "timestamp": str(node.properties.get("updated_at", "")),
                }
            )
            work_node = _graph_node(
                storage_label=kind,
                kind=kind,
                node_id=node.id,
                title=title,
                status=status,
                category="work",
                archived=node.archived,
            )
            # Decimal strings preserve canonical i64 versions in JavaScript clients.
            version = node.properties.get("version")
            work_node["version"] = str(version) if type(version) is int and version > 0 else None
            graph_nodes.append(work_node)

    observations = [
        node
        for node in storage.query(INITIATIVE_OBSERVATION_LABEL, archived=False, limit=None)
        if node.properties.get("role") == INITIATIVE_OBSERVATION_ROLE
    ]
    observation_by_status: Counter[str] = Counter()
    for node in observations:
        claim_status = str(node.properties.get("claim_status", "unknown"))
        observation_by_status[claim_status] += 1
        title = str(node.properties.get("title", node.id))
        activity.append(
            {
                "id": node.id,
                "type": "observation",
                "label": "Initiative observation",
                "title": redact_text(title),
                "status": claim_status,
                "timestamp": str(node.properties.get("observed_at", "")),
            }
        )
        graph_nodes.append(
            _graph_node(
                storage_label=INITIATIVE_OBSERVATION_LABEL,
                kind="InitiativeObservation",
                node_id=node.id,
                title=title,
                status=claim_status,
                category="observation",
                archived=node.archived,
            )
        )

    receipts = storage.query(EVENT_RECEIPT_LABEL, archived=False, limit=None)
    outbox_by_status: Counter[str] = Counter()
    for node in receipts:
        receipt_status = str(node.properties.get("status", "unknown"))
        outbox_by_status[receipt_status] += 1
        receipt_title = (
            f"{node.properties.get('subject_label', 'subject')} · "
            f"{node.properties.get('subject_id', node.id)}"
        )
        activity.append(
            {
                "id": node.id,
                "type": "receipt",
                "label": str(node.properties.get("operation", "mutation")),
                "title": redact_text(receipt_title),
                "status": receipt_status,
                "timestamp": str(node.properties.get("updated_at", "")),
            }
        )
        graph_nodes.append(
            _graph_node(
                storage_label=EVENT_RECEIPT_LABEL,
                kind=EVENT_RECEIPT_LABEL,
                node_id=node.id,
                title=receipt_title,
                status=receipt_status,
                category="receipt",
                archived=node.archived,
            )
        )

    graph_nodes.sort(key=lambda item: (item["category"], item["kind"], item["id"]))
    visible_graph_keys = {item["key"] for item in graph_nodes}
    stored_edges = storage.list_edges()
    graph_edges = [
        {
            "source": _graph_key(edge.from_label, edge.from_id),
            "target": _graph_key(edge.to_label, edge.to_id),
            "relationship": edge.relationship,
        }
        for edge in stored_edges
        if _graph_key(edge.from_label, edge.from_id) in visible_graph_keys
        and _graph_key(edge.to_label, edge.to_id) in visible_graph_keys
    ]
    graph_edges.sort(key=lambda item: (item["source"], item["relationship"], item["target"]))
    work_keys = {item["key"] for item in graph_nodes if item["category"] == "work"}
    unresolved = []
    for edge in stored_edges:
        source = _graph_key(edge.from_label, edge.from_id)
        target = _graph_key(edge.to_label, edge.to_id)
        if edge.relationship == "DEPENDS_ON":
            dependent, prerequisite = source, target
        elif edge.relationship == "BLOCKS":
            dependent, prerequisite = target, source
        else:
            continue
        if dependent in work_keys and prerequisite not in work_keys:
            # Report the affected visible Work without disclosing a hidden endpoint.
            unresolved.append({
                "work_key": dependent, "relationship": edge.relationship,
                "reason": "prerequisite is not available as canonical Work",
            })
    unresolved.sort(key=lambda item: (item["work_key"], item["relationship"]))
    nodes_by_key = {item["key"]: item for item in graph_nodes}
    child_keys_by_parent: dict[str, list[str]] = {}
    for edge in graph_edges:
        if edge["relationship"] == "HAS_CHILD":
            child_keys_by_parent.setdefault(edge["source"], []).append(edge["target"])
    for node in graph_nodes:
        progress = _progress_projection(
            node,
            nodes_by_key=nodes_by_key,
            child_keys_by_parent=child_keys_by_parent,
        )
        if progress is not None:
            node["progress"] = progress

    activity.sort(key=lambda item: (item["timestamp"], item["id"]), reverse=True)
    try:
        health = storage.health()
        storage_health = {
            "live": health.live,
            "ready": health.ready,
            "detail": redact_text(health.detail),
        }
    except Exception:  # pragma: no cover - defensive production seam
        storage_health = {
            "live": False,
            "ready": False,
            "detail": "storage health unavailable",
        }

    pending_receipts = sum(outbox_by_status[status] for status in ("pending", "retry_scheduled"))
    return {
        "generated_at": utc_now().isoformat(),
        "storage": storage_health,
        "total_work": sum(work_by_kind.values()),
        "active_initiatives": sum(
            1
            for node in storage.query("Initiative", archived=False, limit=None)
            if node.properties.get("status") != "archived"
        ),
        "observation_count": len(observations),
        "receipt_count": len(receipts),
        "pending_receipts": pending_receipts,
        "work_by_kind": dict(work_by_kind),
        "work_by_status": dict(work_by_status),
        "observation_by_status": dict(observation_by_status),
        "outbox_by_status": dict(outbox_by_status),
        "recent_activity": activity[:16],
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
        "selection_scope": {
            "schema": "devgraph.selection-scope.v1",
            "edge_scan": "all_stored_edges",
            "consistency": "assembled",
            "read_started_at": read_started_at,
            "read_finished_at": utc_now().isoformat(),
            "unresolved": unresolved,
        },
    }
