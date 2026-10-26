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


def private_json(path: Path, *, maximum_bytes=65536) -> dict:
    if maximum_bytes not in (65536, 262144):
        raise ValueError("unsupported private configuration bound")
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
            or info.st_size > maximum_bytes
        ):
            raise ValueError("owner-private configuration required")
        raw = file.read(maximum_bytes + 1)
        if len(raw) > maximum_bytes:
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
        "devgraph.signed-read-runtime.v6",
        "devgraph.signed-read-runtime.v7",
        "devgraph.signed-read-runtime.v8",
        "devgraph.signed-read-runtime.v9",
        "devgraph.signed-read-runtime.v10",
        "devgraph.signed-read-runtime.v11",
    ] = Field(alias="schema")
    owner_arena_upgrade_file: Path | None = None
    owner_arena_administration: bool = Field(default=False, strict=True)
    canonical_access_points: bool = False
    holder_grant_binary: Path | None = None
    holder_grant_binary_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    canonical_service_id: str | None = Field(default=None, min_length=1, max_length=256)
    holder_grant_provider_runtime_file: Path | None = None
    owner_parent_runtime_file: Path | None = None
    owner_endpoint_profile_file: Path | None = None
    endpoint_tls_proxy_port: int | None = Field(default=None, strict=True, ge=1024, le=65535)
    owner_native_admission_directory: Path | None = None
    owner_bootstrap_database_file: Path | None = None
    owner_install_review_file: Path | None = None
    membership_requirement: Literal["required", "optional"] | None = None
    write_native_policy_file: Path | None = None
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
        if self.owner_arena_upgrade_file is not None and (
            self.schema_ != "devgraph.signed-read-runtime.v11"
            or self.owner_bootstrap_database_file is None
        ):
            raise ValueError("Arena endorsement requires a registered owner runtime v11")
        if self.owner_arena_administration and self.owner_arena_upgrade_file is None:
            raise ValueError("all-Arena role requires owner endorsement")
        holder = (
            self.holder_grant_binary,
            self.holder_grant_binary_sha256,
            self.canonical_service_id,
        )
        if any(v is not None for v in holder) and not all(v is not None for v in holder):
            raise ValueError("holder grants require explicit binary, digest and canonical service")
        if self.canonical_access_points and self.holder_grant_binary is None:
            raise ValueError("canonical service routes require explicit native holder authority")
        if self.holder_grant_binary is not None and self.member_policy_file is None:
            raise ValueError("holder grants require durable member policy")
        if self.holder_grant_provider_runtime_file is not None and self.holder_grant_binary is None:
            raise ValueError("canonical holder provider requires the explicit grant authority")
        if self.endpoint_tls_proxy_port is not None and self.schema_ not in (
            "devgraph.signed-read-runtime.v10",
            "devgraph.signed-read-runtime.v11",
        ):
            raise ValueError("dedicated TLS proxy requires published owner endpoint")
        publication = {
            "owner_parent_runtime_file",
            "owner_endpoint_profile_file",
        } & self.model_fields_set
        if self.schema_ == "devgraph.signed-read-runtime.v10" or (
            self.schema_ == "devgraph.signed-read-runtime.v11" and publication
        ):
            if (
                publication != {"owner_parent_runtime_file", "owner_endpoint_profile_file"}
                or self.owner_parent_runtime_file is None
                or self.owner_endpoint_profile_file is None
            ):
                raise ValueError("published endpoint requires explicit owner parent and profile")
        elif publication:
            raise ValueError("published endpoint binding requires runtime v10")
        owner_fields = {
            "owner_bootstrap_database_file",
            "owner_install_review_file",
        } & self.model_fields_set
        if owner_fields and (
            self.schema_
            not in (
                "devgraph.signed-read-runtime.v9",
                "devgraph.signed-read-runtime.v10",
                "devgraph.signed-read-runtime.v11",
            )
            or owner_fields != {"owner_bootstrap_database_file", "owner_install_review_file"}
            or self.owner_bootstrap_database_file is None
            or self.owner_install_review_file is None
        ):
            raise ValueError("signed owner registration requires explicit runtime v9 binding")
        if (
            self.schema_
            in (
                "devgraph.signed-read-runtime.v9",
                "devgraph.signed-read-runtime.v10",
                "devgraph.signed-read-runtime.v11",
            )
        ) != (self.owner_native_admission_directory is not None):
            raise ValueError("owner native admission requires explicit runtime v9")
        if (
            self.schema_
            not in (
                "devgraph.signed-read-runtime.v9",
                "devgraph.signed-read-runtime.v10",
                "devgraph.signed-read-runtime.v11",
            )
            and "owner_native_admission_directory" in self.model_fields_set
        ):
            raise ValueError("owner native admission requires explicit runtime v9")
        if self.schema_ in (
            "devgraph.signed-read-runtime.v8",
            "devgraph.signed-read-runtime.v9",
            "devgraph.signed-read-runtime.v10",
            "devgraph.signed-read-runtime.v11",
        ):
            if self.membership_requirement is None:
                raise ValueError("runtime v8 requires explicit resource membership policy")
        elif "membership_requirement" in self.model_fields_set:
            raise ValueError("resource membership policy requires runtime v8")
        if (
            self.schema_
            in (
                "devgraph.signed-read-runtime.v6",
                "devgraph.signed-read-runtime.v7",
                "devgraph.signed-read-runtime.v8",
                "devgraph.signed-read-runtime.v9",
                "devgraph.signed-read-runtime.v10",
                "devgraph.signed-read-runtime.v11",
            )
        ) != (self.write_native_policy_file is not None):
            raise ValueError("native write admission requires explicit runtime v6")
        if (
            self.schema_
            not in (
                "devgraph.signed-read-runtime.v6",
                "devgraph.signed-read-runtime.v7",
                "devgraph.signed-read-runtime.v8",
                "devgraph.signed-read-runtime.v9",
                "devgraph.signed-read-runtime.v10",
                "devgraph.signed-read-runtime.v11",
            )
            and "write_native_policy_file" in self.model_fields_set
        ):
            raise ValueError("write admission requires explicit runtime v6")
        if (self.schema_ != "devgraph.signed-read-runtime.v1") != (
            self.member_policy_file is not None
        ):
            raise ValueError("member policy requires explicit runtime v2")
        if self.schema_ in (
            "devgraph.signed-read-runtime.v4",
            "devgraph.signed-read-runtime.v5",
            "devgraph.signed-read-runtime.v6",
            "devgraph.signed-read-runtime.v7",
            "devgraph.signed-read-runtime.v8",
            "devgraph.signed-read-runtime.v9",
            "devgraph.signed-read-runtime.v10",
            "devgraph.signed-read-runtime.v11",
        ):
            if self.access_inbox_database_file is None or self.access_operator_policy_file is None:
                raise ValueError("access inbox requires explicit database and operator policy")
            if self.access_inbox_database_file == self.enrollment_database_file:
                raise ValueError("access inbox requires a separate database")
        elif {
            "access_inbox_database_file",
            "access_operator_policy_file",
        } & self.model_fields_set:
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
                    "devgraph.signed-read-runtime.v6",
                    "devgraph.signed-read-runtime.v7",
                    "devgraph.signed-read-runtime.v8",
                    "devgraph.signed-read-runtime.v9",
                    "devgraph.signed-read-runtime.v10",
                    "devgraph.signed-read-runtime.v11",
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


def owner_operator_policy(config: ReadRuntimeConfiguration, *, data_root):
    if config.schema_ == "devgraph.signed-read-runtime.v10" or (
        config.schema_ == "devgraph.signed-read-runtime.v11"
        and config.owner_parent_runtime_file is not None
    ):
        from devgraph.owner_endpoint import endpoint_operator_policy

        return endpoint_operator_policy(config, data_root=data_root)
    raw = private_json(config.access_operator_policy_file)
    if config.owner_bootstrap_database_file is None:
        return raw
    from devgraph.auth.delegated_contract import canonical_json
    from devgraph.auth.owner_bootstrap import active_owner

    review = private_json(config.owner_install_review_file, maximum_bytes=262144)
    digest = hashlib.sha256(canonical_json(review)).hexdigest()
    active_owner(
        config.owner_bootstrap_database_file,
        resource_id=review["resource_id"],
        grant_id=digest,
        public_key=review["owner_public_key"],
    )
    endorsed = config.owner_arena_upgrade_file is not None
    if endorsed:
        from devgraph.auth.owner_arena_policy import verify_endorsement

        endorsement = private_json(config.owner_arena_upgrade_file, maximum_bytes=262144)
        verify_endorsement(endorsement, review=review, actual_policy=raw)
    if (
        data_root is None
        or Path(review["graph_data_root"]) != Path(data_root)
        or review.get("owner_claim_required") is not True
        or review.get("local_only") is not True
        or raw.get("operator_public_keys") != [review["owner_public_key"]]
        or raw.get("ceiling", {}).get("receiver") != review["connection"]["receiver"]
        or raw.get("ceiling", {}).get("issuer") != review["connection"]["issuer"]
        or (
            not endorsed
            and hashlib.sha256(canonical_json(raw)).hexdigest()
            != review["staged"]["authority/operators.json"]
        )
    ):
        raise ValueError("registered owner policy changed")
    return raw


def load_enrollment(config: ReadRuntimeConfiguration, *, data_root=None) -> SignedReadEnrollment:
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
    if config.schema_ in (
        "devgraph.signed-read-runtime.v8",
        "devgraph.signed-read-runtime.v9",
        "devgraph.signed-read-runtime.v10",
        "devgraph.signed-read-runtime.v11",
    ):
        from devgraph.auth.resource_membership import ResourceMembershipAdmission

        membership = ResourceMembershipAdmission(
            membership, required=config.membership_requirement == "required"
        )
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
        "devgraph.signed-read-runtime.v6",
        "devgraph.signed-read-runtime.v7",
        "devgraph.signed-read-runtime.v8",
        "devgraph.signed-read-runtime.v9",
        "devgraph.signed-read-runtime.v10",
        "devgraph.signed-read-runtime.v11",
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
    if config.schema_ in (
        "devgraph.signed-read-runtime.v7",
        "devgraph.signed-read-runtime.v8",
        "devgraph.signed-read-runtime.v9",
        "devgraph.signed-read-runtime.v10",
        "devgraph.signed-read-runtime.v11",
    ):
        from devgraph.auth.path_access import PathEnrollmentPolicy, PathMemberPolicy

        if config.schema_ in (
            "devgraph.signed-read-runtime.v8",
            "devgraph.signed-read-runtime.v9",
            "devgraph.signed-read-runtime.v10",
            "devgraph.signed-read-runtime.v11",
        ):
            from devgraph.auth.standing_access import StandingMemberPolicy

            PathMemberPolicy = StandingMemberPolicy
        if config.schema_ == "devgraph.signed-read-runtime.v11":
            from devgraph.auth.arena_access import ArenaEnrollmentPolicy, ArenaMemberPolicy

            PathEnrollmentPolicy, PathMemberPolicy = ArenaEnrollmentPolicy, ArenaMemberPolicy
            if config.owner_arena_administration:
                from devgraph.auth.arena_access import (
                    OwnerArenaEnrollmentPolicy,
                    OwnerArenaMemberPolicy,
                )

                PathEnrollmentPolicy, PathMemberPolicy = (
                    OwnerArenaEnrollmentPolicy,
                    OwnerArenaMemberPolicy,
                )
        policy_type = PathEnrollmentPolicy
        extra["members"] = lambda: PathMemberPolicy.model_validate(
            private_json(config.member_policy_file)
        )

    if config.schema_ in (
        "devgraph.signed-read-runtime.v5",
        "devgraph.signed-read-runtime.v6",
        "devgraph.signed-read-runtime.v7",
        "devgraph.signed-read-runtime.v8",
        "devgraph.signed-read-runtime.v9",
        "devgraph.signed-read-runtime.v10",
        "devgraph.signed-read-runtime.v11",
    ):
        from devgraph.auth.access_provisioning import (
            ProvisionedMonitorReadEnrollment,
            ReadGrantProvisioning,
        )
        from devgraph.auth.access_requests import AccessRequestInbox

        root = policy_type.model_validate(private_json(config.enrollment_policy_file))
        inbox_type = AccessRequestInbox
        inbox_extra = {}
        if config.schema_ in (
            "devgraph.signed-read-runtime.v7",
            "devgraph.signed-read-runtime.v8",
            "devgraph.signed-read-runtime.v9",
            "devgraph.signed-read-runtime.v10",
            "devgraph.signed-read-runtime.v11",
        ):
            from devgraph.auth.access_catalog import CatalogOperatorPolicy
            from devgraph.auth.path_activation import PathAccessRequestInbox

            if config.schema_ in (
                "devgraph.signed-read-runtime.v8",
                "devgraph.signed-read-runtime.v9",
                "devgraph.signed-read-runtime.v10",
                "devgraph.signed-read-runtime.v11",
            ):
                from devgraph.auth.standing_access import StandingOperatorPolicy
                from devgraph.auth.standing_activation import StandingAccessRequestInbox

                CatalogOperatorPolicy = StandingOperatorPolicy
                PathAccessRequestInbox = StandingAccessRequestInbox
            if config.schema_ == "devgraph.signed-read-runtime.v11":
                from devgraph.auth.arena_access import ArenaOperatorPolicy
                from devgraph.auth.arena_activation import ArenaAccessRequestInbox

                CatalogOperatorPolicy, PathAccessRequestInbox = (
                    ArenaOperatorPolicy,
                    ArenaAccessRequestInbox,
                )
                if config.owner_arena_administration:
                    from devgraph.auth.arena_access import OwnerArenaOperatorPolicy

                    CatalogOperatorPolicy = OwnerArenaOperatorPolicy
            inbox_type = PathAccessRequestInbox
            # Bind the selected decoder now: provisioning imports below also
            # use CatalogOperatorPolicy and must not reset a v8 inbox to v3.
            inbox_extra["catalog_policy"] = lambda selected=CatalogOperatorPolicy: (
                selected.model_validate(owner_operator_policy(config, data_root=data_root))
            )
        inbox = inbox_type(
            **inbox_extra,
            path=config.access_inbox_database_file,
            issuer=root.issuer,
            receiver=root.receiver,
            membership_check=membership,
        )
        members = extra.pop("members")
        if config.schema_ in (
            "devgraph.signed-read-runtime.v6",
            "devgraph.signed-read-runtime.v7",
            "devgraph.signed-read-runtime.v8",
            "devgraph.signed-read-runtime.v9",
            "devgraph.signed-read-runtime.v10",
            "devgraph.signed-read-runtime.v11",
        ):
            from devgraph.auth.access_operator import OperatorPolicyV2
            from devgraph.auth.write_provisioning import (
                NativeWritePolicy,
                WriteGrantProvisioning,
            )
            from devgraph.ops.named_work_v2_receiver import LocalNamedWorkV2Receiver

            if data_root is None:
                raise ValueError("write admission requires a retained receiver data root")

            def native():
                raw = private_json(config.write_native_policy_file, maximum_bytes=262144)
                if config.schema_ in (
                    "devgraph.signed-read-runtime.v8",
                    "devgraph.signed-read-runtime.v9",
                    "devgraph.signed-read-runtime.v10",
                    "devgraph.signed-read-runtime.v11",
                ):
                    from pydantic import TypeAdapter

                    from devgraph.auth.standing_access import StandingNativePolicy

                    policy = TypeAdapter(StandingNativePolicy | NativeWritePolicy).validate_python(
                        raw
                    )
                else:
                    policy = NativeWritePolicy.model_validate(raw)
                verifier = LocalNamedWorkV2Receiver(
                    data_root=data_root, storage=None, audit_log=None
                )._load_verifier()
                verifier.config.policy_registry.require_binding(
                    policy.policy_id, policy.policy_version, policy.digest
                )
                return policy

            provisioning_type, operator_type = WriteGrantProvisioning, OperatorPolicyV2
            if config.schema_ in (
                "devgraph.signed-read-runtime.v7",
                "devgraph.signed-read-runtime.v8",
                "devgraph.signed-read-runtime.v9",
                "devgraph.signed-read-runtime.v10",
                "devgraph.signed-read-runtime.v11",
            ):
                from devgraph.auth.access_catalog import CatalogOperatorPolicy
                from devgraph.auth.path_activation import PathGrantProvisioning

                provisioning_type, operator_type = (
                    PathGrantProvisioning,
                    CatalogOperatorPolicy,
                )
                if config.schema_ in (
                    "devgraph.signed-read-runtime.v8",
                    "devgraph.signed-read-runtime.v9",
                    "devgraph.signed-read-runtime.v10",
                    "devgraph.signed-read-runtime.v11",
                ):
                    from devgraph.auth.standing_access import StandingOperatorPolicy
                    from devgraph.auth.standing_activation import (
                        StandingGrantProvisioning,
                    )

                    provisioning_type, operator_type = (
                        StandingGrantProvisioning,
                        StandingOperatorPolicy,
                    )
            owner_admission = {}
            if config.schema_ in (
                "devgraph.signed-read-runtime.v9",
                "devgraph.signed-read-runtime.v10",
                "devgraph.signed-read-runtime.v11",
            ):
                from devgraph.auth.owner_native_admission import (
                    OwnerGrantProvisioning,
                    OwnerNativeAdmission,
                )

                provisioning_type = OwnerGrantProvisioning
                owner_admission["native_admission"] = OwnerNativeAdmission(
                    data_root=data_root,
                    state_directory=config.owner_native_admission_directory,
                    native_policy_file=config.write_native_policy_file,
                    admin_binary=Path.home()
                    / "Library/Application Support/Zenith/secS/bin/secs-devgraph-work-v1",
                )
            if config.schema_ == "devgraph.signed-read-runtime.v11":
                from devgraph.auth.arena_access import ArenaOperatorPolicy
                from devgraph.auth.arena_activation import ArenaGrantProvisioning

                provisioning_type, operator_type = ArenaGrantProvisioning, ArenaOperatorPolicy
                if config.owner_arena_administration:
                    from devgraph.auth.arena_access import OwnerArenaOperatorPolicy
                    from devgraph.auth.arena_activation import OwnerArenaGrantProvisioning

                    provisioning_type, operator_type = (
                        OwnerArenaGrantProvisioning,
                        OwnerArenaOperatorPolicy,
                    )
            provisioning = provisioning_type(
                **owner_admission,
                inbox=inbox,
                members=members,
                native=native,
                operators=lambda: operator_type.model_validate(
                    owner_operator_policy(config, data_root=data_root)
                ),
            )
        else:
            provisioning = ReadGrantProvisioning(inbox=inbox, members=members)
        factory = ProvisionedMonitorReadEnrollment
        if config.schema_ in (
            "devgraph.signed-read-runtime.v7",
            "devgraph.signed-read-runtime.v8",
            "devgraph.signed-read-runtime.v9",
            "devgraph.signed-read-runtime.v10",
            "devgraph.signed-read-runtime.v11",
        ):
            from devgraph.auth.path_activation import PathMonitorReadEnrollment

            factory = PathMonitorReadEnrollment
        if config.schema_ == "devgraph.signed-read-runtime.v11":
            from devgraph.auth.arena_activation import ArenaMonitorReadEnrollment

            factory = ArenaMonitorReadEnrollment
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


def create_app(*, config_path=None, canonical_routes=True):
    path = (
        str(config_path)
        if config_path is not None
        else os.environ.get("DEVGRAPH_SIGNED_READ_CONFIG")
    )
    if not path:
        raise RuntimeError("signed read receiver requires explicit reviewed configuration")
    data_root = os.environ.get("DEVGRAPH_DATA_ROOT")
    if not data_root or not Path(data_root).is_absolute():
        raise RuntimeError("signed read receiver requires a retained audit data root")
    config = ReadRuntimeConfiguration.model_validate(private_json(Path(path)))
    audit = RetainedAuditLog(Path(data_root))
    enrollment = load_enrollment(config, data_root=Path(data_root))
    provider_path = None
    if config.holder_grant_binary is not None:
        from devgraph.auth.holder_grants import HolderGrantHost

        provider = enrollment
        provider_path = (
            config.holder_grant_provider_runtime_file or config.owner_parent_runtime_file
        )
        if provider_path is not None and provider_path.resolve() != Path(path).resolve():
            parent = ReadRuntimeConfiguration.model_validate(private_json(provider_path))
            if (
                parent.canonical_service_id != config.canonical_service_id
                or parent.holder_grant_binary != config.holder_grant_binary
                or parent.holder_grant_binary_sha256 != config.holder_grant_binary_sha256
            ):
                raise ValueError("published holder grants require the unchanged canonical provider")
            next_provider = (
                parent.holder_grant_provider_runtime_file or parent.owner_parent_runtime_file
            )
            if next_provider is not None and next_provider.resolve() != provider_path.resolve():
                raise ValueError(
                    "holder provider must select one canonical registry without chaining"
                )
            if any(
                getattr(parent, field) != getattr(config, field)
                for field in (
                    "issuer_key_file",
                    "owner_bootstrap_database_file",
                    "owner_install_review_file",
                    "membership_requirement",
                    "membership_trust_file",
                    "revoked_memberships_file",
                )
            ):
                raise ValueError("canonical holder provider ownership or eligibility changed")
            provider = load_enrollment(parent, data_root=Path(data_root))
        HolderGrantHost(
            enrollment=enrollment,
            provider_enrollment=provider,
            binary=config.holder_grant_binary,
            binary_sha256=config.holder_grant_binary_sha256,
            service=config.canonical_service_id,
        )
    # Same storage implementation/configuration as the production composition
    # root; no database migrations or direct ad-hoc queries are performed here.
    operator = None
    if config.schema_ in (
        "devgraph.signed-read-runtime.v4",
        "devgraph.signed-read-runtime.v5",
        "devgraph.signed-read-runtime.v6",
        "devgraph.signed-read-runtime.v7",
        "devgraph.signed-read-runtime.v8",
        "devgraph.signed-read-runtime.v9",
        "devgraph.signed-read-runtime.v10",
        "devgraph.signed-read-runtime.v11",
    ):
        from devgraph.api.access_operator import register_access_operator
        from devgraph.api.access_requests import register_access_request_routes
        from devgraph.auth.access_operator import (
            AccessOperator,
            OperatorPolicy,
            OperatorPolicyV2,
        )
        from devgraph.auth.access_requests import AccessRequestInbox

        root = enrollment.policy()
        inbox = (
            enrollment.provisioning.inbox
            if config.schema_
            in (
                "devgraph.signed-read-runtime.v5",
                "devgraph.signed-read-runtime.v6",
                "devgraph.signed-read-runtime.v7",
                "devgraph.signed-read-runtime.v8",
                "devgraph.signed-read-runtime.v9",
                "devgraph.signed-read-runtime.v10",
                "devgraph.signed-read-runtime.v11",
            )
            else AccessRequestInbox(
                path=config.access_inbox_database_file,
                issuer=root.issuer,
                receiver=root.receiver,
                membership_check=enrollment.membership_check,
            )
        )
        if config.schema_ not in (
            "devgraph.signed-read-runtime.v7",
            "devgraph.signed-read-runtime.v8",
            "devgraph.signed-read-runtime.v9",
            "devgraph.signed-read-runtime.v10",
            "devgraph.signed-read-runtime.v11",
        ):
            operator = AccessOperator(
                inbox=inbox,
                provisioning=getattr(enrollment, "provisioning", None),
                policy=lambda: (
                    OperatorPolicyV2
                    if config.schema_ == "devgraph.signed-read-runtime.v6"
                    else OperatorPolicy
                ).model_validate(owner_operator_policy(config, data_root=data_root)),
            )
    storage = Neo4jGraphStorage(Neo4jConfig.from_env())
    if config.schema_ in (
        "devgraph.signed-read-runtime.v7",
        "devgraph.signed-read-runtime.v8",
        "devgraph.signed-read-runtime.v9",
        "devgraph.signed-read-runtime.v10",
        "devgraph.signed-read-runtime.v11",
    ):
        from devgraph.auth.access_catalog import CatalogOperatorPolicy
        from devgraph.auth.path_activation import PathAccessOperator

        if config.schema_ in (
            "devgraph.signed-read-runtime.v8",
            "devgraph.signed-read-runtime.v9",
            "devgraph.signed-read-runtime.v10",
            "devgraph.signed-read-runtime.v11",
        ):
            from devgraph.auth.standing_access import StandingOperatorPolicy
            from devgraph.auth.standing_activation import StandingAccessOperator

            CatalogOperatorPolicy, PathAccessOperator = (
                StandingOperatorPolicy,
                StandingAccessOperator,
            )
        if config.schema_ == "devgraph.signed-read-runtime.v11":
            from devgraph.auth.arena_access import ArenaOperatorPolicy
            from devgraph.auth.arena_activation import ArenaAccessOperator

            CatalogOperatorPolicy, PathAccessOperator = ArenaOperatorPolicy, ArenaAccessOperator
            if config.owner_arena_administration:
                from devgraph.auth.arena_access import OwnerArenaOperatorPolicy

                CatalogOperatorPolicy = OwnerArenaOperatorPolicy
        inbox.storage = storage
        operator = PathAccessOperator(
            inbox=inbox,
            provisioning=enrollment.provisioning,
            storage=storage,
            policy=lambda: CatalogOperatorPolicy.model_validate(
                owner_operator_policy(config, data_root=data_root)
            ),
        )

    canonical_children = []

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            storage.close()
            for child in canonical_children:
                child.state.service_storage.close()

    app = create_signed_read_app(
        storage=storage,
        enrollment=enrollment,
        audit_log=audit,
        lifespan=lifespan,
        web_wallet_url=config.web_wallet_url,
    )

    if config.schema_ in (
        "devgraph.signed-read-runtime.v6",
        "devgraph.signed-read-runtime.v7",
        "devgraph.signed-read-runtime.v8",
        "devgraph.signed-read-runtime.v9",
        "devgraph.signed-read-runtime.v10",
        "devgraph.signed-read-runtime.v11",
    ):
        from types import SimpleNamespace

        from devgraph.api.audit import RetainedAuditMiddleware
        from devgraph.api.credential_work import register_credential_work
        from devgraph.api.errors import register_error_handlers
        from devgraph.api.named_work import register_named_work
        from devgraph.auth.write_provisioning import ProvisionedWriteReceiver
        from devgraph.ops.browser_credential_work import BrowserCredentialWorkHost
        from devgraph.ops.named_work_v2_receiver import LocalNamedWorkV2Receiver

        receiver = ProvisionedWriteReceiver(
            receiver=LocalNamedWorkV2Receiver(
                data_root=Path(data_root), storage=storage, audit_log=audit
            ),
            provisioning=enrollment.provisioning,
        )
        services = SimpleNamespace(
            named_work=None,
            named_work_v2=receiver,
            credential_work_host=BrowserCredentialWorkHost(
                data_root=Path(data_root), receiver=receiver
            ),
        )
        previous_handlers = dict(app.exception_handlers)
        register_error_handlers(app)
        app.exception_handlers.update(previous_handlers)
        app.add_middleware(RetainedAuditMiddleware, audit_log=audit)
        register_named_work(app, services)
        register_credential_work(app, services)
    if operator is not None:
        register_access_request_routes(app, inbox)
        register_access_operator(app, operator)
    app.state.service_storage = storage
    app.state.service_authority_receiver = enrollment.policy().receiver
    if canonical_routes and config.canonical_access_points:
        from devgraph.api.canonical_service import register_service_routes

        canonical_app = create_app(config_path=provider_path or Path(path), canonical_routes=False)
        canonical_children.append(canonical_app)
        app.state.canonical_service_app = canonical_app
        app.state.canonical_service_host = enrollment.holder_grants
        register_service_routes(
            app,
            canonical_app=canonical_app,
            host=enrollment.holder_grants,
            origin=enrollment.policy().receiver,
        )
    return app
