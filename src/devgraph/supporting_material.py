"""Read attached metadata without writes, export authority, or document I/O."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from urllib.parse import urlsplit

from devgraph.model.base import WorkObject
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.validation import (
    CANONICAL_WORK_STATUSES,
    validate_page_limit,
    validate_priority,
    validate_version,
    validate_work_object_id,
)
from devgraph.policy.redaction import redact_text
from devgraph.storage.base import GraphStorage, NodeRecord
from devgraph.storage.supporting_material import PUBLIC_WORK_KINDS, SupportingReference
from devgraph.supporting_material_contract import (
    METADATA_FIELDS,
    SupportingMaterialEnvelope,
    SupportingMaterialItem,
    SupportingMaterialVia,
)


class SupportingMaterialCursorChanged(ValueError):
    def __init__(self):
        super().__init__("supporting_material_changed_restart_pagination")


def _binding(work: WorkObject, limit: int) -> dict:
    references = json.dumps([work.artifact_ids, work.external_link_ids], separators=(",", ":"))
    return {
        "v": 1,
        "work": work.kind + "/" + work.id,
        "version": work.version,
        "references": hashlib.sha256(references.encode()).hexdigest(),
        "limit": limit,
    }


def _decode_cursor(after: str | None, binding: dict) -> str | None:
    if after is None:
        return None
    try:
        if (
            not isinstance(after, str)
            or len(after) > 4096
            or re.fullmatch(r"[A-Za-z0-9_-]+", after) is None
        ):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(after + "=" * (-len(after) % 4)))
        if (
            not isinstance(data, dict)
            or set(data) != {*binding, "last"}
            or any(type(data[key]) is not type(value) for key, value in binding.items())
            or data["v"] != 1
            or data["work"] != binding["work"]
            or data["limit"] != binding["limit"]
        ):
            raise ValueError
        if data["version"] != binding["version"] or data["references"] != binding["references"]:
            raise SupportingMaterialCursorChanged
        resource = data["last"]
        if not isinstance(resource, str) or resource.count("/") != 1:
            raise ValueError
        kind, item_id = resource.split("/")
        if kind not in METADATA_FIELDS:
            raise ValueError
        validate_work_object_id(item_id)
        return resource
    except SupportingMaterialCursorChanged:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("invalid_supporting_material_cursor") from None


def _encode_cursor(binding: dict, last: str) -> str:
    data = json.dumps({**binding, "last": last}, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(data.encode()).rstrip(b"=").decode()


def _safe_location(value: str, *, allow_file: bool) -> bool:
    if (
        len(value) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or redact_text(value) != value
    ):
        return False
    if not value:
        return True
    try:
        location = urlsplit(value)
        if location.username is not None or location.password is not None:
            return False
        if location.scheme in {"http", "https"}:
            return bool(location.hostname) and location.port != 0 and " " not in value
        if allow_file and location.scheme == "file":
            return (
                location.netloc in {"", "localhost"}
                and location.path.startswith("/")
                and not location.query
                and not location.fragment
            )
        return (
            allow_file
            and value.startswith("/")
            and not value.startswith("//")
            and not location.query
            and not location.fragment
        )
    except ValueError:
        return False


def _via(path: str) -> SupportingMaterialVia:
    prefix, value = path.split("/", 1)
    if prefix == "field" and value in {"artifact_ids", "external_link_ids"}:
        return SupportingMaterialVia(field=value)
    if prefix == "edge" and value in {
        "HAS_ARTIFACT",
        "HAS_EXTERNAL_LINK",
        "HAS_REQUIREMENT",
        "HAS_ACCEPTANCE_CRITERION",
    }:
        return SupportingMaterialVia(relationships=[value])
    if prefix == "requirement":
        validate_work_object_id(value)
        return SupportingMaterialVia(
            relationships=["HAS_REQUIREMENT", "HAS_ACCEPTANCE_CRITERION"], requirement_id=value
        )
    raise ValueError("invalid_supporting_provenance")


def _item(reference: SupportingReference, node: NodeRecord | None) -> SupportingMaterialItem:
    common = {
        "kind": reference.kind,
        "id": reference.id,
        "via": [_via(path) for path in reference.paths],
        "via_truncated": reference.paths_truncated,
    }
    if node is None:
        return SupportingMaterialItem(**common, resolution="missing", metadata=None, issues=[])
    properties = node.properties
    if reference.kind == "Artifact" and (
        properties.get("role") == "initiative_observation"
        or properties.get("schema") == "devgraph.initiative-observation.v0"
            or properties.get("schema_version") == "devgraph.initiative-observation.v0"
    ):
        return SupportingMaterialItem(
            **common,
            resolution="unsupported_profile",
            metadata=None,
            issues=["use_initiative_observation_reader"],
        )
    issues = []
    metadata = {}
    malformed = bool(properties.get("__supporting_invalid"))
    if malformed:
        issues.append("invalid_record")
    if type(node.archived) is bool:
        metadata["archived"] = node.archived
    else:
        issues.append("archived_invalid")
        malformed = True
    required = {"title"}
    if reference.kind in {"Requirement", "AcceptanceCriterion"}:
        required |= {"description", "status", "priority", "version"}
    for name in sorted(METADATA_FIELDS[reference.kind] - {"archived"}):
        if name not in properties:
            if name in required:
                issues.append(name + "_missing")
                malformed = True
            continue
        value = properties[name]
        try:
            if name == "priority":
                value = validate_priority(value)
            elif name == "version":
                value = validate_version(value)
            else:
                if not isinstance(value, str):
                    raise ValueError
                if name == "status" and value not in CANONICAL_WORK_STATUSES:
                    raise ValueError
                if name in {"uri", "url"}:
                    if not _safe_location(value, allow_file=name == "uri"):
                        issues.append(name + "_unsafe")
                        malformed = True
                        continue
                maximum = 65536 if name in {"description", "summary"} else 4096
                # Scrub before truncating, so truncation cannot expose a partial credential.
                safe = redact_text(value)
                if safe != value:
                    issues.append(name + "_redacted")
                if len(safe) > maximum:
                    issues.append(name + "_truncated")
                value = safe[:maximum]
            metadata[name] = value
        except (TypeError, ValueError):
            issues.append(name + "_invalid")
            malformed = True
    return SupportingMaterialItem(
        **common,
        resolution="malformed" if malformed else "available",
        metadata=metadata,
        issues=issues,
    )


class SupportingMaterialReader:
    def __init__(self, storage: GraphStorage, repository: WorkObjectRepository):
        self._storage = storage
        self._repository = repository

    def _work(self, kind: str, work_id: str) -> WorkObject:
        if kind not in PUBLIC_WORK_KINDS:
            raise ValueError("invalid_supporting_work_kind")
        validate_work_object_id(work_id)
        return self._repository.get_by_id(kind, work_id)

    def read(
        self, kind: str, work_id: str, *, limit: int = 50, after: str | None = None
    ) -> SupportingMaterialEnvelope:
        validate_page_limit(limit)
        work = self._work(kind, work_id)
        binding = _binding(work, limit)
        last = _decode_cursor(after, binding)
        references = self._storage.supporting_material_references(
            kind,
            work_id,
            artifact_ids=work.artifact_ids,
            external_link_ids=work.external_link_ids,
            after_resource=last,
            limit=limit + 1,
        )
        page = references[:limit]
        nodes = {
            (node.label, node.id): node
            for node in self._storage.supporting_material_nodes(
                [(reference.kind, reference.id) for reference in page]
            )
        }
        return SupportingMaterialEnvelope.model_validate(
            {
                "schema": "devgraph.work-supporting-material.v1",
                "work": {"kind": kind, "id": work_id, "version": work.version},
                "items": [
                    _item(reference, nodes.get((reference.kind, reference.id)))
                    for reference in page
                ],
                "next_cursor": _encode_cursor(binding, page[-1].kind + "/" + page[-1].id)
                if len(references) > limit
                else None,
                "content_access": "metadata_only",
            }
        )

    def attached_artifact(
        self, kind: str, work_id: str, artifact_id: str
    ) -> SupportingMaterialItem:
        validate_work_object_id(artifact_id)
        work = self._work(kind, work_id)
        references = self._storage.supporting_material_references(
            kind,
            work_id,
            artifact_ids=work.artifact_ids,
            external_link_ids=work.external_link_ids,
            target_resource="Artifact/" + artifact_id,
            limit=1,
        )
        if not references:
            raise KeyError("artifact_not_attached_to_work")
        nodes = self._storage.supporting_material_nodes([("Artifact", artifact_id)])
        return _item(references[0], nodes[0] if nodes else None)
