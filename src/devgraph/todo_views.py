"""Canonical six-kind Todo reads with independent archive and progress filters."""

import json
from dataclasses import dataclass
from urllib.parse import parse_qs

from devgraph.kanban import BoardChanged, InvalidBoardFilter
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.validation import validate_work_object_id
from devgraph.policy.redaction import redact_text
from devgraph.progress import TODO_KINDS, Progress, progress_label
from devgraph.storage.base import StorageUnavailable
from devgraph.workflow_contract import decode_state
from devgraph.workflows import Workflows, fingerprint


@dataclass(frozen=True)
class TodoFilters:
    kind: str = ""
    progress: str = ""
    archived: str = "exclude"
    classification: str = ""
    q: str = ""
    limit: int = 30
    after: str = ""
    revision: str = ""

    @classmethod
    def parse(cls, query):
        try:
            values = parse_qs(query, keep_blank_values=True, max_num_fields=9, strict_parsing=True)
            if (
                len(query) > 4096
                or set(values) - cls.__dataclass_fields__.keys()
                or any(len(v) != 1 for v in values.values())
            ):
                raise ValueError()
            fields = {k: v[0] for k, v in values.items()}
            if "limit" in fields:
                fields["limit"] = int(fields["limit"])
            result = cls(**fields)
            if (
                result.kind not in ("", *TODO_KINDS)
                or result.progress not in ("", *Progress)
                or result.archived not in ("exclude", "include", "only")
                or result.classification not in ("", "required")
                or not 1 <= result.limit <= 100
                or len(result.q) > 200
                or any(ord(c) < 32 for c in result.q)
                or bool(result.after) != bool(result.revision)
            ):
                raise ValueError()
            if result.after:
                kind, identifier = result.after.split("/", 1)
                if kind not in TODO_KINDS:
                    raise ValueError()
                validate_work_object_id(identifier)
            return result
        except (ValueError, TypeError):
            raise InvalidBoardFilter("invalid_todo_filter") from None


def load_todos(storage):
    result = []
    for kind in TODO_KINDS:
        after = None
        while True:
            nodes = storage.query(kind, archived=None, limit=100, after_id=after)
            for node in nodes:
                if kind == "Todo" and node.properties.get("kind") != "Todo":
                    continue
                result.append(WorkObjectRepository._from_node(node))
            if len(result) > 10000:
                raise StorageUnavailable("Todo read budget exceeded")
            if len(nodes) < 100:
                break
            after = nodes[-1].id
    return result


def todo_summary(work, *, parent=None, description=False):
    state = decode_state(work.workflow_json) if work.workflow_json else None
    result = dict(
        key=f"{work.kind}/{work.id}",
        kind=work.kind,
        id=work.id,
        title=redact_text(work.title),
        priority=str(work.priority),
        version=str(work.version),
        progress=work.progress,
        progress_label=progress_label(work.progress),
        archived=work.archived,
        classification_required=work.progress is None,
        workflow_id=state.workflow_id if state else None,
        stage=state.stage if state else None,
        parent=parent,
        created_at=work.created_at.isoformat(),
        updated_at=work.updated_at.isoformat(),
    )
    if description:
        result["description"] = redact_text(work.description)
        result["artifact_ids"] = list(work.artifact_ids)
        result["external_link_ids"] = list(work.external_link_ids)
    return result


def todo_page(storage, filters):
    work = load_todos(storage)
    engine = Workflows(storage)
    parents = {}
    for edge in engine.edges("HAS_CHILD"):
        key = f"{edge.to_label}/{edge.to_id}"
        if key in parents:
            raise StorageUnavailable("ambiguous Todo parentage")
        parents[key] = f"{edge.from_label}/{edge.from_id}"
    revision = fingerprint(
        (
            [(w.kind, w.id, w.version, w.progress, w.archived, w.workflow_json) for w in work],
            sorted(parents.items()),
        )
    )
    if filters.revision and filters.revision != revision:
        raise BoardChanged("The Todo list changed; refresh before continuing.")
    rows = [
        w
        for w in work
        if (not filters.kind or w.kind == filters.kind)
        and (not filters.progress or w.progress == filters.progress)
        and (not filters.classification or w.progress is None)
        and (filters.archived == "include" or w.archived == (filters.archived == "only"))
        and filters.q.casefold() in f"{redact_text(w.title)} {w.id}".casefold()
    ]
    rows.sort(key=lambda w: (-w.priority, w.kind, w.id))
    keys = [f"{w.kind}/{w.id}" for w in rows]
    start = 0
    if filters.after:
        if filters.after not in keys:
            raise InvalidBoardFilter("cursor_outside_todo_filter")
        start = keys.index(filters.after) + 1
    selected = rows[start : start + filters.limit]
    return dict(
        schema="devgraph.todos.v2",
        revision=revision,
        total=len(rows),
        items=[todo_summary(w, parent=parents.get(f"{w.kind}/{w.id}")) for w in selected],
        next_cursor=keys[start + len(selected) - 1] if start + len(selected) < len(rows) else None,
        classification_required=sum(w.progress is None for w in work),
        complete=True,
    )


def todo_detail(storage, kind, identifier):
    if kind not in TODO_KINDS:
        raise ValueError("invalid_todo_kind")
    work = WorkObjectRepository(storage).get_by_id(kind, identifier)
    parents = Workflows(storage).related("HAS_CHILD", work, incoming=True)
    if len(parents) > 1:
        raise StorageUnavailable("ambiguous Todo parentage")
    parent = f"{parents[0].from_label}/{parents[0].from_id}" if parents else None
    return todo_summary(work, parent=parent, description=True)


def classification_report(storage):
    work = load_todos(storage)
    unresolved = []
    for item in work:
        if item.progress is not None:
            continue
        history = json.loads(item.progress_migration_json) if item.progress_migration_json else {}
        unresolved.append(
            {
                "key": f"{item.kind}/{item.id}",
                "reasons": history.get("reasons")
                or ["No canonical progress classification; legacy status alone is insufficient."],
            }
        )
    return {
        "schema": "devgraph.progress-migration-report.v1",
        "total": len(work),
        "mapped": {p.value: sum(w.progress == p for w in work) for p in Progress},
        "unresolved": unresolved,
    }
