"""Bounded, authenticated board projection with explicit current-state cursors."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs

from devgraph.model.repository import WorkObjectRepository
from devgraph.model.validation import validate_work_object_id
from devgraph.policy.redaction import redact_text
from devgraph.storage.base import StorageUnavailable
from devgraph.workflow_contract import COLUMNS, STAGES, WORK_KINDS, decode_state
from devgraph.workflows import Workflows, fingerprint


class InvalidBoardFilter(ValueError):
    pass


class BoardChanged(ValueError):
    pass


@dataclass(frozen=True)
class BoardFilter:
    kind: tuple[str, ...] = ()
    workflow: tuple[str, ...] = ()
    stage: tuple[str, ...] = ()
    column: tuple[str, ...] = ()
    scope: str = ""
    descendants: bool = False
    q: str = ""
    archived: str = "exclude"
    limit: int = 30
    after: str = ""
    revision: str = ""
    queue: str = ""
    order: str = "key"
    parentage: str = "any"

    @classmethod
    def parse(cls, query):
        try:
            if len(query) > 8192:
                raise ValueError
            values = parse_qs(query, keep_blank_values=True, max_num_fields=80)
            if set(values) - cls.__dataclass_fields__.keys():
                raise ValueError
            allowed = {
                "kind": WORK_KINDS,
                "workflow": (*STAGES, "unset"),
                "column": COLUMNS,
                "stage": ("unset", *{s for rows in STAGES.values() for s, _, _ in rows}),
            }
            fields = {}
            for key, items in values.items():
                if key in allowed:
                    if any(x not in allowed[key] for x in items):
                        raise ValueError
                    fields[key] = tuple(sorted(set(items)))
                else:
                    if len(items) != 1:
                        raise ValueError
                    fields[key] = items[0]
            if "descendants" in fields:
                if fields["descendants"] not in {"true", "false"}:
                    raise ValueError
                fields["descendants"] = fields["descendants"] == "true"
            if "limit" in fields:
                fields["limit"] = int(fields["limit"])
            result = cls(**fields)
            if not 1 <= result.limit <= 100 or len(result.q) > 200:
                raise ValueError
            if result.archived not in {"include", "exclude", "only"}:
                raise ValueError
            if result.queue not in {"", "not_started"} or result.order not in {"key", "priority"}:
                raise ValueError
            if result.parentage not in {"any", "standalone"}:
                raise ValueError
            for ref in (result.scope, result.after):
                if ref:
                    kind, identifier = ref.split("/", 1)
                    if kind not in WORK_KINDS:
                        raise ValueError
                    validate_work_object_id(identifier)
            if result.scope and result.scope.split("/")[0] not in {
                "Initiative",
                "Project",
                "Issue",
            }:
                raise ValueError
            if result.after and (len(result.column) != 1 or not result.revision):
                raise ValueError
            return result
        except (ValueError, TypeError):
            raise InvalidBoardFilter("invalid_board_filter") from None


def build_board(storage, filters: BoardFilter):
    work = []
    for kind in WORK_KINDS:
        after = None
        while True:
            nodes = storage.query(kind, archived=None, limit=100, after_id=after)
            if len(work) + len(nodes) > 10000:
                raise StorageUnavailable("board work budget exceeded")
            work.extend(WorkObjectRepository._from_node(n) for n in nodes)
            if len(nodes) < 100:
                break
            after = nodes[-1].id
    by_key = {f"{w.kind}/{w.id}": w for w in work}
    engine = Workflows(storage)
    engine._work_cache.update({(w.kind, w.id): w for w in work})
    children, parents = {}, {}
    for e in engine.edges("HAS_CHILD"):
        parent, child = f"{e.from_label}/{e.from_id}", f"{e.to_label}/{e.to_id}"
        if parent in by_key and child in by_key:
            if child in parents:
                raise StorageUnavailable("ambiguous board parentage")
            parents[child] = parent
            children.setdefault(parent, []).append(child)
    from devgraph.workflow_contract import PARENTS

    for child, parent in parents.items():
        if by_key[parent].kind not in PARENTS.get(by_key[child].kind, ()):
            raise StorageUnavailable("invalid board parentage")
    revision = fingerprint((sorted((key, w.version, w.workflow_json) for key, w in by_key.items()),
                            sorted(parents.items())))
    if filters.revision and filters.revision != revision:
        raise BoardChanged("Board changed; refresh before continuing.")
    scoped = None
    if filters.scope:
        if filters.scope not in by_key:
            from devgraph.model.repository import MissingWorkObjectError

            raise MissingWorkObjectError("Board scope no longer exists.")
        scoped = set(children.get(filters.scope, []))
        if filters.descendants:
            pending = list(scoped)
            while pending:
                for child in children.get(pending.pop(), []):
                    if child not in scoped:
                        scoped.add(child)
                        pending.append(child)
    grouped = {column: [] for column in COLUMNS}
    states = {
        key: decode_state(w.workflow_json) if w.workflow_json else None for key, w in by_key.items()
    }
    row_info = {
        workflow: {stage: (label, column) for stage, label, column in rows}
        for workflow, rows in STAGES.items()
    }
    blockers = {}
    for edge in engine.edges("BLOCKS"):
        key, blocking = f"{edge.to_label}/{edge.to_id}", f"{edge.from_label}/{edge.from_id}"
        if blocking in states and (states[blocking] is None or states[blocking].stage != "done"):
            blockers.setdefault(key, []).append(blocking)
    for key, w in by_key.items():
        state = states[key]
        stage, workflow = (state.stage, state.workflow_id) if state else ("unset", "unset")
        label, column = row_info[workflow][stage] if state else ("Stage not set", "backlog")
        if (
            filters.queue == "not_started"
            and (w.status.value == "archived" or not (
                stage == "backlog" or state is None and w.status.value == "draft"
            ))
            or scoped is not None
            and key not in scoped
            or filters.parentage == "standalone"
            and key in parents
            or filters.kind
            and w.kind not in filters.kind
            or filters.workflow
            and workflow not in filters.workflow
            or filters.stage
            and stage not in filters.stage
            or filters.column
            and column not in filters.column
            or filters.q.casefold() not in f"{redact_text(w.title)} {w.id}".casefold()
            or filters.archived == "exclude"
            and w.status.value == "archived"
            or filters.archived == "only"
            and w.status.value != "archived"
        ):
            continue
        grouped[column].append((key, w, state, label))
    columns = []
    for column, rows in grouped.items():
        rows.sort(key=(lambda row: (-row[1].priority, row[0]))
                  if filters.order == "priority" else lambda row: row[0])
        if filters.order == "priority" and filters.after and column in filters.column:
            keys = [row[0] for row in rows]
            if filters.after not in keys:
                raise InvalidBoardFilter("priority_cursor_outside_filter")
            available = rows[keys.index(filters.after) + 1:]
        else:
            available = (rows if filters.order == "priority"
                         else [r for r in rows if r[0] > filters.after])
        cards = []
        for key, w, state, label in available[: filters.limit]:
            child_keys = children.get(key, [])
            completed = sum(bool(states[c] and states[c].stage == "done") for c in child_keys)
            gaps = engine.completion_gaps(w, state) if state else ["Stage not set"]
            cards.append(
                {
                    "key": key,
                    "kind": w.kind,
                    "id": w.id,
                    "title": redact_text(w.title)[:240],
                    "version": str(w.version),
                    "priority": str(w.priority),
                    "lifecycle": w.status.value,
                    "workflow_id": state.workflow_id if state else None,
                    "stage": state.stage if state else None,
                    "stage_label": label,
                    "column": column,
                    "parent": parents.get(key),
                    "child_progress": {"done": completed, "total": len(child_keys)},
                    "blockers": blockers.get(key, []),
                    "evidence_gaps": gaps,
                    "blocking_reason": redact_text(state.blocking_reason) if state else "",
                    "transitions": engine.transitions(w),
                }
            )
        columns.append(
            {
                "id": column,
                "count": len(rows),
                "items": cards,
                "next_cursor": cards[-1]["key"] if len(available) > filters.limit else None,
            }
        )
    scopes = [
        {"key": k, "kind": w.kind, "title": redact_text(w.title)[:240]}
        for k, w in sorted(by_key.items())
        if w.kind in {"Initiative", "Project", "Issue"}
        and (filters.archived != "exclude" or w.status.value != "archived")
        and (not filters.q or filters.q.casefold() in f"{redact_text(w.title)} {w.id}".casefold()
             or k == filters.scope)
    ]
    visible_scopes = scopes[:200]
    selected = next((s for s in scopes if s["key"] == filters.scope), None)
    if selected and selected not in visible_scopes:
        visible_scopes[-1:] = [selected]
    return {
        "schema": "devgraph.kanban.v1",
        "revision": revision,
        "columns": columns,
        "total": sum(len(rows) for rows in grouped.values()),
        "scopes": visible_scopes,
        "scope_count": len(scopes),
        "scopes_truncated": len(scopes) > len(visible_scopes),
    }
