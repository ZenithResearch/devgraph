"""Protected local preparation and endorsement of an existing-owner Arena ceiling.

Preparation and endorsement alone apply no policy. The protected executor
applies only the exact verified endorsement and restarts its existing Wallet jobs.
The original bootstrap
registry, issuer keys and member assignments are never changed by this service.
"""

import hashlib
import json
import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from devgraph.auth.access_approval import WalletAccessChallenge, verify_wallet_approval
from devgraph.auth.access_policy import _Closed, _draft_validity
from devgraph.auth.arena_access import ArenaMemberPolicy, compile_arena_read
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.native_read import _seconds
from devgraph.auth.owner_arena_policy import (
    OwnerArenaUpgradeAction,
    arena_identifiers,
    checksum,
    upgraded_policy,
    verify_endorsement,
)
from devgraph.auth.signed_read import SecsReadAuthority
from devgraph.auth.standing_access import StandingMemberPolicy, StandingOperatorPolicy
from devgraph.ops import owner_install as files
from devgraph.signed_read_runtime import (
    ReadRuntimeConfiguration,
    owner_operator_policy,
    private_json,
)


class UpgradeRequest(_Closed):
    schema_: Literal["devgraph.access-operator-action.v10"] = Field(alias="schema")
    action: Literal["upgrade_arena_policy"]
    action_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_policy_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    arena_scope: Literal["selected", "all_current_and_future"] = "selected"
    create_arenas: bool = Field(default=False, strict=True)
    arena_read_ids: tuple[str, ...] = Field(min_length=1, max_length=256)

    @field_validator("arena_read_ids", mode="before")
    @classmethod
    def identifiers(cls, value):
        return arena_identifiers(value)


def wire(value):
    return json.loads(canonical_json(value))


class OwnerArenaUpgrade:
    def __init__(self, *, runtime_file, data_root, storage=None, home=None):
        self.runtime_file = Path(runtime_file)
        self.data_root = Path(data_root)
        self.storage = storage
        self.home = Path(home) if home is not None else Path.home()

    def _configuration(self):
        selected = ReadRuntimeConfiguration.model_validate(private_json(self.runtime_file))
        if selected.owner_arena_upgrade_file is not None:
            raise ValueError("owner Arena upgrade already configured")
        if selected.schema_ not in (
            "devgraph.signed-read-runtime.v9",
            "devgraph.signed-read-runtime.v10",
            "devgraph.signed-read-runtime.v11",
        ):
            raise ValueError("owner Arena upgrade is already configured or unsupported")
        selected_raw = owner_operator_policy(selected, data_root=self.data_root)
        primary_path = selected.owner_parent_runtime_file or self.runtime_file
        primary = ReadRuntimeConfiguration.model_validate(private_json(primary_path))
        if (
            primary.schema_
            not in ("devgraph.signed-read-runtime.v9", "devgraph.signed-read-runtime.v11")
            or primary.owner_bootstrap_database_file is None
        ):
            raise ValueError("original signed owner runtime required")
        original = owner_operator_policy(primary, data_root=self.data_root)
        review = private_json(primary.owner_install_review_file, maximum_bytes=262144)
        return (
            selected,
            StandingOperatorPolicy.model_validate(selected_raw),
            primary_path,
            primary,
            original,
            review,
        )

    def _plan(self, request):
        selected, policy, primary_path, primary, original, review = self._configuration()
        if request.expected_policy_digest_sha256 != policy.digest:
            raise ValueError("owner policy changed")
        changed = upgraded_policy(
            original,
            request.arena_read_ids,
            arena_scope=request.arena_scope,
            create_arenas=request.create_arenas,
        )
        directory = (
            primary.enrollment_database_file.parent / "arena-policy-upgrades" / request.action_id
        )
        endorsement_file = directory / "endorsement.json"
        paths = {primary_path, self.runtime_file}
        # Only current fixed native-RPC selections of this same registered graph.
        rpc = self.home / ".zenith/rpc/v1"
        if rpc.is_dir():
            for path in sorted(rpc.glob("*.json")):
                value = private_json(path)
                if value.get("resource_id") != review["resource_id"]:
                    continue
                candidate = Path(value["runtime"])
                cfg = ReadRuntimeConfiguration.model_validate(private_json(candidate))
                if cfg.owner_parent_runtime_file != primary_path and candidate != primary_path:
                    raise ValueError("registered owner runtime lineage changed")
                paths.add(candidate)
        changes = []
        receivers = []

        def change(path, value):
            before = files.read(path, maximum=262144)
            changes.append(
                dict(path=str(path), before_sha256=hashlib.sha256(before).hexdigest(), after=value)
            )

        for path in sorted(paths):
            cfg_raw = private_json(path)
            cfg = ReadRuntimeConfiguration.model_validate(cfg_raw)
            old_raw = owner_operator_policy(cfg, data_root=self.data_root)
            old = StandingOperatorPolicy.model_validate(old_raw)
            if (
                cfg.owner_install_review_file != primary.owner_install_review_file
                or cfg.owner_bootstrap_database_file != primary.owner_bootstrap_database_file
            ):
                raise ValueError("registered owner changed")
            new_raw = wire(changed)
            new_raw["ceiling"]["receiver"] = old.ceiling.receiver
            new = type(changed).model_validate(new_raw)
            member_raw = private_json(cfg.member_policy_file)
            members = StandingMemberPolicy.model_validate(member_raw)
            if members.ceiling != old.ceiling:
                raise ValueError("member policy ceiling changed")
            member_raw.update(
                schema="devgraph.member-access-policy.v6"
                if request.arena_scope == "all_current_and_future"
                else "devgraph.member-access-policy.v5",
                revision=members.revision + 1,
                ceiling=wire(new.ceiling),
            )
            if request.arena_scope == "all_current_and_future":
                from devgraph.auth.arena_access import OwnerArenaMemberPolicy

                OwnerArenaMemberPolicy.model_validate(member_raw)
            else:
                ArenaMemberPolicy.model_validate(member_raw)
            enrollment = compile_arena_read(new.ceiling)
            from devgraph.auth.path_access import compile_path_read

            previous = compile_path_read(old.ceiling)
            if canonical_json(previous) != canonical_json(private_json(cfg.enrollment_policy_file)):
                raise ValueError("read enrollment ceiling changed")
            authority = private_json(cfg.authority_policy_file)
            expected = SecsReadAuthority.policy_for(
                previous, key_id=authority["key_id"], public_key=authority["public_key"]
            )
            if authority != expected:
                raise ValueError("read authority changed")
            next_authority = SecsReadAuthority.policy_for(
                enrollment, key_id=authority["key_id"], public_key=authority["public_key"]
            )
            for field, value in (
                ("access_operator_policy_file", wire(new)),
                ("member_policy_file", member_raw),
                ("enrollment_policy_file", wire(enrollment)),
                ("authority_policy_file", next_authority),
            ):
                change(getattr(cfg, field), value)
            cfg_raw.update(
                schema="devgraph.signed-read-runtime.v11",
                owner_arena_upgrade_file=str(endorsement_file),
                owner_arena_administration=request.arena_scope == "all_current_and_future",
            )
            ReadRuntimeConfiguration.model_validate(cfg_raw)
            change(path, cfg_raw)
            receivers.append(old.ceiling.receiver)
        if len({c["path"] for c in changes}) != len(changes):
            raise ValueError("owner endpoints share mutable policy files")
        if self.storage is not None:
            from devgraph.arenas import ArenaRepository

            for identifier in request.arena_read_ids:
                ArenaRepository(self.storage).get(identifier)
        # Only the exact already-installed Wallet jobs using these runtime files.
        # No new listener, shell command, private API or database lifecycle action.
        import plistlib

        restarts = []
        agents = self.home / "Library/LaunchAgents"
        for path in sorted(agents.glob("ca.zenith.devgraph.*.plist")) if agents.is_dir() else ():
            raw = files.read(path, private=False)
            job = plistlib.loads(raw)
            if job.get("EnvironmentVariables", {}).get("DEVGRAPH_SIGNED_READ_CONFIG") not in {
                str(p) for p in paths
            }:
                continue
            label = job.get("Label", "")
            if (
                path.name != label + ".plist"
                or not label.startswith("ca.zenith.devgraph.")
                or "/" in label
            ):
                raise ValueError("installed Wallet service changed")
            restarts.append(
                dict(path=str(path), label=label, sha256=hashlib.sha256(raw).hexdigest())
            )
        proposal = dict(
            schema="devgraph.owner-arena-proposal.v1",
            action_id=request.action_id,
            resource_id=review["resource_id"],
            runtime_file=str(self.runtime_file),
            primary_runtime_file=str(primary_path),
            original_policy=original,
            owner_review_digest_sha256=checksum(review),
            selected_receiver=policy.ceiling.receiver,
            arena_read_ids=list(request.arena_read_ids),
            affected_receivers=sorted(receivers),
            changes=changes,
            restarts=restarts,
        )
        return directory, proposal, policy, changed, review

    def challenge(self, owner, device, value, *, now=None):
        import re

        request = UpgradeRequest.model_validate(value)
        directory, proposal, policy, changed, review = self._plan(request)
        current = _seconds(now)
        if (
            owner != review["owner_public_key"]
            or not isinstance(device, str)
            or not re.fullmatch("[0-9a-f]{32}", device)
            or not _draft_validity(policy.ceiling).contains(current)
        ):
            raise ValueError("current registered owner required")
        action = OwnerArenaUpgradeAction.model_validate(
            dict(
                schema="devgraph.access-operator-action.v10",
                action="upgrade_arena_policy",
                action_id=request.action_id,
                resource_id=review["resource_id"],
                owner_grant_id=checksum(review),
                primary_receiver=changed.ceiling.receiver,
                selected_receiver=policy.ceiling.receiver,
                affected_receivers=proposal["affected_receivers"],
                previous_primary_policy_digest_sha256=StandingOperatorPolicy.model_validate(
                    proposal["original_policy"]
                ).digest,
                previous_selected_policy_digest_sha256=policy.digest,
                new_primary_policy_digest_sha256=changed.digest,
                arena_scope=request.arena_scope,
                create_arenas=request.create_arenas,
                arena_read_ids=request.arena_read_ids,
                proposal_digest_sha256=checksum(proposal),
            )
        )
        challenge = dict(
            schema="castalia.access-request-challenge.v1",
            challenge_id=secrets.token_hex(16),
            nonce=secrets.token_urlsafe(32),
            issuer=policy.ceiling.issuer,
            receiver=policy.ceiling.receiver,
            subject_public_key=owner,
            installation_id=device,
            issued_at=current,
            expires_at=current + 120,
            operation="access.decide",
            details=dict(
                action=action.wire(),
                policy_revision=policy.revision,
                policy_digest_sha256=policy.digest,
            ),
        )
        WalletAccessChallenge.model_validate(challenge)
        if directory.exists():
            stored = private_json(directory / "challenge.json")
            if (
                private_json(directory / "proposal.json", maximum_bytes=262144) != proposal
                or stored["subject_public_key"] != owner
                or stored["installation_id"] != device
                or not stored["issued_at"] <= current < stored["expires_at"]
            ):
                raise ValueError("owner upgrade action changed or expired")
            return stored
        files.directory(directory)
        files.save(directory / "proposal.json", files.encoded(proposal))
        files.save(directory / "challenge.json", files.encoded(challenge))
        return challenge

    def endorse(self, challenge, signature, *, now=None):
        with self.action_lock(challenge["details"]["action"]["action_id"]):
            directory = self._directory(challenge["details"]["action"]["action_id"])
            if (directory / "cancelled.json").exists():
                raise ValueError("original upgrade was closed without applying")
            return self._endorse(challenge, signature, now=now)

    def _endorse(self, challenge, signature, *, now=None):
        action = OwnerArenaUpgradeAction.model_validate(challenge["details"]["action"])
        request = UpgradeRequest.model_validate(
            dict(
                schema="devgraph.access-operator-action.v10",
                action="upgrade_arena_policy",
                action_id=action.action_id,
                expected_policy_digest_sha256=action.previous_selected_policy_digest_sha256,
                arena_scope=action.arena_scope,
                create_arenas=action.create_arenas,
                arena_read_ids=action.arena_read_ids,
            )
        )
        directory, proposal, policy, changed, review = self._plan(request)
        if (
            checksum(proposal) != action.proposal_digest_sha256
            or private_json(directory / "proposal.json", maximum_bytes=262144) != proposal
            or private_json(directory / "challenge.json") != challenge
        ):
            raise ValueError("exact owner upgrade review required")
        current = _seconds(now)
        verify_wallet_approval(
            challenge,
            signature,
            issuer=policy.ceiling.issuer,
            receiver=policy.ceiling.receiver,
            current=current,
        )
        endorsement = dict(
            schema="devgraph.owner-arena-endorsement.v1",
            original_policy=proposal["original_policy"],
            challenge=challenge,
            signature=signature,
            approved_at=current,
        )
        verify_endorsement(endorsement, review=review, actual_policy=wire(changed))
        path = directory / "endorsement.json"
        if path.exists():
            stored = private_json(path, maximum_bytes=262144)
            if stored["challenge"] != challenge or stored["signature"] != signature:
                raise ValueError("owner upgrade action already endorsed")
        else:
            files.save(path, files.encoded(endorsement))
        return dict(
            schema="devgraph.owner-arena-upgrade-result.v1",
            action_id=action.action_id,
            proposal_digest_sha256=action.proposal_digest_sha256,
            status="approved_pending_local_apply",
            applied=False,
            member_grants_changed=False,
        )

    def _binding(self):
        """Read original owner binding even during an interrupted file transaction."""
        cfg = ReadRuntimeConfiguration.model_validate(private_json(self.runtime_file))
        primary_path = cfg.owner_parent_runtime_file or self.runtime_file
        primary = ReadRuntimeConfiguration.model_validate(private_json(primary_path))
        review = private_json(primary.owner_install_review_file, maximum_bytes=262144)
        from devgraph.auth.owner_bootstrap import active_owner

        active_owner(
            primary.owner_bootstrap_database_file,
            resource_id=review["resource_id"],
            grant_id=checksum(review),
            public_key=review["owner_public_key"],
        )
        if Path(review["graph_data_root"]) != self.data_root:
            raise ValueError("graph binding changed")
        return cfg, primary, review

    def information(self, owner):
        cfg, primary, review = self._binding()
        if owner != review["owner_public_key"]:
            return None
        policy = owner_operator_policy(cfg, data_root=self.data_root)
        if cfg.owner_arena_upgrade_file is not None:
            if cfg.owner_arena_administration:
                proof = private_json(cfg.owner_arena_upgrade_file, maximum_bytes=262144)
                return dict(
                    enabled=True,
                    policy_digest_sha256=checksum(policy),
                    arena_scope="all_current_and_future",
                    create_arenas=proof["challenge"]["details"]["action"]["create_arenas"],
                )
            return dict(
                enabled=True,
                arena_read_ids=[
                    identifier
                    for grant in policy["ceiling"]["read_grants"]
                    if grant["scope"] == "devgraph.arena.read"
                    for identifier in grant["arena_ids"]
                ],
            )
        return dict(
            enabled=False,
            schema="devgraph.access-operator-action.v10",
            policy_digest_sha256=StandingOperatorPolicy.model_validate(policy).digest,
        )

    def _directory(self, identifier):
        import re

        if not isinstance(identifier, str) or not re.fullmatch("[0-9a-f]{32}", identifier):
            raise ValueError("invalid upgrade identifier")
        _, primary, _ = self._binding()
        return primary.enrollment_database_file.parent / "arena-policy-upgrades" / identifier

    from contextlib import contextmanager

    @contextmanager
    def action_lock(self, identifier):
        import fcntl
        import os
        import stat

        directory = self._directory(identifier)
        files.directory(directory)
        fd = os.open(directory / ".action.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077
            ):
                raise ValueError("unsafe owner action lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def apply(self, action_id):
        """Resume only the exact signed transaction. Never infer a new change.

        Each file admits only its signed prior or target digest. Runtime manifests
        are switched last. A crash may temporarily deny access; it cannot widen a
        member assignment. Backups and the transaction journal are retained.
        """
        import fcntl
        import os

        directory = self._directory(action_id)
        lock = directory.parent / ".apply.lock"
        files.directory(lock.parent)
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            import stat

            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                raise ValueError("unsafe upgrade lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            proposal = private_json(directory / "proposal.json", maximum_bytes=262144)
            endorsement = private_json(directory / "endorsement.json", maximum_bytes=262144)
            action = OwnerArenaUpgradeAction.model_validate(
                endorsement["challenge"]["details"]["action"]
            )
            cfg, primary, review = self._binding()
            if (
                action.action_id != action_id
                or checksum(proposal) != action.proposal_digest_sha256
                or proposal["resource_id"] != review["resource_id"]
                or proposal["owner_review_digest_sha256"] != checksum(review)
            ):
                raise ValueError("signed upgrade proposal changed")
            if not _draft_validity(
                StandingOperatorPolicy.model_validate(proposal["original_policy"]).ceiling
            ).contains(_seconds(None)):
                raise ValueError("original owner policy expired")
            verify_endorsement(
                endorsement,
                review=review,
                actual_policy=wire(
                    upgraded_policy(
                        proposal["original_policy"],
                        action.arena_read_ids,
                        arena_scope=action.arena_scope,
                        create_arenas=action.create_arenas,
                    )
                ),
            )
            changes = proposal["changes"]
            if len({c["path"] for c in changes}) != len(changes):
                raise ValueError("duplicate upgrade destination")
            completed = directory / "result.json"
            if completed.exists():
                result = private_json(completed)
                # Member policy may legitimately evolve after a completed upgrade.
                # Verify owner signature/current policy instead of replaying old files.
                owner_operator_policy(cfg, data_root=self.data_root)
                return result
            for job in proposal.get("restarts", []):
                if (
                    hashlib.sha256(files.read(Path(job["path"]), private=False)).hexdigest()
                    != job["sha256"]
                ):
                    raise ValueError("Wallet service changed")
            for index, change in enumerate(changes):
                path = Path(change["path"])
                raw = files.read(path, maximum=262144)
                target = files.encoded(change["after"])
                if hashlib.sha256(raw).hexdigest() not in (
                    change["before_sha256"],
                    hashlib.sha256(target).hexdigest(),
                ):
                    raise ValueError("upgrade input changed; inspect retained transaction")
                backup = directory / "prior" / (str(index) + ".json")
                if (
                    hashlib.sha256(raw).hexdigest() == change["before_sha256"]
                    and not backup.exists()
                ):
                    files.save(backup, raw)
            started = directory / "started.json"
            if not started.exists():
                files.save(started, files.encoded(dict(proposal_digest_sha256=checksum(proposal))))
            # Enrollment and operator/member policy before manifests; all decisions
            # fail closed if interrupted between these individually atomic replaces.
            ordered = sorted(changes, key=lambda c: "secs_binary_sha256" in c["after"])
            for change in ordered:
                path = Path(change["path"])
                target = files.encoded(change["after"])
                raw = files.read(path, maximum=262144)
                if raw != target:
                    files.replace(path, target, change["before_sha256"])
            for change in changes:
                if files.read(Path(change["path"]), maximum=262144) != files.encoded(
                    change["after"]
                ):
                    raise ValueError("upgrade readback failed")
            current = ReadRuntimeConfiguration.model_validate(private_json(self.runtime_file))
            owner_operator_policy(current, data_root=self.data_root)
            for job in proposal.get("restarts", []):
                self._restart(job)
            result = dict(
                schema="devgraph.owner-arena-upgrade-result.v1",
                action_id=action_id,
                proposal_digest_sha256=checksum(proposal),
                status="active",
                applied=True,
                member_grants_changed=False,
                arena_read_ids=list(action.arena_read_ids),
            )
            files.save(completed, files.encoded(result))
            return result
        finally:
            os.close(fd)

    @staticmethod
    def _restart(job):
        import os
        import subprocess
        import time

        # Exact labels and immutable plist digests are included in the signed
        # proposal. A status recovery may repeat this bounded same-job restart.
        if (
            hashlib.sha256(files.read(Path(job["path"]), private=False)).hexdigest()
            != job["sha256"]
        ):
            raise ValueError("Wallet service changed")
        target = "gui/" + str(os.geteuid())
        stopped = subprocess.run(
            ["/bin/launchctl", "bootout", target + "/" + job["label"]],
            capture_output=True,
            timeout=10,
            env={},
        )
        if stopped.returncode != 0:
            state = subprocess.run(
                ["/bin/launchctl", "print", target + "/" + job["label"]],
                capture_output=True,
                timeout=5,
                env={},
            )
            if state.returncode == 0:
                raise ValueError("Wallet service stop unconfirmed")
        # launchd can acknowledge bootout before the removal has drained. A
        # same-job bootstrap may then return EIO even though the label is absent.
        # Wait for confirmed absence and retry only that exact signed plist;
        # never replace an unexpected loaded job or infer a different target.
        for attempt in range(20):
            state = subprocess.run(
                ["/bin/launchctl", "print", target + "/" + job["label"]],
                capture_output=True,
                timeout=5,
                env={},
            )
            if state.returncode == 113:
                break
            if state.returncode != 0 or attempt == 19:
                raise ValueError("Wallet service stop unconfirmed")
            time.sleep(0.25)
        for attempt in range(20):
            if (
                hashlib.sha256(files.read(Path(job["path"]), private=False)).hexdigest()
                != job["sha256"]
            ):
                raise ValueError("Wallet service changed")
            started = subprocess.run(
                ["/bin/launchctl", "bootstrap", target, job["path"]],
                capture_output=True,
                timeout=10,
                env={},
            )
            if started.returncode == 0:
                return
            state = subprocess.run(
                ["/bin/launchctl", "print", target + "/" + job["label"]],
                capture_output=True,
                timeout=5,
                env={},
            )
            if state.returncode != 113 or attempt == 19:
                raise ValueError("Wallet service restart unconfirmed")
            time.sleep(0.25)

    def status_challenge(self, owner, device, action, *, now=None):
        if set(action) != {"schema", "action", "action_id", "original_action_id"}:
            raise ValueError("invalid status selection")
        if (
            action["schema"] != "devgraph.access-operator-action.v10"
            or action["action"] != "arena_policy_status"
        ):
            raise ValueError("invalid status selection")
        directory = self._directory(action["original_action_id"])
        _, _, review = self._binding()
        original = private_json(directory / "challenge.json")
        if owner != review["owner_public_key"] or device != original["installation_id"]:
            raise ValueError("original owner installation required")
        import re

        if not re.fullmatch("[0-9a-f]{32}", action["action_id"]):
            raise ValueError("invalid status action")
        current = _seconds(now)
        value = dict(
            original,
            challenge_id=secrets.token_hex(16),
            nonce=secrets.token_urlsafe(32),
            issued_at=current,
            expires_at=current + 120,
            operation="access.status",
            details=dict(original["details"], action=action),
        )
        WalletAccessChallenge.model_validate(value)
        return value

    def status(self, challenge, signature, *, now=None):
        with self.action_lock(challenge["details"]["action"]["original_action_id"]):
            return self._status(challenge, signature, now=now)

    def _status(self, challenge, signature, *, now=None):
        action = challenge["details"]["action"]
        expected = self.status_challenge(
            challenge["subject_public_key"], challenge["installation_id"], action, now=now
        )
        verify_wallet_approval(
            challenge,
            signature,
            issuer=expected["issuer"],
            receiver=expected["receiver"],
            current=_seconds(now),
        )
        if challenge["operation"] != "access.status":
            raise ValueError("status proof required")
        directory = self._directory(action["original_action_id"])
        if not (directory / "endorsement.json").exists():
            if not (directory / "cancelled.json").exists():
                files.save(directory / "cancelled.json", files.encoded(dict(status="not_applied")))
            return dict(
                schema="devgraph.owner-arena-upgrade-result.v1",
                action_id=action["original_action_id"],
                status="not_applied",
                applied=False,
                member_grants_changed=False,
            )
        return self.apply(action["original_action_id"])


class OwnerArenaOperator:
    """Only composed at the protected local RPC boundary, never HTTP."""

    def __init__(self, operator, upgrade, commands=None):
        self.operator, self.upgrade, self.commands = operator, upgrade, commands

    def __getattr__(self, name):
        return getattr(self.operator, name)

    def parse_action(self, value):
        if value.get("schema") in (
            "devgraph.access-operator-action.v10",
            "devgraph.access-operator-action.v11",
        ):
            return value
        return self.operator.parse_action(value)

    def challenge(self, owner, device, action):
        if not isinstance(action, dict):
            return self.operator.challenge(owner, device, action)
        if action.get("schema") == "devgraph.access-operator-action.v11":
            if self.commands is None:
                raise ValueError("owner Arena commands unavailable")
            if action.get("action") == "arena_create_status":
                return self.commands.status_challenge(owner, device, action)
            return self.commands.challenge(owner, device, action)
        if action.get("action") == "arena_policy_status":
            return self.upgrade.status_challenge(owner, device, action)
        if "proposal_digest_sha256" in action:
            parsed = OwnerArenaUpgradeAction.model_validate(action)
            request = dict(
                schema=parsed.schema_,
                action=parsed.action,
                action_id=parsed.action_id,
                expected_policy_digest_sha256=parsed.previous_selected_policy_digest_sha256,
                arena_scope=parsed.arena_scope,
                create_arenas=parsed.create_arenas,
                arena_read_ids=list(parsed.arena_read_ids),
            )
            result = self.upgrade.challenge(owner, device, request)
            if result["details"]["action"] != action:
                raise ValueError("original review changed")
            return result
        return self.upgrade.challenge(owner, device, action)

    def execute(self, challenge, signature):
        action = challenge.get("details", {}).get("action", {})
        if action.get("schema") == "devgraph.access-operator-action.v11":
            if self.commands is None:
                raise ValueError("owner Arena commands unavailable")
            return (
                self.commands.status(challenge, signature)
                if action["action"] == "arena_create_status"
                else self.commands.execute(challenge, signature)
            )
        if action.get("schema") == "devgraph.access-operator-action.v10":
            if action.get("action") == "arena_policy_status":
                result = self.upgrade.status(challenge, signature)
                return dict(
                    schema="devgraph.access-operator-result.v2",
                    action_id=action["action_id"],
                    original_action_id=action["original_action_id"],
                    authority_issued=False,
                    status="reconciled",
                    result=dict(
                        result, schema="devgraph.access-operator-result.v2", authority_issued=False
                    ),
                )
            self.upgrade.endorse(challenge, signature)
            return dict(
                self.upgrade.apply(action["action_id"]),
                schema="devgraph.access-operator-result.v2",
                authority_issued=False,
            )
        result = self.operator.execute(challenge, signature)
        if "inbox" in result:
            information = self.upgrade.information(challenge["subject_public_key"])
            if information is not None:
                result["owner_arena_upgrade"] = information
        return result


def recover_local(configuration, request):
    """Status remains reachable when interrupted manifests deny app startup.

    The fixed native RPC verifier has already checked the outer selection and
    signature. Service bindings are independently checked again by shared secS.
    Only the original signed transaction can be resumed; no fresh policy action
    or application read is admitted through this recovery path.
    """
    operation, body = request["operation"], request["body"]
    runtime = ReadRuntimeConfiguration.model_validate(private_json(Path(configuration["runtime"])))
    provider = runtime.holder_grant_provider_runtime_file or Path(configuration["runtime"])
    service = OwnerArenaUpgrade(runtime_file=provider, data_root=configuration["data_root"])
    inner = body.get("request", {}) if operation.startswith("service_") else body
    action = (
        inner.get("action", {})
        if operation.endswith("challenge")
        else inner.get("challenge", {}).get("details", {}).get("action", {})
    )
    if (
        action.get("schema") != "devgraph.access-operator-action.v10"
        or action.get("action") != "arena_policy_status"
    ):
        return None
    cfg, _, review = service._binding()
    if operation.startswith("service_"):
        from devgraph.auth.holder_grants import invoke_native

        pin = {k: review["connection"]["authority"][k] for k in ("issuer", "key_id", "public_key")}
        pin["service"] = "devgraph:" + review["resource_id"]
        claims = invoke_native(
            runtime.holder_grant_binary,
            runtime.holder_grant_binary_sha256,
            pin,
            "verify_access_point",
            binding=body["service_access_point"],
            origin=configuration["receiver"],
            now=_seconds(None),
        )
        if claims["authority_receiver"] != private_json(cfg.enrollment_policy_file)["receiver"]:
            raise ValueError("canonical recovery receiver changed")
    if operation.endswith("challenge"):
        return service.status_challenge(
            inner["subject_public_key"], inner["installation_id"], action
        )
    if not operation.endswith("execute"):
        raise ValueError("status operation required")
    result = service.status(inner["challenge"], inner["signature"])
    return dict(
        schema="devgraph.access-operator-result.v2",
        action_id=action["action_id"],
        original_action_id=action["original_action_id"],
        authority_issued=False,
        status="reconciled",
        result=dict(result, schema="devgraph.access-operator-result.v2", authority_issued=False),
    )


def recovery_inspection(configuration):
    """Public route metadata only; a partial policy must not strand its journal."""
    runtime = ReadRuntimeConfiguration.model_validate(private_json(Path(configuration["runtime"])))
    provider = runtime.holder_grant_provider_runtime_file or Path(configuration["runtime"])
    service = OwnerArenaUpgrade(runtime_file=provider, data_root=configuration["data_root"])
    _, primary, review = service._binding()
    pending = primary.enrollment_database_file.parent / "arena-policy-upgrades"
    if not pending.is_dir():
        return None
    directories = sorted(pending.iterdir())
    if len(directories) > 256:
        raise ValueError("upgrade history requires inspection")
    verified = False
    for directory in directories:
        if (
            directory.name.startswith(".")
            or not (directory / "started.json").exists()
            or (directory / "result.json").exists()
        ):
            continue
        proof = private_json(directory / "endorsement.json", maximum_bytes=262144)
        proposal = private_json(directory / "proposal.json", maximum_bytes=262144)
        action = OwnerArenaUpgradeAction.model_validate(proof["challenge"]["details"]["action"])
        if (
            checksum(proposal) != action.proposal_digest_sha256
            or proposal["resource_id"] != review["resource_id"]
        ):
            raise ValueError("retained owner review changed")
        verify_endorsement(
            proof,
            review=review,
            actual_policy=wire(
                upgraded_policy(
                    proposal["original_policy"],
                    action.arena_read_ids,
                    arena_scope=action.arena_scope,
                    create_arenas=action.create_arenas,
                )
            ),
        )
        verified = True
    if not verified:
        return None
    result = dict(
        transport="local-secs-rpc",
        owner_registered=True,
        credential_administration=True,
        service_configuration=False,
        https_hosted_by_transfer=False,
        owner_policy_recovery_required=True,
    )
    if runtime.canonical_access_points:
        from types import SimpleNamespace

        from devgraph.api.canonical_service import access_point_binding
        from devgraph.auth.holder_grants import invoke_native

        pin = {k: review["connection"]["authority"][k] for k in ("issuer", "key_id", "public_key")}
        pin["service"] = "devgraph:" + review["resource_id"]
        host = SimpleNamespace(
            pin=pin,
            service=pin["service"],
            invoke=lambda operation, **fields: invoke_native(
                runtime.holder_grant_binary,
                runtime.holder_grant_binary_sha256,
                pin,
                operation,
                issuer_key_file=runtime.issuer_key_file,
                **fields,
            ),
        )
        result["service_access_point"] = access_point_binding(
            host,
            origin=configuration["receiver"],
            authority_receiver=private_json(
                ReadRuntimeConfiguration.model_validate(
                    private_json(Path(provider))
                ).enrollment_policy_file
            )["receiver"],
        )
    return result
