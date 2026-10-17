"""Opt-in per-member issuer policy for the existing shared read credential.

There is no client-selected policy, bearer fallback, or child credential. The
approved registry and root secS policy must agree; changing either fails closed.
"""

import hashlib
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar

from devgraph.auth.access_policy import MemberAccessPolicy, compile_read
from devgraph.auth.delegated_contract import _grant_is_subset, canonical_json
from devgraph.auth.errors import UnauthenticatedError
from devgraph.auth.native_read import EnrollmentPolicy
from devgraph.auth.signed_read import SignedReadEnrollment


class MemberReadEnrollment(SignedReadEnrollment):
    def __init__(self, *, members: Callable[[], MemberAccessPolicy], **kwargs):
        self.members = members
        self._selection = ContextVar("devgraph_member_selection", default=None)
        super().__init__(**kwargs)
        with self._database() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS member_policy_revision (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
                    digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS member_assignment_epoch (
                    owner TEXT, device TEXT, digest TEXT NOT NULL, epoch INTEGER NOT NULL,
                    active INTEGER NOT NULL, PRIMARY KEY(owner,device));
                CREATE TABLE IF NOT EXISTS member_challenge_epoch (
                    request_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL);
            """)
        with self._using_registry():
            pass

    def _load_registry(self):
        registry = self.members()
        ceiling = registry.ceiling
        configured = self.policy()
        if (
            ceiling.issuer != configured.issuer
            or ceiling.receiver != configured.receiver
            or ceiling.read_grants != configured.grants
            or ceiling.lifetime_seconds != configured.lifetime_seconds
        ):
            raise UnauthenticatedError("read_policy_mismatch")
        return registry

    @contextmanager
    def _using_registry(self):
        # One approved snapshot per operation. Persist the highest revision and
        # member generations before issuing or accepting anything under it.
        registry = self._load_registry()
        digest = hashlib.sha256(canonical_json(registry)).hexdigest()
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = db.execute(
                    "SELECT revision,digest FROM member_policy_revision WHERE id=1"
                ).fetchone()
                if previous and (
                    registry.revision < previous[0]
                    or registry.revision == previous[0]
                    and digest != previous[1]
                ):
                    raise UnauthenticatedError("member_policy_rollback")
                if not previous or registry.revision > previous[0]:
                    rows = {
                        (r[0], r[1]): r[2:]
                        for r in db.execute(
                            "SELECT owner,device,digest,epoch,active FROM member_assignment_epoch"
                        )
                    }
                    selected = {
                        (g.subject_public_key, g.installation_id): g for g in registry.assignments
                    }
                    if len(set(rows) | set(selected)) > 10000:
                        raise UnauthenticatedError("try_later")
                    for key, grant in selected.items():
                        old = rows.get(key)
                        epoch = (old[1] + int(old[0] != grant.digest or not old[2])) if old else 1
                        db.execute(
                            "INSERT OR REPLACE INTO member_assignment_epoch VALUES(?,?,?,?,1)",
                            (*key, grant.digest, epoch),
                        )
                    for key, old in rows.items():
                        if key not in selected and old[2]:
                            db.execute(
                                "UPDATE member_assignment_epoch SET active=0,epoch=epoch+1 "
                                "WHERE owner=? AND device=?",
                                key,
                            )
                    db.execute(
                        "INSERT OR REPLACE INTO member_policy_revision VALUES(1,?,?)",
                        (registry.revision, digest),
                    )
                epochs = {
                    (r[0], r[1]): r[2]
                    for r in db.execute(
                        "SELECT owner,device,epoch FROM member_assignment_epoch WHERE active=1"
                    )
                }
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        token = self._selection.set((registry, epochs))
        try:
            yield
        finally:
            self._selection.reset(token)

    def _registry(self):
        selected = self._selection.get()
        if selected is None:
            raise UnauthenticatedError("member_policy_snapshot_required")
        return selected[0]

    def _check_policy_snapshot(self, db):
        registry = self._registry()
        expected = (registry.revision, hashlib.sha256(canonical_json(registry)).hexdigest())
        current = db.execute(
            "SELECT revision,digest FROM member_policy_revision WHERE id=1"
        ).fetchone()
        if current != expected:
            raise UnauthenticatedError("policy_changed")

    def _epoch(self, owner, device):
        selected = self._selection.get()
        if selected is None or (owner, device) not in selected[1]:
            raise UnauthenticatedError("member_access_not_granted")
        return selected[1][owner, device]

    def challenge(self, owner, device, *, now=None):
        with self._using_registry():
            value = super().challenge(owner, device, now=now)
            with self._database() as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    self._check_policy_snapshot(db)
                    db.execute(
                        "DELETE FROM member_challenge_epoch WHERE request_id NOT IN "
                        "(SELECT id FROM challenges)"
                    )
                    db.execute(
                        "INSERT INTO member_challenge_epoch VALUES(?,?)",
                        (value["request_id"], self._epoch(owner, device)),
                    )
                    db.execute("COMMIT")
                except BaseException:
                    db.execute("ROLLBACK")
                    raise
            return value

    def enroll(self, challenge, proof, signature, *, now=None):
        with self._using_registry():
            if not isinstance(challenge, dict) or any(
                not isinstance(challenge.get(k), str)
                for k in ("request_id", "subject_public_key", "installation_id")
            ):
                raise UnauthenticatedError("invalid_request")
            with self._database() as db:
                row = db.execute(
                    "SELECT epoch FROM member_challenge_epoch WHERE request_id=?",
                    (challenge["request_id"],),
                ).fetchone()
            if not row or row[0] != self._epoch(
                challenge["subject_public_key"], challenge["installation_id"]
            ):
                raise UnauthenticatedError("policy_changed")
            return super().enroll(challenge, proof, signature, now=now)

    def verify_request(self, *, credential, call, proof, now=None):
        with self._using_registry():
            return super().verify_request(credential=credential, call=call, proof=proof, now=now)

    def _assignment(self, owner, device, current):
        try:
            return self._registry().assignment(owner, device, current)
        except ValueError:
            raise UnauthenticatedError("member_access_not_granted") from None

    def _policy_for(self, owner, device, current):
        return compile_read(self._assignment(owner, device, current))

    def _credential_expiry(self, policy, owner, device, current):
        assignment = self._assignment(owner, device, current)
        if self._policy_for(owner, device, current).digest != policy.digest:
            raise UnauthenticatedError("policy_changed")
        return min(current + policy.lifetime_seconds, assignment.not_after)

    def _authority_policy(self, policy: EnrollmentPolicy):
        # The native shared contract compares exact permission records. Validate
        # semantic narrowing in Devgraph, then project a narrow policy rather
        # than changing Wallet's application-neutral verifier semantics.
        configured = super()._authority_policy(self.policy())
        root = self.policy()
        if (
            policy.issuer != root.issuer
            or policy.receiver != root.receiver
            or policy.lifetime_seconds > root.lifetime_seconds
            or any(not any(_grant_is_subset(g, p) for p in root.grants) for g in policy.grants)
        ):
            raise UnauthenticatedError("grant_amplification")
        return self.authority.policy_for(
            policy, key_id=configured["key_id"], public_key=configured["public_key"]
        )

    def _invoke(self, policy, operation, **fields):
        return self.authority.invoke_under_policy(policy, operation, **fields)

    def _mint(self, manifest, current):
        assignment = self._assignment(
            manifest["subject_public_key"], manifest["installation_id"], current
        )
        # Remember the complete assignment revision, including expiry and write
        # selections. Reducing a deadline must invalidate older credentials too.
        if manifest["expires_at"] > assignment.not_after:
            raise UnauthenticatedError("policy_changed")
        manifest["assignment_digest"] = assignment.digest
        manifest["assignment_epoch"] = self._epoch(
            manifest["subject_public_key"], manifest["installation_id"]
        )
        return super()._mint(manifest, current)

    def _manifest_current(self, manifest, current):
        assignment = self._assignment(
            manifest["subject_public_key"], manifest["installation_id"], current
        )
        return manifest.get("assignment_digest") == assignment.digest and manifest.get(
            "assignment_epoch"
        ) == self._epoch(manifest["subject_public_key"], manifest["installation_id"])
