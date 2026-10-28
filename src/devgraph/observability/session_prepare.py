"""Prepare an exact signed request from observed metadata; never sign or grant."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from devgraph.observability.session_ref import SessionIdentity, work_ref
from devgraph.work_requests import WorkRequest

BASIS = {
    "successful_cli_work_read": "inspected",
    "inspected_work": "inspected",
    "declared_dispatch_scope": "declared_scope",
}


def prepare_state(reader, ref_id, state):
    from devgraph.observability.session_reads import REF_ID

    if not REF_ID.fullmatch(ref_id):
        raise ValueError("invalid_session_reference")
    # A native record is not required: DevGraph owns this disposition even if
    # the source app deleted its retained conversation or is currently offline.
    canonical = reader.graph_reader("/session-refs/v1/" + ref_id)
    if not canonical or canonical.get("canonical") is not True:
        raise ValueError("canonical_session_required")
    current = canonical.get("work_state")
    if not current:
        raise ValueError("session_state_host_upgrade_required")
    if current["state"] == state:
        raise ValueError("session_work_state_unchanged")
    return WorkRequest.from_json(
        json.dumps(
            {
                "schema": "devgraph.work-request.v2",
                "operation": "session.state",
                "kind": "SessionRef",
                "id": ref_id,
                "expected_version": current["version"],
                "payload": {"state": state},
            }
        ).encode()
    )


def prepare(reader, ref_id, work, *, link_ref=None):
    kind, node_id = work_ref(work)
    detail = reader.detail(ref_id)
    if not detail or detail["source_resolution"] != "available":
        raise ValueError("source_session_not_resolved")
    bindings = [
        b
        for b in detail["related_work"]
        if b["key"] == work
        and b.get("basis") in BASIS
        and b.get("resolution") == "available"
        and b.get("observed_at")
    ]
    if not bindings:
        raise ValueError("no_observed_work_association")
    binding = max(bindings, key=lambda b: (BASIS[b["basis"]] == "inspected", b["observed_at"]))
    identity = SessionIdentity(
        **{k: detail[k] for k in ("application", "source_instance_id", "external_session_id")}
    )
    payload = {
        "session": {
            k: getattr(identity, k)
            for k in ("application", "source_instance_id", "external_session_id")
        },
        "basis": BASIS[binding["basis"]],
        "observed_at": binding["observed_at"],
    }
    if link_ref is not None:
        links = [link for link in detail["links"] if link["id"] == link_ref and link.get("witness")]
        if len(links) != 1:
            raise ValueError("source_link_not_resolved")
        other = reader.detail(link_ref)
        if not other or other["source_resolution"] != "available":
            raise ValueError("source_link_not_resolved")
        payload["link"] = {
            key: links[0]["witness"][key]
            for key in (
                "source_instance_id",
                "event_id",
                "attempt_id",
                "relation",
                "origin",
                "target",
                "observed_at",
                "basis",
            )
        }
    value = {
        "schema": "devgraph.work-request.v2",
        "operation": "session.attach",
        "kind": kind,
        "id": node_id,
        "expected_version": binding["version"],
        "payload": payload,
    }
    return WorkRequest.from_json(json.dumps(value).encode())


def write_bundle(request, directory: Path):
    # A fresh private directory makes uncertain outcomes inspectable, never overwritten.
    directory = directory.absolute()
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError("unsafe_import_directory")
    directory.mkdir(mode=0o700)
    values = {
        "request.json": request.canonical,
        "idempotency.txt": (uuid4().hex + "\n").encode(),
        "review.json": (
            json.dumps(
                {
                    "schema": "devgraph.session-import-review.v1",
                    "authority": False,
                    "operation": request.authority_operation,
                    **(
                        {"session_work_state": request.payload["state"]}
                        if request.operation == "session.state"
                        else {}
                    ),
                    "resources": list(request.resources),
                    "expected_version": request.expected_version,
                    "request_digest_sha256": request.digest,
                    "scope": "All listed resources must be admitted together. No grant is created.",
                    "retry": "Reuse request and idempotency files after an uncertain submission.",
                },
                indent=2,
            )
            + "\n"
        ).encode(),
    }
    for name, content in values.items():
        fd = os.open(directory / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
    return {
        "prepared": True,
        "submitted": False,
        "operation": request.authority_operation,
        "resources": list(request.resources),
    }
