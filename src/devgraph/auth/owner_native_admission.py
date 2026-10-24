"""Owner-approved native admission, opt-in only; review never mutates authority.

The receiver's exact assignment remains the path/operation authority. Native
admission admits its holder to the selected named operations, preserving all old
rules and denies. A partial native dispatch cannot publish a member assignment;
it leaves a private journal and requires local reconciliation, never a retry.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from devgraph.auth.access_policy import require_subset
from devgraph.auth.delegated_contract import canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.standing_access import StandingNativePolicy
from devgraph.auth.standing_activation import StandingGrantProvisioning


def proposed_policy(policy, grant):
    """Minimal additional holder/operation/kind rules, without weakening denies."""
    if not isinstance(policy, StandingNativePolicy):
        raise UnauthenticatedError("owner_native_policy_upgrade_required")
    try:
        policy.require(grant)
        return policy
    except UnauthenticatedError:
        pass
    raw = policy.model_dump(mode="json", by_alias=True)
    actor = "pubkey:sha256:" + hashlib.sha256(bytes.fromhex(grant.subject_public_key)).hexdigest()
    from devgraph.auth.access_policy import _draft_validity

    validity = json.loads(_draft_validity(grant).to_json())
    for selected in grant.write_grants:
        for resource in selected.resources:
            for identifier in resource.ids or (None,):
                rule = dict(
                    actor_id=actor,
                    operation=selected.operation,
                    resource=resource.kind + "/" + (identifier or ""),
                    resource_match="exact" if identifier else "prefix",
                    effect="allow",
                    status="active",
                    validity=validity,
                )
                if rule not in raw["rules"]:
                    raw["rules"].append(rule)
    raw["policy_version"] += 1
    try:
        proposed = StandingNativePolicy.model_validate(raw)
        proposed.require(grant)  # Matching denies continue to override new allows.
    except (ValueError, UnauthenticatedError) as exc:
        raise UnauthenticatedError("owner_native_admission_denied") from exc
    return proposed


class OwnerNativeAdmission:
    """Fixed local native admin; never accepts an executable or signer over HTTP."""

    def __init__(self, *, data_root, state_directory, native_policy_file, admin_binary):
        self.data_root = Path(data_root)
        self.state = Path(state_directory)
        self.policy_file = Path(native_policy_file)
        self.admin = Path(admin_binary)
        home_native = Path.home() / "Library/Application Support/Zenith/secS"
        if (
            self.policy_file != home_native / "authority/devgraph.work.v1/receiver-policy.json"
            or self.admin != home_native / "bin/secs-devgraph-work-v1"
        ):
            raise ValueError("owner native admission requires fixed owner-local paths")

    def commit(self, policy, grant, actor, action_id):
        import fcntl
        import os
        import re
        import stat
        import subprocess
        import time

        from devgraph.ops.owner_install import (
            directory,
            encoded,
            invoke,
            object_file,
            read,
            replace,
            require,
            save,
            sha,
        )

        proposed = proposed_policy(policy, grant)
        if proposed.digest == policy.digest:
            return policy
        require(
            bool(re.fullmatch(r"[a-f0-9]{32}(?::write)?", action_id)),
            "invalid_owner_admission_action",
        )
        directory(self.state)
        fd = os.open(self.state / ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            require(
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.geteuid()
                and info.st_nlink == 1
                and info.st_mode & 0o077 == 0,
                "unsafe_native_admission_lock",
            )
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Any unresolved prior dispatch stops further native changes. Its
            # assignment may have rolled back; inspect rather than guessing.
            require(
                not any(
                    p.name == "started.json" and not p.with_name("completed.json").exists()
                    for p in self.state.glob("*/started.json")
                ),
                "native_admission_reconciliation_required",
            )
            require(
                StandingNativePolicy.model_validate(object_file(self.policy_file)).digest
                == policy.digest,
                "native_policy_changed",
            )
            status = invoke([self.admin, "admin", "status"])
            require(
                status.get("ready") is True and status["policy_digest_sha256"] == policy.digest,
                "native_policy_changed",
            )
            registry = status["registry"]
            require(
                len(registry["keys"]) == 1
                and registry["keys"][0]["status"] == "active"
                and int(time.time()) < registry["keys"][0]["not_after"],
                "native_key_not_current",
            )
            bundle = self.data_root / "secrets/secs-magik"
            originals = {}
            for version in ("v1", "v2"):
                root = bundle / ("devgraph.work." + version)
                config = object_file(root / "receiver.json")
                require(
                    config["policy_binding"]
                    == dict(
                        policy_id=policy.policy_id,
                        policy_version=policy.policy_version,
                        policy_digest_sha256=policy.digest,
                    ),
                    "native_receiver_changed",
                )
                require(
                    object_file(root / "secs-public-key-registry.json") == registry,
                    "native_key_changed",
                )
                for name in ("receiver.json", "secs-public-key-registry.json"):
                    path = root / name
                    originals[path] = (sha(read(path)), read(path))
            run = self.state / action_id.replace(":", "-")
            require(not run.exists(), "native_admission_reconciliation_required")
            directory(run)
            save(run / "proposal.json", encoded(proposed))
            for path, (_, raw) in originals.items():
                save(run / (path.parent.name + "-" + path.name), raw)
            save(
                run / "started.json",
                encoded(
                    dict(
                        actor=actor,
                        action_id=action_id,
                        assignment_digest=grant.digest,
                        previous_policy_digest=policy.digest,
                        proposed_policy_digest=proposed.digest,
                    )
                ),
            )
            result = invoke(
                [
                    self.admin,
                    "admin",
                    "provision",
                    "--policy-file",
                    run / "proposal.json",
                    "--expected-policy-digest",
                    policy.digest,
                    "--verifier-not-after",
                    registry["keys"][0]["not_after"],
                ]
            )
            require(
                result.get("ready") is True
                and result["policy_digest_sha256"] == proposed.digest
                and result["policy_version"] == proposed.policy_version,
                "native_admission_readback_failed",
            )
            updated = result["registry"]
            before, after = registry["keys"][0], updated["keys"][0]
            require(
                len(updated["keys"]) == 1
                and before["not_after"] == after["not_after"]
                and {k: v for k, v in before.items() if k != "not_before"}
                == {k: v for k, v in after.items() if k != "not_before"},
                "native_admission_key_changed",
            )
            for path, (digest, raw) in originals.items():
                value = json.loads(raw)
                if path.name == "receiver.json":
                    value["policy_binding"].update(
                        policy_version=proposed.policy_version,
                        policy_digest_sha256=proposed.digest,
                    )
                else:
                    value = updated
                replace(path, encoded(value), digest)
            save(
                run / "completed.json",
                encoded(dict(policy_digest=proposed.digest, assignment_activated=False)),
            )
            return proposed
        except (
            OSError,
            ValueError,
            KeyError,
            RuntimeError,
            subprocess.SubprocessError,
        ) as exc:
            raise UnauthenticatedError("native_admission_reconciliation_required") from exc
        finally:
            os.close(fd)


class OwnerGrantProvisioning(StandingGrantProvisioning):
    def __init__(self, *, native_admission, **kwargs):
        self.native_admission = native_admission
        super().__init__(**kwargs)

    @staticmethod
    def _review_eligible(grant, base, operator, native):
        require_subset(grant, base.ceiling)
        require_subset(grant, operator.ceiling)
        StandingGrantProvisioning._eligible(grant, base, operator, proposed_policy(native, grant))

    @staticmethod
    def require_reviewed_activation(reviewed, current):
        if canonical_json(reviewed) != canonical_json(current):
            raise UnauthenticatedError("exact_review_required")

    def _activate_native(self, db, grant, actor, action_id):
        base, operator, native = self._write_configuration(db)
        self._review_eligible(grant, base, operator, native)
        proposed = self.native_admission.commit(native, grant, actor, action_id)
        # Publishing the assignment below and the observed native head shares
        # one SQLite transaction. No assignment is committed on partial failure.
        actual_operator, actual_native = self._configuration()
        if actual_native.digest != proposed.digest or actual_operator.digest != operator.digest:
            raise UnauthenticatedError("native_admission_reconciliation_required")
        db.execute(
            "INSERT OR REPLACE INTO write_provisioning_head VALUES(1,?,?,?,?,?)",
            self._head(operator, proposed),
        )
        proposed.require(grant)
