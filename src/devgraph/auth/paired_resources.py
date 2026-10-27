"""Installed DevGraph adapter for shared secS compound-resource checks.

Existing native admission and receiver constraints remain mandatory. One whole
receiver rule must cover a mutation; never pool its source/destination across
separate rules. No caller-provided contract, ancestry, or policy is accepted.
"""

from dataclasses import dataclass

from devgraph.auth.secs_work import SecSWorkDenied
from devgraph.auth.selector_grants import snapshot
from devgraph.auth.write_scope import _inside
from devgraph.progress import TODO_KINDS


def effects(storage, request):
    """Resolve sources from current storage. Payload references are preconditions."""
    subject = (request.kind, request.id)
    related = [tuple(r.split("/", 1)) for r in request.resources if r != "/".join(subject)]
    source, destination, affected = [], [], []
    payload = request.payload
    if request.operation in ("parent.set", "member.set"):
        item = storage.work_containment(*subject)
        if item is None or len(item.parents) + len(item.memberships) > 1:
            raise SecSWorkDenied("ambiguous_effect_source")
        source = [(e.from_label, e.from_id) for e in (*item.parents, *item.memberships)]
        ref = payload.get("parent" if request.operation == "parent.set" else "arena")
        destination = [(ref.get("kind", "Arena"), ref["id"])] if ref else []
        # Include every current descendant, not a caller's declared inventory.
        all_edges = storage.list_edges("HAS_CHILD", limit=10001)
        if len(all_edges) > 10000:
            raise SecSWorkDenied("effect_capacity")
        keys = {subject}
        frontier = [subject]
        while frontier:
            edges = [e for e in all_edges if (e.from_label, e.from_id) in frontier]
            if len(edges) > 1000:
                raise SecSWorkDenied("effect_capacity")
            next_keys = set()
            for e in edges:
                if (e.from_label, e.from_id) in keys:
                    key = e.to_label, e.to_id
                    if key in keys:
                        raise SecSWorkDenied("ambiguous_effect_hierarchy")
                    next_keys.add(key)
            if len(keys) + len(next_keys) > 128:
                raise SecSWorkDenied("effect_capacity")
            keys.update(next_keys)
            frontier = sorted(next_keys)
        affected = sorted(keys - {subject})
    return {
        "subject": [subject],
        "related": related,
        "source": source,
        "destination": destination,
        "affected": affected,
    }


def _contract(service, operation):
    # Closed installed kind vocabulary includes the separately granted provenance
    # records. A selector cannot invent a resource kind or an operation handler.
    from devgraph.workflow_contract import RECORD_KINDS

    kinds = list(
        dict.fromkeys(
            [
                *TODO_KINDS,
                "Arena",
                "Decision",
                "ReviewPacket",
                "ExternalLink",
                *RECORD_KINDS.values(),
            ]
        )
    )
    return dict(
        service=service,
        operation=operation,
        roles=[
            dict(
                name=name,
                kinds=kinds,
                minimum=1 if name == "subject" else 0,
                maximum=1 if name in ("subject", "source", "destination") else 128,
            )
            for name in ("subject", "related", "source", "destination", "affected")
        ],
    )


def _selector(grant, service):
    kinds = list(dict.fromkeys(r.kind for r in grant.resources))
    fixed = all(r.ids is not None for r in grant.resources)
    selection = (
        {
            "mode": "objects",
            "refs": [dict(kind=r.kind, id=i) for r in grant.resources for i in r.ids],
        }
        if fixed
        else {"mode": "all"}
    )
    # Original per-kind IDs/Arena/path constraints are ALSO checked below. A mixed
    # fixed/all-kind selector is never approximated by dropping those constraints.
    return dict(
        schema="secs.resource-selector.v1", service=service, kinds=kinds, selection=selection
    )


def _matches(storage, grant, resolved, prospective):
    for refs in resolved.values():
        for kind, identifier in refs:
            if not any(
                r.kind == kind and (r.ids is None or identifier in r.ids) for r in grant.resources
            ):
                return False
            if (kind, identifier) in prospective:
                # A path cannot authorize an unplaced, not-yet-created object.
                if getattr(grant, "resource_paths", ()) or grant.arena_ids is not None:
                    return False
            elif not _inside(storage, kind, identifier, grant, {}):
                return False
    return True


def _check(host, storage, rule, contract, resolved, prospective):
    refs = [r for values in resolved.values() for r in values]
    state = snapshot(storage, host.service, refs)
    for kind, identifier in prospective:
        if storage.get_node(kind, identifier) is None:
            state["resources"].append(dict(resource=dict(kind=kind, id=identifier), ancestors=[]))
    checked = host.invoke(
        "check_effect",
        rule=rule,
        contract=contract,
        effects=[
            dict(role=name, resources=[dict(kind=k, id=i) for k, i in refs])
            for name, refs in resolved.items()
        ],
        snapshot=state,
    )
    return checked.get("allowed") is True


@dataclass(frozen=True)
class PairedGuard:
    repeat_admission_before_commit = True
    legacy: object
    host: object

    @property
    def summary(self):
        return {**self.legacy.summary, "resource_effect_contract": "devgraph.compound-effects.v1"}

    def prepare(self, storage, request):
        legacy = self.legacy.prepare(storage, request)
        resolved = effects(storage, request)
        # Only create-owned new IDs may be prospective. Existing-object mutation
        # requests cannot turn a missing reference into an authorization witness.
        prospective = set()
        for kind, identifier in [r for values in resolved.values() for r in values]:
            if storage.get_node(kind, identifier) is None:
                if request.operation not in (
                    "create",
                    "accept",
                    "convert",
                    "proposal.reject",
                    "progress.set",
                    "workflow.review",
                ):
                    raise SecSWorkDenied("missing_effect_resource")
                prospective.add((kind, identifier))
        contract = _contract(self.host.service, request.authority_operation)
        for grant in self.legacy.assignment.grants:
            if grant.operation != request.authority_operation or not _matches(
                storage, grant, resolved, prospective
            ):
                continue
            selector = _selector(grant, self.host.service)
            rule = dict(
                service=self.host.service,
                operation=request.authority_operation,
                roles=[dict(role=name, selector=selector) for name in resolved],
            )
            if _check(self.host, storage, rule, contract, resolved, prospective):
                return PairedMutation(
                    legacy, self.host, grant, rule, contract, resolved, prospective, request
                )
        raise SecSWorkDenied("compound_rule_not_granted")


@dataclass(frozen=True)
class PairedMutation:
    legacy: object
    host: object
    grant: object
    rule: dict
    contract: dict
    resolved: dict
    prospective: set
    request: object

    def check_after(self, storage):
        self.legacy.check_after(storage)
        post = effects(storage, self.request)
        # Retain the actual original source. Replacing it with the destination
        # after the move would authorize a different operation than was reviewed.
        post["source"] = self.resolved["source"]
        if (
            post != self.resolved
            or not _matches(storage, self.grant, post, set())
            or not _check(self.host, storage, self.rule, self.contract, post, set())
        ):
            raise SecSWorkDenied("compound_postcondition_denied")
