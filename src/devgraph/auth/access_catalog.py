"""Server-owned permission templates and explicitly published scope metadata.

Labels request no authority. The signed exact selection, current policy and native
admission still decide access. Public discovery never scans an unpublished root.
"""

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from devgraph.auth.access_operator import OperatorPolicy
from devgraph.auth.access_policy import WRITE_OPERATIONS, _Closed
from devgraph.auth.delegated_contract import WORK_OPERATION_ORDER, canonical_json
from devgraph.auth.path_access import PathAccessDraft
from devgraph.resource_paths import ResourcePath, current_resource_path, intersect_paths
from devgraph.storage.base import StorageUnavailable
from devgraph.storage.delegated import DelegatedWorkSelector


class AccessPreset(_Closed):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    name: str = Field(min_length=1, max_length=80)
    version: int = Field(strict=True, ge=1, le=9007199254740991)
    read_operations: tuple[str, ...] = Field(max_length=5)
    write_operations: tuple[str, ...] = Field(max_length=64)

    @model_validator(mode="after")
    def supported(self):
        if (
            not self.read_operations
            and not self.write_operations
            or len(set(self.read_operations)) != len(self.read_operations)
            or len(set(self.write_operations)) != len(self.write_operations)
            or set(self.read_operations) - set(WORK_OPERATION_ORDER)
            or set(self.write_operations) - WRITE_OPERATIONS
        ):
            raise ValueError("invalid permission template")
        return self


def _preset(identifier, name, operations):
    return AccessPreset(
        id=identifier,
        name=name,
        version=1,
        read_operations=("list", "get", "relationships"),
        write_operations=tuple("devgraph.work." + op + ".v2" for op in operations),
    )


CONTRIBUTOR = (
    "create",
    "patch",
    "progress.set",
    "dependency.add",
    "dependency.remove",
    "blocker.add",
    "blocker.remove",
)
DEFAULT_PRESETS = (
    _preset("contributor", "Contributor", CONTRIBUTOR),
    _preset(
        "project-manager",
        "Project Manager",
        (
            *CONTRIBUTOR,
            "parent.set",
            "workflow.assign",
            "workflow.transition",
            "archive",
            "restore",
        ),
    ),
)
# Deliberately absent: access administration, independent workflow.review,
# proposal acceptance/conversion/rejection, receipt forgery and delegation.


class PublishedPath(_Closed):
    resource_path: ResourcePath
    discover_descendants: bool = False


class CatalogOperatorPolicy(OperatorPolicy):
    schema_: Literal["devgraph.access-operator-policy.v3"] = Field(alias="schema")
    ceiling: PathAccessDraft
    presets: tuple[AccessPreset, ...] = Field(default=DEFAULT_PRESETS, max_length=16)
    published_paths: tuple[PublishedPath, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def catalog(self):
        if len({p.id for p in self.presets}) != len(self.presets) or len(
            {p.resource_path for p in self.published_paths}
        ) != len(self.published_paths):
            raise ValueError("duplicate preset or published path")
        return self


def catalog(policy):
    ceiling = policy.ceiling
    reads = {op for grant in ceiling.read_grants for op in grant.operations}
    writes = {g.operation for g in ceiling.write_grants}
    result = dict(
        schema="devgraph.access-catalog.v1",
        issuer=ceiling.issuer,
        receiver=ceiling.receiver,
        revision=policy.revision,
        presets=[
            dict(
                id=p.id,
                name=p.name,
                version=p.version,
                read_operations=[op for op in p.read_operations if op in reads],
                write_operations=[op for op in p.write_operations if op in writes],
            )
            for p in policy.presets
        ],
        published_paths=[p.model_dump(mode="json", by_alias=True) for p in policy.published_paths],
        policy_digest_sha256=policy.digest,
    )
    result["catalog_digest_sha256"] = hashlib.sha256(canonical_json(result)).hexdigest()
    return result


def validate_preset(draft, policy):
    ref = getattr(draft, "preset", None)
    if ref is None:
        return
    available = catalog(policy)
    preset = next(
        (p for p in available["presets"] if p["id"] == ref.id and p["version"] == ref.version), None
    )
    if (
        ref.catalog_digest_sha256 != available["catalog_digest_sha256"]
        or preset is None
        or any(set(g.operations) - set(preset["read_operations"]) for g in draft.read_grants)
        or any(g.operation not in preset["write_operations"] for g in draft.write_grants)
    ):
        raise ValueError("preset changed or selection exceeds the template")


def scope_inventory(storage, grants, *, published=None):
    if published is not None and not published:
        return []
    rows, seen = {}, set()

    def add(selected, node=None):
        key = selected.path[-1]
        if key in rows:
            return
        kind, identifier = key.split("/", 1)
        record = (
            node
            if node and (node.label, node.id) == (kind, identifier)
            else storage.get_node(kind, identifier)
        )
        if record is None or record.archived:
            return
        rows[key] = dict(
            resource_path=selected.model_dump(mode="json", by_alias=True),
            resource=key,
            title=str(record.properties.get("title", identifier)),
            parent=selected.path[-2] if len(selected.path) > 1 else None,
        )
        if len(rows) > 2000:
            raise StorageUnavailable("scope inventory capacity exceeded")

    with storage.work_mutation_transaction():
        if published is not None:
            for item in published:
                selected = item.resource_path
                kind, identifier = selected.path[-1].split("/", 1)
                chain = (
                    (selected.path[-1],)
                    if kind == "Arena"
                    else current_resource_path(storage, kind, identifier)
                )
                if chain is not None and all(anchor in chain for anchor in selected.path):
                    add(selected)
            discovery = tuple(p.resource_path for p in published if p.discover_descendants)
            if not discovery:
                return sorted(
                    rows.values(),
                    key=lambda row: (len(row["resource_path"]["path"]), row["resource"]),
                )
        else:
            discovery = ()
        for g in grants:
            if not set(g.operations) & {"list", "get"}:
                continue
            paths = intersect_paths(getattr(g, "resource_paths", ()), discovery)
            if discovery and not paths:
                continue
            for kind in ("Initiative", "Project"):
                if kind not in g.work_kinds:
                    continue
                selector = DelegatedWorkSelector(
                    work_ids=g.work_ids,
                    arena_ids=g.arena_ids,
                    work_kinds=(kind,),
                    include_archived=False,
                    resource_paths=paths,
                )
                after = None
                while True:
                    page = storage.delegated_work_page(
                        kind,
                        selectors=(selector,),
                        work_kinds=(kind,),
                        include_archived=False,
                        after_id=after,
                        limit=100,
                    )
                    for node in page:
                        if (kind, node.id) in seen:
                            continue
                        seen.add((kind, node.id))
                        if len(seen) > 10000:
                            raise StorageUnavailable("scope inventory capacity exceeded")
                        chain = current_resource_path(storage, kind, node.id)
                        if chain is None:
                            continue
                        anchors = tuple(
                            k
                            for k in chain
                            if k.split("/", 1)[0] in ("Arena", "Initiative", "Project")
                        )
                        if published is None:
                            for end in range(1, len(anchors) + 1):
                                add(
                                    ResourcePath.model_validate(
                                        dict(schema="devgraph.resource-path.v1", path=anchors[:end])
                                    ),
                                    node,
                                )
                        else:
                            for root in discovery:
                                if not all(anchor in anchors for anchor in root.path):
                                    continue
                                index = anchors.index(root.path[-1])
                                for end in range(index, len(anchors)):
                                    add(
                                        ResourcePath.model_validate(
                                            dict(
                                                schema="devgraph.resource-path.v1",
                                                path=(*root.path, *anchors[index + 1 : end + 1]),
                                            )
                                        ),
                                        node,
                                    )
                    if len(page) < 100:
                        break
                    after = page[-1].id
    return sorted(
        rows.values(), key=lambda row: (len(row["resource_path"]["path"]), row["resource"])
    )
