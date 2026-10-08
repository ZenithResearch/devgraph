"""Forward-only progress mapping, checked against the original graph and evidence."""

import json
from dataclasses import replace

from devgraph.model.repository import WorkObjectRepository
from devgraph.progress import TODO_KINDS, Progress, stage_progress
from devgraph.storage.base import NodeRecord, StorageUnavailable
from devgraph.workflow_contract import decode_state
from devgraph.workflows import Workflows


def plan_progress_migration(storage, records=None):
    supplied = records is not None
    records = list(records or [])
    if len(records) > 10000:
        raise StorageUnavailable("progress migration budget exceeded")
    for kind in () if supplied else TODO_KINDS:
        after = None
        while True:
            batch = storage.query(kind, archived=None, after_id=after, limit=100)
            records.extend(n for n in batch if n.properties.get("kind") == kind)
            if len(records) > 10000:
                raise StorageUnavailable("progress migration budget exceeded")
            if len(batch) < 100:
                break
            after = batch[-1].id
    engine = Workflows(storage)
    candidates, reasons, models = {}, {}, {}
    for node in records:
        key = f"{node.label}/{node.id}"
        props = {**node.properties, "progress_schema": 1}
        old = props.pop("progress", None)
        work = WorkObjectRepository._from_node(
            NodeRecord(node.label, node.id, props, node.archived)
        )
        models[key] = work
        reason = []
        state = decode_state(work.workflow_json) if work.workflow_json else None
        mapped = stage_progress(state.workflow_id, state.stage) if state else None
        if old is not None:
            # This migration does not overwrite an existing valid canonical classification.
            try:
                current = Progress(old)
                if mapped is not None and current != mapped:
                    raise ValueError()
                mapped = current
            except ValueError:
                reason.append("Existing progress contradicts the recorded stage.")
                mapped = None
        elif state is None:
            reason.append("No workflow or progress evidence; legacy status alone is insufficient.")
        if state and state.stage == "backlog" and state.reviews:
            reason.append("Backlog retains review evidence; classification needs review.")
            mapped = None
        candidates[key], reasons[key] = mapped, reason
        engine._work_cache[(work.kind, work.id)] = replace(work, progress=mapped)
    # Resolve in passes so parents cannot inherit completion from a child whose
    # own completion evidence failed validation later in the traversal.
    changed = True
    while changed:
        changed = False
        for key, work in models.items():
            if candidates[key] is None:
                continue
            state = decode_state(work.workflow_json) if work.workflow_json else None
            if candidates[key] == Progress.DONE and (
                work.kind == "Todo"
                or (work.kind == "Proposal" and engine.disposition(work) == "rejected")
            ):
                from devgraph.progress_mutations import completion_record_gaps

                gaps = completion_record_gaps(engine, work)
            elif state:
                gaps = engine.gaps(replace(work, archived=False), state, state.stage)
            elif candidates[key] == Progress.DONE:
                gaps = ["Subtype completion has no workflow or applicable gate evidence."]
            else:
                gaps = []
            if gaps:
                candidates[key] = None
                reasons[key].extend(gaps)
                engine._work_cache[(work.kind, work.id)] = replace(work, progress=None)
                engine._basis_cache.clear()
                changed = True
    result = []
    for node in records:
        key = f"{node.label}/{node.id}"
        historical = {k: node.properties.get(k) for k in ("status", "workflow_json", "progress")}
        historical["archived"] = node.archived
        result.append(
            dict(
                key=key,
                kind=node.label,
                id=node.id,
                expected_version=node.properties["version"],
                progress=candidates[key],
                archived=node.archived,
                reasons=reasons[key],
                progress_migration_json=json.dumps(
                    {
                        "schema": "devgraph.progress-migration.v1",
                        "source": historical,
                        "reasons": reasons[key],
                    },
                    sort_keys=True,
                ),
            )
        )
    return result


def migration_report(plan):
    return {
        "schema": "devgraph.progress-migration-report.v1",
        "total": len(plan),
        "mapped": {p.value: sum(row["progress"] == p for row in plan) for p in Progress},
        "unresolved": [
            {"key": row["key"], "reasons": row["reasons"]}
            for row in plan
            if row["progress"] is None
        ],
    }
