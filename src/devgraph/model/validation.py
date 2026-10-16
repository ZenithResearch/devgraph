from __future__ import annotations

import re
from datetime import datetime
from typing import Any

ID_GRAMMAR_VERSION = "ascii-lower-hyphen-v1"
SIGNED_64_MIN = -9223372036854775808
SIGNED_64_MAX = 9223372036854775807
CANONICAL_WORK_KINDS = (
    "Todo",
    "Proposal",
    "Initiative",
    "Project",
    "Issue",
    "Task",
    "Requirement",
    "AcceptanceCriterion",
    "Blocker",
    "Decision",
    "Handoff",
    "ReviewPacket",
    "Milestone",
)
CANONICAL_WORK_STATUSES = frozenset({"draft", "review", "accepted", "archived"})
CANONICAL_MODEL_PROPERTIES = frozenset(
    {
        "kind",
        "title",
        "description",
        "status",
        "created_at",
        "updated_at",
        "version",
        "artifact_ids",
        "external_link_ids",
        "priority",
    }
)
_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,254}[a-z0-9])?$", re.ASCII)


class InvalidWorkObjectId(ValueError):
    """Safe failure for a non-canonical application identifier."""


class NumericBoundError(ValueError):
    """Safe failure for a value outside the canonical signed-number contract."""


def validate_work_object_id(value: object) -> str:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise InvalidWorkObjectId("invalid_work_object_id")
    return value


def validate_work_object_id_collection(
    values: object, *, expected_type: type[list] | type[tuple]
) -> tuple[str, ...]:
    if not isinstance(values, expected_type):
        raise TypeError("invalid_identifier_collection")
    return tuple(validate_work_object_id(value) for value in values)


def validate_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= SIGNED_64_MAX:
        raise NumericBoundError("invalid_version")
    return value


def validate_page_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise NumericBoundError("invalid_page_limit")
    return value


def validate_priority(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not SIGNED_64_MIN <= value <= SIGNED_64_MAX
    ):
        raise NumericBoundError("invalid_priority")
    return value


def validate_canonical_work_object_properties(
    label: object,
    node_id: object,
    properties: object,
    *,
    archived: object,
    allow_unknown: bool = False,
) -> dict[str, Any]:
    if label not in CANONICAL_WORK_KINDS:
        raise ValueError("invalid_work_object_kind")
    validate_work_object_id(node_id)
    if not isinstance(properties, dict):
        raise ValueError("invalid_work_object_properties")
    property_names = set(properties)
    optional = {
        "workflow_json",
        "progress_schema",
        "progress",
        "progress_record_id",
        "progress_migration_json",
    }
    valid_property_names = (
        CANONICAL_MODEL_PROPERTIES.issubset(property_names)
        if allow_unknown
        else CANONICAL_MODEL_PROPERTIES.issubset(property_names)
        and property_names <= CANONICAL_MODEL_PROPERTIES | optional
    )
    if not valid_property_names:
        raise ValueError("invalid_work_object_properties")
    canonical = {key: properties[key] for key in CANONICAL_MODEL_PROPERTIES}
    if "workflow_json" in properties:
        from devgraph.workflow_contract import decode_state

        decode_state(properties["workflow_json"])
        canonical["workflow_json"] = properties["workflow_json"]
    from devgraph.progress import TODO_KINDS, validate_progress

    if any(key in properties for key in ("progress", "progress_schema", "progress_record_id")):
        if (
            label not in TODO_KINDS
            or type(properties.get("progress_schema")) is not int
            or properties["progress_schema"] != 1
        ):
            raise ValueError("invalid_progress_schema")
        canonical["progress_schema"] = 1
        if "progress" in properties:
            if properties["progress"] is None:
                raise ValueError("invalid_progress")
            validate_progress(properties["progress"], properties.get("workflow_json"))
            canonical["progress"] = properties["progress"]
        if "progress_record_id" in properties:
            validate_work_object_id(properties["progress_record_id"])
            canonical["progress_record_id"] = properties["progress_record_id"]
    if "progress_migration_json" in properties:
        import json

        value = properties["progress_migration_json"]
        if (
            not isinstance(value, str)
            or len(value) > 65536
            or not isinstance(json.loads(value), dict)
        ):
            raise ValueError("invalid_progress_migration")
        canonical["progress_migration_json"] = value
    if canonical.get("kind") != label:
        raise ValueError("invalid_work_object_kind")
    if not isinstance(canonical.get("title"), str) or not isinstance(
        canonical.get("description"), str
    ):
        raise ValueError("invalid_work_object_content")
    status = canonical.get("status")
    if not isinstance(status, str) or status not in CANONICAL_WORK_STATUSES:
        raise ValueError("invalid_work_object_status")
    for field_name in ("created_at", "updated_at"):
        timestamp = canonical.get(field_name)
        if not isinstance(timestamp, str):
            raise ValueError("invalid_work_object_timestamp")
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("invalid_work_object_timestamp")
    validate_version(canonical.get("version"))
    validate_priority(canonical.get("priority"))
    for field_name in ("artifact_ids", "external_link_ids"):
        validate_work_object_id_collection(canonical.get(field_name), expected_type=list)
    if not isinstance(archived, bool) or (
        "progress_schema" not in canonical and archived is not (status == "archived")
    ):
        raise ValueError("invalid_work_object_archive_state")
    return canonical
