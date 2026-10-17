"""Offline operator review tools. No signing, grant installation, or network I/O."""

import os
import secrets
import stat
from pathlib import Path

from devgraph.auth.access_policy import (
    AccessDraft,
    MemberAccessPolicy,
    attenuate,
    compile_read,
    compile_write,
    require_subset,
)
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.monitor_read import AccessDraftV2, MemberAccessPolicyV2, compile_monitor_read
from devgraph.signed_read_runtime import private_json


def _publish(path: Path, value):
    if not path.is_absolute():
        raise ValueError("absolute output path required")
    # Resolve the already existing directory, then exclusively create one private
    # file. Never overwrite an approved selection or follow a file symlink.
    directory = path.parent.resolve(strict=True)
    info = directory.stat()
    if info.st_uid != os.geteuid() or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("owner-private output directory required")
    raw = canonical_json(value)
    if len(raw) > 65536:
        raise ValueError("access plan too large")
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    temporary = ".devgraph-access-" + secrets.token_hex(16)
    staged, published = False, False
    try:
        output = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        staged = True
        with os.fdopen(output, "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        # Link a complete private file exclusively into its final name. An old
        # reviewed file is never overwritten, and readers cannot see partial JSON.
        os.link(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        published = True
        os.unlink(temporary, dir_fd=fd)
        staged = False
        os.fsync(fd)
    except BaseException:
        if published:
            os.unlink(path.name, dir_fd=fd)
        raise
    finally:
        if staged:
            os.unlink(temporary, dir_fd=fd)
        os.close(fd)


def _draft(raw):
    model = AccessDraftV2 if raw.get("schema") == "devgraph.access-draft.v2" else AccessDraft
    return model.model_validate(raw)


def _matching(child, parent):
    if child.schema_ != parent.schema_:
        raise ValueError("explicit matching access contract versions required")
    require_subset(child, parent)


def execute(args):
    action = args.auth_access_command
    if action == "registry":
        ceiling = _draft(private_json(args.ceiling_file))
        modern = isinstance(ceiling, AccessDraftV2)
        model = MemberAccessPolicyV2 if modern else MemberAccessPolicy
        value = model.model_validate(
            {
                "schema": "devgraph.member-access-policy.v2"
                if modern
                else "devgraph.member-access-policy.v1",
                "revision": args.revision,
                "ceiling": ceiling,
                "assignments": [_draft(private_json(path)) for path in args.assignment_file],
            }
        )
        _publish(args.output_file, value)
        return {
            "schema": "devgraph.access-plan-result.v1",
            "authority_changed": False,
            "assignments": len(value.assignments),
        }
    if action == "attenuate":
        parent = _draft(private_json(args.parent_file))
        raw = private_json(args.selection_file)
        if raw.get("previous_digest") not in (None, parent.digest):
            raise ValueError("previous draft mismatch")
        raw["previous_digest"] = parent.digest
        child = _draft(raw)
        _matching(child, parent)
        draft = attenuate(parent, child)
        _publish(args.output_file, draft)
    else:
        draft = _draft(private_json(args.draft_file))
        if action == "export":
            ceiling = _draft(private_json(args.ceiling_file))
            _matching(draft, ceiling)
            if args.format == "read":
                if draft.subject_public_key is None or draft.installation_id is None:
                    raise ValueError("read issuance needs an exact member and installation")
                value = (
                    compile_monitor_read(draft)
                    if isinstance(draft, AccessDraftV2)
                    else compile_read(draft)
                )
            else:
                value = compile_write(
                    draft, policy_id=args.policy_id, policy_version=args.policy_version
                )
            # A bare enrollment/Work policy omits member/device/time or server
            # setup bindings. Export a review plan so an old provisioning command
            # cannot accidentally activate it as global authority.
            _publish(
                args.output_file,
                {
                    "schema": (
                        f"devgraph.member-{args.format}-plan.v"
                        f"{2 if isinstance(draft, AccessDraftV2) else 1}"
                    ),
                    "draft": draft.model_dump(mode="json", by_alias=True, exclude_defaults=True),
                    "ceiling_digest": ceiling.digest,
                    "policy": (
                        value.model_dump(mode="json", by_alias=True, exclude_defaults=True)
                        if args.format == "read"
                        else value
                    ),
                    "activation": "separate_operator_review_required",
                },
            )
    return {
        "schema": "devgraph.access-plan-result.v1",
        "authority_changed": False,
        "draft_digest": draft.digest,
        "draft": draft.model_dump(mode="json", by_alias=True, exclude_defaults=True),
        "next_step": "review_and_explicit_operator_activation",
    }
