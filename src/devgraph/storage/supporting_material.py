"""Parent-scoped storage contract; no graph-wide expansion or arbitrary relationships."""

from __future__ import annotations

from dataclasses import dataclass

SUPPORTING_RELATIONSHIPS = {
    "HAS_ARTIFACT": "Artifact",
    "HAS_EXTERNAL_LINK": "ExternalLink",
    "HAS_REQUIREMENT": "Requirement",
    "HAS_ACCEPTANCE_CRITERION": "AcceptanceCriterion",
}
SUPPORTING_KINDS = frozenset(SUPPORTING_RELATIONSHIPS.values())
PUBLIC_WORK_KINDS = frozenset({"Proposal", "Initiative", "Project", "Issue", "Task"})
MAX_PROVENANCE = 100
SUPPORTING_PROPERTIES = frozenset({
    "kind", "schema", "schema_version", "title", "description", "role", "summary", "uri",
    "media_type", "url", "external_id", "status", "priority", "version",
})


@dataclass(frozen=True)
class SupportingReference:
    kind: str
    id: str
    paths: tuple[str, ...]
    paths_truncated: bool = False


def validate_supporting_read(
    label, node_id, artifact_ids, external_link_ids, limit, after_resource, target_resource
):
    # The storage protocol imports this module before model initialization.
    from devgraph.model.validation import validate_work_object_id

    if label not in PUBLIC_WORK_KINDS or type(limit) is not int or not 1 <= limit <= 101:
        raise ValueError("invalid_supporting_read")
    validate_work_object_id(node_id)
    for ids in (artifact_ids, external_link_ids):
        if not isinstance(ids, tuple):
            raise ValueError("invalid_supporting_references")
        for item in ids:
            validate_work_object_id(item)
    for resource in (after_resource, target_resource):
        if resource is not None:
            if not isinstance(resource, str) or resource.count("/") != 1:
                raise ValueError("invalid_supporting_resource")
            kind, item_id = resource.split("/")
            if kind not in SUPPORTING_KINDS:
                raise ValueError("invalid_supporting_resource")
            validate_work_object_id(item_id)


def validate_supporting_keys(keys):
    from devgraph.model.validation import validate_work_object_id

    if not isinstance(keys, list) or len(keys) > 100:
        raise ValueError("invalid_supporting_keys")
    for kind, item_id in keys:
        if kind not in SUPPORTING_KINDS:
            raise ValueError("invalid_supporting_keys")
        validate_work_object_id(item_id)
