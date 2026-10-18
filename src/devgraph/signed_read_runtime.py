"""Opt-in read receiver using canonical storage and reviewed local authority.

Run with uvicorn's --factory. This creates no trust, issuer key, or policy and
never loads or forwards the machine owner's read credential.
"""

import hashlib
import json
import os
import stat
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from devgraph.api.native_read import _unique
from devgraph.api.signed_read import create_signed_read_app
from devgraph.auth.native_membership import ZenithMembershipValidator
from devgraph.auth.native_read import EnrollmentPolicy
from devgraph.auth.signed_read import SecsReadAuthority, SignedReadEnrollment
from devgraph.ops.retained_audit import RetainedAuditLog
from devgraph.storage.neo4j import Neo4jConfig, Neo4jGraphStorage


def private_json(path: Path) -> dict:
    if not path.is_absolute():
        raise ValueError("absolute owner-private configuration required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > 65536
        ):
            raise ValueError("owner-private configuration required")
        raw = file.read(65537)
        if len(raw) > 65536:
            raise ValueError("configuration too large")
    value = json.loads(raw, object_pairs_hook=_unique)
    if not isinstance(value, dict):
        raise ValueError("configuration object required")
    return value


class ReadRuntimeConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_: Literal[
        "devgraph.signed-read-runtime.v1",
        "devgraph.signed-read-runtime.v2",
        "devgraph.signed-read-runtime.v3",
        "devgraph.signed-read-runtime.v4",
        "devgraph.signed-read-runtime.v5",
    ] = Field(alias="schema")
    member_policy_file: Path | None = None
    access_inbox_database_file: Path | None = None
    access_operator_policy_file: Path | None = None
    web_wallet_url: str | None = None
    enrollment_policy_file: Path
    authority_policy_file: Path
    membership_trust_file: Path
    revoked_memberships_file: Path
    enrollment_database_file: Path
    secs_binary: Path
    secs_binary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    issuer_key_file: Path

    @model_validator(mode="after")
    def paths(self):
        if (self.schema_ != "devgraph.signed-read-runtime.v1") != (
            self.member_policy_file is not None
        ):
            raise ValueError("member policy requires explicit runtime v2")
        if self.schema_ in ("devgraph.signed-read-runtime.v4", "devgraph.signed-read-runtime.v5"):
            if self.access_inbox_database_file is None or self.access_operator_policy_file is None:
                raise ValueError("access inbox requires explicit database and operator policy")
            if self.access_inbox_database_file == self.enrollment_database_file:
                raise ValueError("access inbox requires a separate database")
        elif {"access_inbox_database_file", "access_operator_policy_file"} & self.model_fields_set:
            raise ValueError("access administration requires explicit runtime v4")
        if self.web_wallet_url is not None:
            from urllib.parse import urlsplit

            url = urlsplit(self.web_wallet_url)
            if (
                self.schema_
                not in (
                    "devgraph.signed-read-runtime.v3",
                    "devgraph.signed-read-runtime.v4",
                    "devgraph.signed-read-runtime.v5",
                )
                or url.scheme != "https"
                or not url.netloc
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("an explicit HTTPS Wallet URL requires runtime v3")
        for value in self.__dict__.values():
            if isinstance(value, Path) and not value.is_absolute():
                raise ValueError("absolute configuration paths required")
        return self


def load_enrollment(config: ReadRuntimeConfiguration) -> SignedReadEnrollment:
    if hashlib.sha256(config.secs_binary.read_bytes()).hexdigest() != config.secs_binary_sha256:
        raise ValueError("secS artifact mismatch")

    def revoked(identifier):
        state = private_json(config.revoked_memberships_file)
        if (
            set(state) != {"schema", "membership_ids"}
            or state["schema"] != "devgraph.revoked-memberships.v1"
            or not isinstance(state["membership_ids"], list)
            or any(not isinstance(v, str) for v in state["membership_ids"])
        ):
            raise ValueError("invalid revocation registry")
        return identifier in state["membership_ids"]

    membership = ZenithMembershipValidator(
        lambda: private_json(config.membership_trust_file), revoked
    )
    # Require all configured trust/status files at setup; do not turn an absent
    # revocation source into an empty revocation set.
    private_json(config.membership_trust_file)
    revoked("")
    factory = SignedReadEnrollment
    extra = {}
    if config.member_policy_file is not None:
        from devgraph.auth.access_policy import MemberAccessPolicy
        from devgraph.auth.member_read import MemberReadEnrollment

        factory = MemberReadEnrollment
        extra["members"] = lambda: MemberAccessPolicy.model_validate(
            private_json(config.member_policy_file)
        )
    policy_type = EnrollmentPolicy
    if config.schema_ in (
        "devgraph.signed-read-runtime.v3",
        "devgraph.signed-read-runtime.v4",
        "devgraph.signed-read-runtime.v5",
    ):
        from devgraph.auth.monitor_read import (
            EnrollmentPolicyV2,
            MemberAccessPolicyV2,
            MemberMonitorReadEnrollment,
        )

        factory = MemberMonitorReadEnrollment
        policy_type = EnrollmentPolicyV2
        extra["members"] = lambda: MemberAccessPolicyV2.model_validate(
            private_json(config.member_policy_file)
        )
    if config.schema_ == "devgraph.signed-read-runtime.v5":
        from devgraph.auth.access_provisioning import (
            ProvisionedMonitorReadEnrollment,
            ReadGrantProvisioning,
        )
        from devgraph.auth.access_requests import AccessRequestInbox

        root = policy_type.model_validate(private_json(config.enrollment_policy_file))
        inbox = AccessRequestInbox(
            path=config.access_inbox_database_file,
            issuer=root.issuer,
            receiver=root.receiver,
            membership_check=membership,
        )
        provisioning = ReadGrantProvisioning(inbox=inbox, members=extra.pop("members"))
        factory = ProvisionedMonitorReadEnrollment
        extra["provisioning"] = provisioning
    return factory(
        **extra,
        path=config.enrollment_database_file,
        policy=lambda: policy_type.model_validate(private_json(config.enrollment_policy_file)),
        membership_check=membership,
        authority=SecsReadAuthority(
            binary=config.secs_binary,
            policy_file=config.authority_policy_file,
            issuer_key_file=config.issuer_key_file,
        ),
    )


def create_app():
    path = os.environ.get("DEVGRAPH_SIGNED_READ_CONFIG")
    if not path:
        raise RuntimeError("signed read receiver requires explicit reviewed configuration")
    data_root = os.environ.get("DEVGRAPH_DATA_ROOT")
    if not data_root or not Path(data_root).is_absolute():
        raise RuntimeError("signed read receiver requires a retained audit data root")
    config = ReadRuntimeConfiguration.model_validate(private_json(Path(path)))
    audit = RetainedAuditLog(Path(data_root))
    enrollment = load_enrollment(config)
    # Same storage implementation/configuration as the production composition
    # root; no database migrations or direct ad-hoc queries are performed here.
    operator = None
    if config.schema_ in ("devgraph.signed-read-runtime.v4", "devgraph.signed-read-runtime.v5"):
        from devgraph.api.access_operator import register_access_operator
        from devgraph.api.access_requests import register_access_request_routes
        from devgraph.auth.access_operator import AccessOperator, OperatorPolicy
        from devgraph.auth.access_requests import AccessRequestInbox

        root = enrollment.policy()
        inbox = (
            enrollment.provisioning.inbox
            if config.schema_ == "devgraph.signed-read-runtime.v5"
            else AccessRequestInbox(
                path=config.access_inbox_database_file,
                issuer=root.issuer,
                receiver=root.receiver,
                membership_check=enrollment.membership_check,
            )
        )
        operator = AccessOperator(
            inbox=inbox,
            provisioning=getattr(enrollment, "provisioning", None),
            policy=lambda: OperatorPolicy.model_validate(
                private_json(config.access_operator_policy_file)
            ),
        )
    storage = Neo4jGraphStorage(Neo4jConfig.from_env())

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            storage.close()

    app = create_signed_read_app(
        storage=storage,
        enrollment=enrollment,
        audit_log=audit,
        lifespan=lifespan,
        web_wallet_url=config.web_wallet_url,
    )

    if operator is not None:
        register_access_request_routes(app, inbox)
        register_access_operator(app, operator)
    return app
