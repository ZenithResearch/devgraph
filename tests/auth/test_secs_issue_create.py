from __future__ import annotations

import base64
import hashlib
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import devgraph.auth.secs_issue_create as exact
from devgraph.auth.enforcement import AuditLog
from devgraph.auth.errors import ForbiddenError
from devgraph.events.outbox import EVENT_RECEIPT_LABEL, IdempotencyScopeConflict
from devgraph.storage.memory import MemoryGraphStorage

FIXTURES = Path(__file__).parents[1] / "fixtures" / "secs_devgraph_issue_create_v1"
EXPECTED_NOW = 1_800_000_000
STABLE_ISSUER = "secs:devgraph-receiver-local"


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _fixture_json(name: str) -> dict[str, object]:
    value = json.loads(_fixture_bytes(name))
    assert isinstance(value, dict)
    return value


def _raw_key() -> str:
    return _fixture_bytes("idempotency-key.txt").decode("ascii").rstrip("\n")


def _policy() -> exact.SecSIssueCreatePolicyBinding:
    binding = _fixture_json("receiver-policy-binding.json")
    return exact.SecSIssueCreatePolicyBinding(
        policy_id=str(binding["policy_id"]),
        policy_version=int(binding["policy_version"]),
        policy_digest_sha256=str(binding["policy_digest_sha256"]),
    )


def _verifier(
    *,
    now: int = EXPECTED_NOW,
    registry: exact.SecSVerifierKeyRegistry | None = None,
    policy_registry: exact.SecSIssueCreatePolicyRegistry | None = None,
) -> exact.SecSIssueCreateVerifier:
    return exact.SecSIssueCreateVerifier(
        exact.SecSIssueCreateVerifierConfig(
            audience="devgraph://receiver-local",
            stable_issuer=STABLE_ISSUER,
            policy_registry=policy_registry
            or exact.SecSIssueCreatePolicyRegistry([_policy()]),
            key_registry=registry
            or exact.SecSVerifierKeyRegistry.from_json(
                _fixture_bytes("secs-public-key-registry.json")
            ),
            clock=lambda: now,
        )
    )


def _adapter(
    *,
    verifier: exact.SecSIssueCreateVerifier | None = None,
    audit_log: AuditLog | None = None,
    receipt_ids: tuple[str, ...] = ("receipt-1", "receipt-2", "receipt-3"),
) -> tuple[exact.SecSIssueCreateAdapter, MemoryGraphStorage, AuditLog]:
    storage = MemoryGraphStorage()
    audit = audit_log or AuditLog()
    ids = iter(receipt_ids)
    adapter = exact.SecSIssueCreateAdapter(
        verifier=verifier or _verifier(),
        storage=storage,
        audit_log=audit,
        receipt_id_factory=lambda: next(ids),
    )
    return adapter, storage, audit


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _with_nonzero_base64url_pad_bits(value: str) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    unused_bits = {2: 4, 3: 2}[len(value) % 4]
    index = alphabet.index(value[-1])
    mutated_index = (index >> unused_bits << unused_bits) | 1
    assert mutated_index != index
    return value[:-1] + alphabet[mutated_index]


def _generated_contract(
    request: dict[str, object],
    idempotency_key: str,
    *,
    private_key: Ed25519PrivateKey | None = None,
    key_id: str = "generated-key-v1",
    session_id: str = "AAECAwQFBgcICQoLDA0ODw",
    issued_at: int = EXPECTED_NOW,
    expires_at: int = EXPECTED_NOW + 60,
    projection_overrides: dict[str, object] | None = None,
) -> tuple[bytes, bytes, exact.SecSVerifierKey]:
    signing_key = private_key or Ed25519PrivateKey.generate()
    request_json = exact._canonical_json(request)
    issue, canonical_request = exact._parse_issue_create_request(request_json)
    projection = _fixture_json("unsigned-projection.json")
    projection.update(
        {
            "expires_at": expires_at,
            "idempotency_key_digest_sha256": hashlib.sha256(
                idempotency_key.encode("ascii")
            ).hexdigest(),
            "issued_at": issued_at,
            "request_digest_sha256": hashlib.sha256(
                exact.DEVGRAPH_ISSUE_CREATE_REQUEST_DOMAIN_V1 + canonical_request
            ).hexdigest(),
            "resource": f"Issue/{issue.id}",
            "secs_verifier_key_id": key_id,
            "session_id": session_id,
        }
    )
    projection.update(projection_overrides or {})
    signature = signing_key.sign(
        exact.DEVGRAPH_ISSUE_CREATE_SIGNATURE_DOMAIN_V1
        + exact._canonical_json(projection)
    )
    signed = {**projection, "secs_verifier_signature": _b64url(signature)}
    public_key = exact.SecSVerifierKey(
        key_id=key_id,
        public_key=signing_key.public_key().public_bytes_raw(),
    )
    return request_json, exact._canonical_json(signed), public_key


def _snapshot(storage: MemoryGraphStorage, audit: AuditLog) -> tuple[object, object, object]:
    return storage.query(), storage.list_edges(), list(audit.records)


def test_fixture_manifest_and_every_payload_digest_are_pinned() -> None:
    manifest_bytes = _fixture_bytes("manifest.json")
    assert hashlib.sha256(manifest_bytes).hexdigest() == (
        "040ddbbea7a62904ee20350a0395744b95904229141c99081304a8bff231b4ea"
    )
    manifest = json.loads(manifest_bytes)
    for item in manifest["files"]:
        assert hashlib.sha256(_fixture_bytes(item["path"])).hexdigest() == item["sha256"]


def test_exact_consumer_has_no_generic_authority_or_transport_input_seam() -> None:
    source = inspect.getsource(exact)
    adapter_parameters = inspect.signature(exact.SecSIssueCreateAdapter).parameters
    execute_parameters = inspect.signature(exact.SecSIssueCreateAdapter.execute).parameters
    assert set(adapter_parameters) == {
        "verifier",
        "storage",
        "audit_log",
        "receipt_id_factory",
    }
    assert set(execute_parameters) == {
        "self",
        "request_json",
        "idempotency_key",
        "signed_projection_json",
    }
    for forbidden in (
        "AuthorityContext",
        "CredentialEnvelope",
        "FastAPI",
        "APIRouter",
        "httpx",
        "LocalDevVerifier",
    ):
        assert forbidden not in source


def test_golden_request_and_canonicalization_boundaries_match_exact_vectors() -> None:
    _, canonical = exact._parse_issue_create_request(_fixture_bytes("request.json"))
    assert canonical == _fixture_bytes("canonical-request.json").removesuffix(b"\n")
    assert hashlib.sha256(
        exact.DEVGRAPH_ISSUE_CREATE_REQUEST_DOMAIN_V1 + canonical
    ).hexdigest() == "dd1f3ed1bdd7171956e51f81414bb4d790d44ae51339884c3667583793be0706"

    boundaries = _fixture_json("canonicalization-boundaries.json")
    for vector in boundaries["request_accept"]:  # type: ignore[index]
        request = exact._canonical_json(vector["materialized_request"])
        _, actual = exact._parse_issue_create_request(request)
        assert actual.decode("utf-8") == vector["canonical_json_utf8"]
        assert hashlib.sha256(
            exact.DEVGRAPH_ISSUE_CREATE_REQUEST_DOMAIN_V1 + actual
        ).hexdigest() == vector["request_digest_sha256"]

    for vector in boundaries["request_reject"]:  # type: ignore[index]
        raw = exact._canonical_json(
            {
                "id": "issue-boundary-reject",
                "kind": "Issue",
                "priority": vector["priority"],
                "title": "Rejected boundary",
            }
        )
        with pytest.raises(exact.SecSIssueCreateDenied):
            exact._parse_issue_create_request(raw)

    projection = _fixture_json("signed-projection.json")
    projection["issued_at"] = boundaries["canonical_nonnegative_integer_reject"][2][  # type: ignore[index]
        "value"
    ]
    with pytest.raises(exact.SecSIssueCreateDenied):
        _verifier().verify(
            request_json=_fixture_bytes("request.json"),
            idempotency_key=_raw_key(),
            signed_projection_json=exact._canonical_json(projection),
        )


def test_fixture_safe_integer_maximum_projection_and_policy_binding_are_accepted() -> None:
    boundaries = _fixture_json("canonicalization-boundaries.json")
    vector = boundaries["canonical_nonnegative_integer_accept"][2]  # type: ignore[index]
    integers = vector["integers"]
    maximum = int(integers["expires_at"])
    policy_version = int(integers["receiver_policy_version"])
    signing_key = Ed25519PrivateKey.generate()
    binding = exact.SecSIssueCreatePolicyBinding(
        policy_id=_policy().policy_id,
        policy_version=policy_version,
        policy_digest_sha256=_policy().policy_digest_sha256,
    )
    request, projection, verifier_key = _generated_contract(
        {"id": "issue-vector-max", "kind": "Issue", "title": "Vector max"},
        "fixture-maximum-key-0001",
        private_key=signing_key,
        issued_at=int(integers["issued_at"]),
        expires_at=maximum,
        projection_overrides={"receiver_policy_version": policy_version},
    )
    adapter, storage, _ = _adapter(
        verifier=_verifier(
            now=int(integers["issued_at"]),
            registry=exact.SecSVerifierKeyRegistry([verifier_key]),
            policy_registry=exact.SecSIssueCreatePolicyRegistry([binding]),
        )
    )
    result = adapter.execute(
        request_json=request,
        idempotency_key="fixture-maximum-key-0001",
        signed_projection_json=projection,
    )
    assert result.issue is not None
    assert len(storage.query("Issue")) == 1


def test_golden_projection_creates_one_issue_receipt_edge_and_safe_audit() -> None:
    adapter, storage, audit = _adapter()

    result = adapter.execute(
        request_json=_fixture_bytes("request.json"),
        idempotency_key=_raw_key(),
        signed_projection_json=_fixture_bytes("signed-projection.json"),
    )

    assert result.issue is not None and result.issue.id == "issue-golden"
    assert result.duplicate is False
    assert result.receipt.operation == exact.DEVGRAPH_ISSUE_CREATE_OPERATION_V1
    assert result.receipt.subject_label == "Issue"
    assert result.receipt.request_digest_sha256 == (
        "dd1f3ed1bdd7171956e51f81414bb4d790d44ae51339884c3667583793be0706"
    )
    assert result.receipt.idempotency_key_digest_sha256 == (
        "b2d81561ff6835c04849321c6c40ada8638e454f16694da2dabfaf4a32a754ea"
    )
    assert result.receipt.idempotency_key_digest is None
    assert result.receipt.correlation_id == (
        "dg:sha256:d3bfcfc7829aece79f74d643525406e6565a49a3f862e71739aea4fc87d5e337"
    )
    assert len(storage.query("Issue")) == len(storage.query(EVENT_RECEIPT_LABEL)) == 1
    assert len(storage.list_edges("EMITTED_EVENT")) == 1
    assert len(audit.records) == 1
    assert audit.records[0].safe_summary["duplicate"] is False
    assert audit.records[0].safe_summary["receipt_id"] == "receipt-1"
    receipt_projection = repr(result.receipt.to_node_properties())
    audit_projection = repr(audit.records[0])
    assert _raw_key() not in receipt_projection + audit_projection
    assert "Golden issue" not in receipt_projection + audit_projection
    assert "secs_verifier_signature" not in receipt_projection + audit_projection


def test_same_request_retry_is_duplicate_without_rewriting_receipt() -> None:
    adapter, storage, audit = _adapter()
    kwargs = {
        "request_json": _fixture_bytes("request.json"),
        "idempotency_key": _raw_key(),
        "signed_projection_json": _fixture_bytes("signed-projection.json"),
    }

    first = adapter.execute(**kwargs)
    second = adapter.execute(**kwargs)

    assert second.duplicate is True and second.issue is None
    assert second.receipt == first.receipt
    assert [record.safe_summary["duplicate"] for record in audit.records] == [False, True]
    assert len(storage.query("Issue")) == len(storage.query(EVENT_RECEIPT_LABEL)) == 1
    assert len(storage.list_edges("EMITTED_EVENT")) == 1


def test_concurrent_exact_retries_create_one_issue_receipt_and_edge() -> None:
    adapter, storage, audit = _adapter()

    def execute_once() -> exact.SecSIssueCreateResult:
        return adapter.execute(
            request_json=_fixture_bytes("request.json"),
            idempotency_key=_raw_key(),
            signed_projection_json=_fixture_bytes("signed-projection.json"),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: execute_once(), range(8)))

    assert sum(result.issue is not None for result in results) == 1
    assert sum(result.duplicate for result in results) == 7
    assert len({result.receipt.id for result in results}) == 1
    assert len(storage.query("Issue")) == len(storage.query(EVENT_RECEIPT_LABEL)) == 1
    assert len(storage.list_edges("EMITTED_EVENT")) == 1
    assert sum(record.safe_summary["duplicate"] is False for record in audit.records) == 1


def test_explicit_defaults_and_transport_key_order_share_request_identity() -> None:
    adapter, storage, _ = _adapter()
    first = adapter.execute(
        request_json=_fixture_bytes("request.json"),
        idempotency_key=_raw_key(),
        signed_projection_json=_fixture_bytes("signed-projection.json"),
    )
    equivalent = b'{ "priority":0, "title":"Golden issue", "kind":"Issue",' \
        b' "id":"issue-golden", "description":"", "external_link_ids":[],' \
        b' "artifact_ids":[] }'
    second = adapter.execute(
        request_json=equivalent,
        idempotency_key=_raw_key(),
        signed_projection_json=_fixture_bytes("signed-projection.json"),
    )
    assert second.duplicate is True
    assert second.receipt == first.receipt
    assert len(storage.query("Issue")) == 1


def test_changed_request_under_same_principal_and_key_conflicts_without_side_effect() -> None:
    signing_key = Ed25519PrivateKey.generate()
    raw_key = "changed-request-key-0001"
    first_request = {"id": "issue-scope", "kind": "Issue", "title": "First"}
    second_request = {"id": "issue-scope", "kind": "Issue", "title": "Changed"}
    request_a, projection_a, verifier_key = _generated_contract(
        first_request, raw_key, private_key=signing_key
    )
    request_b, projection_b, _ = _generated_contract(
        second_request,
        raw_key,
        private_key=signing_key,
        session_id="AQIDBAUGBwgJCgsMDQ4PEA",
    )
    adapter, storage, audit = _adapter(
        verifier=_verifier(registry=exact.SecSVerifierKeyRegistry([verifier_key]))
    )
    adapter.execute(
        request_json=request_a,
        idempotency_key=raw_key,
        signed_projection_json=projection_a,
    )
    before = _snapshot(storage, audit)

    with pytest.raises(IdempotencyScopeConflict):
        adapter.execute(
            request_json=request_b,
            idempotency_key=raw_key,
            signed_projection_json=projection_b,
        )

    assert _snapshot(storage, audit) == before


def test_verifier_key_and_session_rotation_preserve_receiver_principal_retry_identity() -> None:
    key_a = Ed25519PrivateKey.generate()
    key_b = Ed25519PrivateKey.generate()
    request = {"id": "issue-rotation", "kind": "Issue", "title": "Rotation"}
    raw_key = "rotation-retry-key-0001"
    request_a, projection_a, verifier_key_a = _generated_contract(
        request, raw_key, private_key=key_a, key_id="rotation-key-a"
    )
    request_b, projection_b, verifier_key_b = _generated_contract(
        request,
        raw_key,
        private_key=key_b,
        key_id="rotation-key-b",
        session_id="AQIDBAUGBwgJCgsMDQ4PEA",
    )
    adapter, storage, _ = _adapter(
        verifier=_verifier(
            registry=exact.SecSVerifierKeyRegistry([verifier_key_a, verifier_key_b])
        )
    )

    first = adapter.execute(
        request_json=request_a,
        idempotency_key=raw_key,
        signed_projection_json=projection_a,
    )
    second = adapter.execute(
        request_json=request_b,
        idempotency_key=raw_key,
        signed_projection_json=projection_b,
    )

    assert second.duplicate is True
    assert second.receipt.id == first.receipt.id
    assert len(storage.query("Issue")) == len(storage.query(EVENT_RECEIPT_LABEL)) == 1


def test_safe_integer_upper_bound_times_do_not_require_datetime_conversion() -> None:
    signing_key = Ed25519PrivateKey.generate()
    maximum = exact.DEVGRAPH_JSON_SAFE_INTEGER_MAX_V1
    request, projection, verifier_key = _generated_contract(
        {"id": "issue-max-time", "kind": "Issue", "title": "Max time"},
        "maximum-time-key-0001",
        private_key=signing_key,
        issued_at=maximum - 1,
        expires_at=maximum,
    )
    adapter, storage, _ = _adapter(
        verifier=_verifier(
            now=maximum - 1,
            registry=exact.SecSVerifierKeyRegistry([verifier_key]),
        )
    )

    adapter.execute(
        request_json=request,
        idempotency_key="maximum-time-key-0001",
        signed_projection_json=projection,
    )

    assert len(storage.query("Issue")) == 1


def test_maximum_length_issue_id_has_a_valid_exact_resource() -> None:
    signing_key = Ed25519PrivateKey.generate()
    issue_id = "a" * 256
    raw_key = "maximum-resource-key-0001"
    request, projection, verifier_key = _generated_contract(
        {"id": issue_id, "kind": "Issue", "title": "Max resource"},
        raw_key,
        private_key=signing_key,
    )
    adapter, storage, _ = _adapter(
        verifier=_verifier(registry=exact.SecSVerifierKeyRegistry([verifier_key]))
    )
    result = adapter.execute(
        request_json=request,
        idempotency_key=raw_key,
        signed_projection_json=projection,
    )
    assert result.issue is not None and result.issue.id == issue_id
    assert len(storage.query("Issue")) == 1


def test_excessive_json_nesting_and_clock_failure_are_bounded_causeless_denials() -> None:
    nested: object = "leaf"
    for _ in range(20):
        nested = [nested]
    request = json.dumps(
        {"id": "issue-depth", "kind": "Issue", "title": "Depth", "unknown": nested}
    ).encode()
    adapter, storage, audit = _adapter()
    with pytest.raises(exact.SecSIssueCreateDenied) as caught:
        adapter.execute(
            request_json=request,
            idempotency_key=_raw_key(),
            signed_projection_json=_fixture_bytes("signed-projection.json"),
        )
    assert caught.value.__cause__ is None
    assert _snapshot(storage, audit) == ([], [], [])

    def broken_clock() -> int:
        raise RuntimeError("secret=must-not-escape")

    config = exact.SecSIssueCreateVerifierConfig(
        audience="devgraph://receiver-local",
        stable_issuer=STABLE_ISSUER,
        policy_registry=exact.SecSIssueCreatePolicyRegistry([_policy()]),
        key_registry=exact.SecSVerifierKeyRegistry.from_json(
            _fixture_bytes("secs-public-key-registry.json")
        ),
        clock=broken_clock,
    )
    with pytest.raises(exact.SecSIssueCreateDenied) as clock_caught:
        exact.SecSIssueCreateVerifier(config).verify(
            request_json=_fixture_bytes("request.json"),
            idempotency_key=_raw_key(),
            signed_projection_json=_fixture_bytes("signed-projection.json"),
        )
    assert clock_caught.value.reason == "clock_unavailable"
    assert clock_caught.value.__cause__ is None
    assert "must-not-escape" not in repr(clock_caught.value)


@pytest.mark.parametrize(
    "request_json",
    [
        b'{"id":"issue-x","kind":"Issue","title":"x","title":"y"}',
        b'{"id":"issue-x","kind":"Issue","title":"x","unknown":true}',
        b'\xef\xbb\xbf{"id":"issue-x","kind":"Issue","title":"x"}',
        b'{"id":"issue-x","kind":"Issue","title":"x","priority":1.0}',
        b'{"id":"issue-x","kind":"Issue","title":"x","priority":true}',
        b'{"id":"issue-x","kind":"Issue","title":"\\ud800"}',
        b'{"id":"issue-x","kind":"Issue","title":"x"}\xff',
    ],
)
def test_malformed_requests_deny_with_zero_effects(request_json: bytes) -> None:
    adapter, storage, audit = _adapter()
    before = _snapshot(storage, audit)
    with pytest.raises(exact.SecSIssueCreateDenied):
        adapter.execute(
            request_json=request_json,
            idempotency_key=_raw_key(),
            signed_projection_json=_fixture_bytes("signed-projection.json"),
        )
    assert _snapshot(storage, audit) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("operation", "devgraph.issue.update.v1"),
        ("audience", "devgraph://other"),
        ("resource", "Issue/other"),
        ("request_digest_sha256", "A" * 64),
        ("session_id", "AAECAwQFBgcICQoLDA0OD="),
        ("expires_at", EXPECTED_NOW),
    ],
)
def test_projection_mutations_deny_with_zero_effects(field: str, value: object) -> None:
    projection = _fixture_json("signed-projection.json")
    projection[field] = value
    adapter, storage, audit = _adapter()
    before = _snapshot(storage, audit)
    with pytest.raises(exact.SecSIssueCreateDenied):
        adapter.execute(
            request_json=_fixture_bytes("request.json"),
            idempotency_key=_raw_key(),
            signed_projection_json=exact._canonical_json(projection),
        )
    assert _snapshot(storage, audit) == before


@pytest.mark.parametrize(
    ("issued_at", "expires_at", "now"),
    [
        (EXPECTED_NOW + 1, EXPECTED_NOW + 60, EXPECTED_NOW),
        (EXPECTED_NOW, EXPECTED_NOW, EXPECTED_NOW),
        (EXPECTED_NOW, EXPECTED_NOW + 61, EXPECTED_NOW),
        (EXPECTED_NOW - 1, EXPECTED_NOW, EXPECTED_NOW),
    ],
)
def test_locally_signed_time_denials_reach_the_current_authority_rule(
    issued_at: int,
    expires_at: int,
    now: int,
) -> None:
    raw_key = "signed-time-denial-key"
    request, projection, verifier_key = _generated_contract(
        {"id": "issue-time-denial", "kind": "Issue", "title": "Time denial"},
        raw_key,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    adapter, storage, audit = _adapter(
        verifier=_verifier(
            now=now,
            registry=exact.SecSVerifierKeyRegistry([verifier_key]),
        )
    )

    with pytest.raises(exact.SecSIssueCreateDenied) as caught:
        adapter.execute(
            request_json=request,
            idempotency_key=raw_key,
            signed_projection_json=projection,
        )

    assert caught.value.reason == "secs_authority_not_current"
    assert _snapshot(storage, audit) == ([], [], [])


@pytest.mark.parametrize(
    "projection_overrides",
    [
        {"audience": "devgraph://unknown-receiver"},
        {"receiver_policy_id": "unknown-policy"},
        {"receiver_policy_version": 2},
        {"receiver_policy_digest_sha256": "1" * 64},
    ],
)
def test_locally_signed_receiver_policy_denials_reach_policy_enforcement(
    projection_overrides: dict[str, object],
) -> None:
    raw_key = "signed-policy-denial-key"
    request, projection, verifier_key = _generated_contract(
        {
            "id": "issue-policy-denial",
            "kind": "Issue",
            "title": "Policy denial",
        },
        raw_key,
        projection_overrides=projection_overrides,
    )
    adapter, storage, audit = _adapter(
        verifier=_verifier(
            registry=exact.SecSVerifierKeyRegistry([verifier_key]),
        )
    )

    with pytest.raises(exact.SecSIssueCreateDenied) as caught:
        adapter.execute(
            request_json=request,
            idempotency_key=raw_key,
            signed_projection_json=projection,
        )

    assert caught.value.reason == "secs_receiver_policy_mismatch"
    assert _snapshot(storage, audit) == ([], [], [])


@pytest.mark.parametrize(
    ("projection_overrides", "reason"),
    [
        ({"resource": "Issue/other"}, "devgraph_resource_mismatch"),
        ({"request_digest_sha256": "1" * 64}, "devgraph_request_digest_mismatch"),
        (
            {"idempotency_key_digest_sha256": "1" * 64},
            "devgraph_idempotency_digest_mismatch",
        ),
    ],
)
def test_locally_signed_request_binding_denials_reach_the_exact_rule(
    projection_overrides: dict[str, object],
    reason: str,
) -> None:
    raw_key = "signed-binding-denial-key"
    request, projection, verifier_key = _generated_contract(
        {
            "id": "issue-binding-denial",
            "kind": "Issue",
            "title": "Binding denial",
        },
        raw_key,
        projection_overrides=projection_overrides,
    )
    adapter, storage, audit = _adapter(
        verifier=_verifier(
            registry=exact.SecSVerifierKeyRegistry([verifier_key]),
        )
    )

    with pytest.raises(exact.SecSIssueCreateDenied) as caught:
        adapter.execute(
            request_json=request,
            idempotency_key=raw_key,
            signed_projection_json=projection,
        )

    assert caught.value.reason == reason
    assert _snapshot(storage, audit) == ([], [], [])


@pytest.mark.parametrize(
    ("key_changes", "reason"),
    [
        ({"not_before": EXPECTED_NOW + 1}, "secs_verifier_key_not_yet_valid"),
        ({"not_after": EXPECTED_NOW}, "secs_verifier_key_expired"),
        ({"revoked_at": EXPECTED_NOW}, "secs_verifier_key_revoked"),
        ({"algorithm": "rsa"}, "untrusted_secs_verifier_key"),
        ({"status": "retired"}, "untrusted_secs_verifier_key"),
        ({"production_authority": False}, "untrusted_secs_verifier_key"),
    ],
)
def test_locally_signed_key_lifecycle_denials_reach_key_enforcement(
    key_changes: dict[str, object],
    reason: str,
) -> None:
    raw_key = "signed-key-lifecycle-denial"
    request, projection, verifier_key = _generated_contract(
        {
            "id": "issue-key-lifecycle",
            "kind": "Issue",
            "title": "Key lifecycle",
        },
        raw_key,
    )
    configured_key = replace(verifier_key, **key_changes)
    adapter, storage, audit = _adapter(
        verifier=_verifier(
            registry=exact.SecSVerifierKeyRegistry([configured_key]),
        )
    )

    with pytest.raises(exact.SecSIssueCreateDenied) as caught:
        adapter.execute(
            request_json=request,
            idempotency_key=raw_key,
            signed_projection_json=projection,
        )

    assert caught.value.reason == reason
    assert _snapshot(storage, audit) == ([], [], [])


@pytest.mark.parametrize(
    "verifier_key",
    [
        exact.SecSVerifierKey(
            key_id="secs-devgraph-authority-v1",
            public_key=b"\x00" * 32,
        ),
        exact.SecSVerifierKey(
            key_id="secs-devgraph-authority-v1",
            public_key=((1 << 255) - 19).to_bytes(32, "little"),
        ),
        replace(
            exact.SecSVerifierKeyRegistry.from_json(
                _fixture_bytes("secs-public-key-registry.json")
            ).require_production_key("secs-devgraph-authority-v1", now=EXPECTED_NOW),
            production_authority=False,
        ),
        replace(
            exact.SecSVerifierKeyRegistry.from_json(
                _fixture_bytes("secs-public-key-registry.json")
            ).require_production_key("secs-devgraph-authority-v1", now=EXPECTED_NOW),
            revoked_at=EXPECTED_NOW,
        ),
    ],
)
def test_wrong_small_order_or_untrusted_registry_key_denies_without_mutation(
    verifier_key: exact.SecSVerifierKey,
) -> None:
    adapter, storage, audit = _adapter(
        verifier=_verifier(registry=exact.SecSVerifierKeyRegistry([verifier_key]))
    )
    with pytest.raises(exact.SecSIssueCreateDenied):
        adapter.execute(
            request_json=_fixture_bytes("request.json"),
            idempotency_key=_raw_key(),
            signed_projection_json=_fixture_bytes("signed-projection.json"),
        )
    assert _snapshot(storage, audit) == ([], [], [])


@pytest.mark.parametrize("signature", [b"\x00" * 64, b"\x00" * 32 + b"\xff" * 32])
def test_small_order_or_noncanonical_signature_encoding_is_rejected(signature: bytes) -> None:
    projection = _fixture_json("signed-projection.json")
    projection["secs_verifier_signature"] = _b64url(signature)
    adapter, storage, audit = _adapter()
    with pytest.raises(exact.SecSIssueCreateDenied, match="invalid_secs_authority_signature"):
        adapter.execute(
            request_json=_fixture_bytes("request.json"),
            idempotency_key=_raw_key(),
            signed_projection_json=exact._canonical_json(projection),
        )
    assert _snapshot(storage, audit) == ([], [], [])


def test_identity_r_and_noncanonical_s_are_rejected_before_openssl_verification() -> None:
    projection = _fixture_json("signed-projection.json")
    golden = exact._decode_base64url(
        projection["secs_verifier_signature"],
        encoded_length=86,
        decoded_length=64,
        reason="invalid",
    )
    identity = b"\x01" + b"\x00" * 31
    scalar_order = (1 << 252) + 27742317777372353535851937790883648493
    invalid_signatures = (
        identity + b"\x00" * 32,
        golden[:32] + scalar_order.to_bytes(32, "little"),
    )
    for invalid_signature in invalid_signatures:
        mutated = {**projection, "secs_verifier_signature": _b64url(invalid_signature)}
        adapter, storage, audit = _adapter()
        with pytest.raises(
            exact.SecSIssueCreateDenied,
            match="invalid_secs_authority_signature",
        ):
            adapter.execute(
                request_json=_fixture_bytes("request.json"),
                idempotency_key=_raw_key(),
                signed_projection_json=exact._canonical_json(mutated),
            )
        assert _snapshot(storage, audit) == ([], [], [])


def test_identity_point_universal_ed25519_forgery_is_rejected_without_mutation() -> None:
    identity = b"\x01" + b"\x00" * 31
    forged_key = exact.SecSVerifierKey(
        key_id="forged-identity-key",
        public_key=identity,
    )
    projection = _fixture_json("unsigned-projection.json")
    projection["secs_verifier_key_id"] = forged_key.key_id
    projection["secs_verifier_signature"] = _b64url(identity + b"\x00" * 32)
    adapter, storage, audit = _adapter(
        verifier=_verifier(registry=exact.SecSVerifierKeyRegistry([forged_key]))
    )

    with pytest.raises(exact.SecSIssueCreateDenied, match="untrusted_secs_verifier_key"):
        adapter.execute(
            request_json=_fixture_bytes("request.json"),
            idempotency_key=_raw_key(),
            signed_projection_json=exact._canonical_json(projection),
        )

    assert _snapshot(storage, audit) == ([], [], [])


def test_duplicate_receiver_policy_binding_fails_closed() -> None:
    binding = _policy()
    verifier = exact.SecSIssueCreateVerifier(
        exact.SecSIssueCreateVerifierConfig(
            audience="devgraph://receiver-local",
            stable_issuer=STABLE_ISSUER,
            policy_registry=exact.SecSIssueCreatePolicyRegistry([binding, binding]),
            key_registry=exact.SecSVerifierKeyRegistry.from_json(
                _fixture_bytes("secs-public-key-registry.json")
            ),
            clock=lambda: EXPECTED_NOW,
        )
    )
    adapter, storage, audit = _adapter(verifier=verifier)
    with pytest.raises(exact.SecSIssueCreateDenied, match="secs_receiver_policy_mismatch"):
        adapter.execute(
            request_json=_fixture_bytes("request.json"),
            idempotency_key=_raw_key(),
            signed_projection_json=_fixture_bytes("signed-projection.json"),
        )
    assert _snapshot(storage, audit) == ([], [], [])


def test_internal_verified_state_repr_does_not_include_work_content() -> None:
    grant = _verifier().verify(
        request_json=_fixture_bytes("request.json"),
        idempotency_key=_raw_key(),
        signed_projection_json=_fixture_bytes("signed-projection.json"),
    )
    assert "Golden issue" not in repr(grant)


def test_audit_failure_rolls_back_issue_receipt_and_edge() -> None:
    class FailingAuditLog(AuditLog):
        def record(self, **kwargs: object):  # type: ignore[no-untyped-def]
            raise RuntimeError("audit unavailable")

    failing_audit = FailingAuditLog()
    adapter, storage, audit = _adapter(audit_log=failing_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        adapter.execute(
            request_json=_fixture_bytes("request.json"),
            idempotency_key=_raw_key(),
            signed_projection_json=_fixture_bytes("signed-projection.json"),
        )
    assert audit is failing_audit
    assert _snapshot(storage, audit) == ([], [], [])


def test_graph_session_is_immutable_single_use_and_has_no_broad_work_methods() -> None:
    grant = _verifier().verify(
        request_json=_fixture_bytes("request.json"),
        idempotency_key=_raw_key(),
        signed_projection_json=_fixture_bytes("signed-projection.json"),
    )
    storage = MemoryGraphStorage()
    graph = exact._SecSIssueCreateGraph(storage, AuditLog())
    session = graph._open_session(grant, exact._ISSUE_CREATE_ADAPTER)
    public_methods = {
        name
        for name in dir(session)
        if not name.startswith("_") and callable(getattr(session, name))
    }
    assert public_methods == {"create_issue"}
    with pytest.raises(AttributeError):
        session.extra = object()  # type: ignore[attr-defined]
    assert session.create_issue().id == "issue-golden"
    with pytest.raises(ForbiddenError):
        session.create_issue()


def test_duplicate_registry_key_id_fails_closed() -> None:
    key = exact.SecSVerifierKeyRegistry.from_json(
        _fixture_bytes("secs-public-key-registry.json")
    ).require_production_key("secs-devgraph-authority-v1", now=EXPECTED_NOW)
    adapter, storage, audit = _adapter(
        verifier=_verifier(registry=exact.SecSVerifierKeyRegistry([key, key]))
    )
    with pytest.raises(exact.SecSIssueCreateDenied, match="unknown_secs_verifier_key"):
        adapter.execute(
            request_json=_fixture_bytes("request.json"),
            idempotency_key=_raw_key(),
            signed_projection_json=_fixture_bytes("signed-projection.json"),
        )
    assert _snapshot(storage, audit) == ([], [], [])


def test_raw_request_projection_and_registry_byte_caps_are_exact() -> None:
    request = _fixture_bytes("request.json")
    projection = _fixture_bytes("signed-projection.json")
    registry = _fixture_bytes("secs-public-key-registry.json")

    request_at_limit = request + b" " * (131_072 - len(request))
    projection_at_limit = projection + b" " * (16_384 - len(projection))
    registry_at_limit = registry + b" " * (262_144 - len(registry))
    assert len(request_at_limit) == 131_072
    assert len(projection_at_limit) == 16_384
    assert len(registry_at_limit) == 262_144

    adapter, storage, _ = _adapter()
    result = adapter.execute(
        request_json=request_at_limit,
        idempotency_key=_raw_key(),
        signed_projection_json=projection_at_limit,
    )
    assert result.issue is not None
    assert len(storage.query("Issue")) == 1
    exact.SecSVerifierKeyRegistry.from_json(registry_at_limit)

    for oversized_request, oversized_projection in (
        (request_at_limit + b" ", projection),
        (request, projection_at_limit + b" "),
    ):
        rejected, rejected_storage, rejected_audit = _adapter()
        with pytest.raises(exact.SecSIssueCreateDenied):
            rejected.execute(
                request_json=oversized_request,
                idempotency_key=_raw_key(),
                signed_projection_json=oversized_projection,
            )
        assert _snapshot(rejected_storage, rejected_audit) == ([], [], [])

    with pytest.raises(ValueError, match="invalid secS verifier key registry"):
        exact.SecSVerifierKeyRegistry.from_json(registry_at_limit + b" ")


def test_materialized_canonical_request_cap_accepts_65536_and_rejects_65537() -> None:
    raw_key = "canonical-request-limit-key"
    base_request = {
        "id": "issue-canonical-limit",
        "kind": "Issue",
        "title": "Canonical limit",
        "description": "",
    }
    _, base_canonical = exact._parse_issue_create_request(
        exact._canonical_json(base_request)
    )
    description_length = 65_536 - len(base_canonical)
    at_limit_request = {**base_request, "description": "x" * description_length}
    request, projection, verifier_key = _generated_contract(
        at_limit_request,
        raw_key,
    )
    _, canonical = exact._parse_issue_create_request(request)
    assert len(canonical) == 65_536
    adapter, storage, _ = _adapter(
        verifier=_verifier(
            registry=exact.SecSVerifierKeyRegistry([verifier_key]),
        )
    )
    assert adapter.execute(
        request_json=request,
        idempotency_key=raw_key,
        signed_projection_json=projection,
    ).issue is not None
    assert len(storage.query("Issue")) == 1

    over_limit_request = {
        **base_request,
        "description": "x" * (description_length + 1),
    }
    with pytest.raises(
        exact.SecSIssueCreateDenied,
        match="invalid_devgraph_issue_create_request",
    ):
        exact._parse_issue_create_request(exact._canonical_json(over_limit_request))


@pytest.mark.parametrize(
    "projection_json",
    [
        _fixture_bytes("signed-projection.json").rstrip()[:-1]
        + b',"operation":"devgraph.issue.create.v1"}',
        _fixture_bytes("signed-projection.json").rstrip()[:-1]
        + b',"unknown":true}',
        exact._canonical_json(
            {
                key: value
                for key, value in _fixture_json("signed-projection.json").items()
                if key != "operation"
            }
        ),
        _fixture_bytes("signed-projection.json") + b"x",
    ],
)
def test_projection_duplicate_unknown_missing_and_trailing_data_are_rejected(
    projection_json: bytes,
) -> None:
    adapter, storage, audit = _adapter()
    with pytest.raises(exact.SecSIssueCreateDenied):
        adapter.execute(
            request_json=_fixture_bytes("request.json"),
            idempotency_key=_raw_key(),
            signed_projection_json=projection_json,
        )
    assert _snapshot(storage, audit) == ([], [], [])


@pytest.mark.parametrize(
    ("field", "mutation"),
    [
        ("session_id", "padding"),
        ("session_id", "forbidden"),
        ("session_id", "pad_bits"),
        ("nonce", "padding"),
        ("nonce", "forbidden"),
        ("secs_verifier_signature", "padding"),
        ("secs_verifier_signature", "forbidden"),
        ("secs_verifier_signature", "pad_bits"),
    ],
)
def test_projection_base64url_is_canonical_no_pad(
    field: str,
    mutation: str,
) -> None:
    projection = _fixture_json("signed-projection.json")
    original = str(projection[field])
    if mutation == "padding":
        projection[field] = original + "="
    elif mutation == "forbidden":
        projection[field] = "+" + original[1:]
    else:
        projection[field] = _with_nonzero_base64url_pad_bits(original)
    adapter, storage, audit = _adapter()
    with pytest.raises(exact.SecSIssueCreateDenied):
        adapter.execute(
            request_json=_fixture_bytes("request.json"),
            idempotency_key=_raw_key(),
            signed_projection_json=exact._canonical_json(projection),
        )
    assert _snapshot(storage, audit) == ([], [], [])


@pytest.mark.parametrize("mutation", ["padding", "forbidden", "pad_bits"])
def test_registry_public_key_base64url_is_canonical_no_pad(mutation: str) -> None:
    registry = _fixture_json("secs-public-key-registry.json")
    key = registry["keys"][0]  # type: ignore[index]
    original = str(key["public_key_base64url"])
    if mutation == "padding":
        key["public_key_base64url"] = original + "="
    elif mutation == "forbidden":
        key["public_key_base64url"] = "/" + original[1:]
    else:
        key["public_key_base64url"] = _with_nonzero_base64url_pad_bits(original)

    with pytest.raises(ValueError, match="invalid secS verifier key registry"):
        exact.SecSVerifierKeyRegistry.from_json(exact._canonical_json(registry))


@pytest.mark.parametrize(
    ("idempotency_key", "accepted"),
    [
        ("a" * 15, False),
        ("a" * 16, True),
        ("a" * 128, True),
        ("a" * 129, False),
        ("a" * 15 + "!", False),
    ],
)
def test_idempotency_key_grammar_and_length_boundaries(
    idempotency_key: str,
    accepted: bool,
) -> None:
    request, projection, verifier_key = _generated_contract(
        {
            "id": "issue-idempotency-boundary",
            "kind": "Issue",
            "title": "Idempotency boundary",
        },
        idempotency_key,
    )
    adapter, storage, audit = _adapter(
        verifier=_verifier(
            registry=exact.SecSVerifierKeyRegistry([verifier_key]),
        )
    )

    if accepted:
        result = adapter.execute(
            request_json=request,
            idempotency_key=idempotency_key,
            signed_projection_json=projection,
        )
        assert result.issue is not None
        assert len(storage.query("Issue")) == 1
    else:
        with pytest.raises(
            exact.SecSIssueCreateDenied,
            match="invalid_devgraph_idempotency_key",
        ):
            adapter.execute(
                request_json=request,
                idempotency_key=idempotency_key,
                signed_projection_json=projection,
            )
        assert _snapshot(storage, audit) == ([], [], [])
