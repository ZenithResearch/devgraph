use devgraph_client_core::*;
use serde_json::{json, Value};

fn fixture() -> Value {
    serde_json::from_str(include_str!("fixtures/http-responses.json")).unwrap()
}
fn metadata(status: u16) -> ResponseMetadata {
    ResponseMetadata {
        status,
        content_type: "application/json".into(),
        content_encoding: None,
        max_bytes: None,
    }
}
fn prepared() -> PreparedMutation {
    let f = fixture();
    PreparedMutation::parse(
        f["request"].as_str().unwrap().as_bytes(),
        f["idempotency_key"].as_str().unwrap(),
    )
    .unwrap()
}
fn read(input: Value) -> PreparedRead {
    prepare_read(&serde_json::to_vec(&input).unwrap()).unwrap()
}

#[test]
fn python_http_response_fixtures() {
    let f = fixture();
    for name in ["create", "duplicate"] {
        let response = &f["responses"][name];
        let decoded = prepared()
            .decode_response(
                response["body"].as_str().unwrap().as_bytes(),
                &metadata(response["status"].as_u64().unwrap() as u16),
            )
            .unwrap();
        assert_eq!(decoded.receipt.duplicate, name == "duplicate");
        assert_eq!(decoded.work.is_none(), name == "duplicate");
        assert_eq!(decoded.receipt.receipt_status, "pending");
    }
    for (name, input) in [
        (
            "get",
            json!({"kind":"get_work","work_kind":"Issue","id":"test-one"}),
        ),
        ("list", json!({"kind":"list_work","work_kind":"Issue"})),
        (
            "relations",
            json!({"kind":"list_relations","subject":{"kind":"Issue","id":"test-one"},"relation_kind":"children"}),
        ),
    ] {
        let result = read(input).decode_response(
            f["responses"][name]["body"].as_str().unwrap().as_bytes(),
            &metadata(200),
        );
        assert!(result.is_ok(), "{name}: {result:?}");
    }
}

#[test]
fn profile_matches_python_shared_corpus() {
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/sdk-receiver-profile-v1.json"
    ))
    .unwrap();
    for vector in corpus["vectors"].as_array().unwrap() {
        assert_eq!(
            receiver_profile_digest(vector["profile"]["stable_issuer"].as_str().unwrap()).unwrap(),
            vector["digest_sha256"]
        );
    }
    for issuer in ["", "\n", "secs:é", "secs:test\n"] {
        assert!(receiver_profile_digest(issuer).is_err());
    }
}

#[test]
fn integer_tokens_are_lossless_under_all_serde_features() {
    let parsed = parse_response_json(
        b"[-9223372036854775808,9223372036854775807,18446744073709551615]",
        1000,
        64,
    )
    .unwrap();
    assert_eq!(parsed[0].as_i64(), Some(i64::MIN));
    assert_eq!(parsed[1].as_i64(), Some(i64::MAX));
    assert_eq!(parsed[2].as_u64(), Some(u64::MAX));
    for raw in [
        b"18446744073709551616".as_slice(),
        b"-9223372036854775809",
        b"1.0",
        b"1e3",
        b"01",
        b"NaN",
        b"[1,]",
        b"{\"a\":1,}",
        b"{\"a\":1,\"a\":2}",
        b"\"\\ud800\"",
        b"\"\xff\"",
    ] {
        assert!(parse_response_json(raw, 1000, 64).is_err(), "{raw:?}");
    }
    assert_eq!(
        parse_response_json(b"\"\\ud83d\\ude80\"", 100, 64).unwrap(),
        "🚀"
    );
    assert_eq!(
        parse_response_json(&vec![b' '; 1001], 1000, 64)
            .unwrap_err()
            .kind,
        ErrorKind::ResourceLimit
    );
    let deep = format!("{}0{}", "[".repeat(65), "]".repeat(65));
    assert_eq!(
        parse_response_json(deep.as_bytes(), 1000, 64)
            .unwrap_err()
            .code,
        "response_too_deep"
    );
}

#[test]
fn work_numbers_and_identity_are_checked_before_exposure() {
    let f = fixture();
    let mut value: Value =
        serde_json::from_str(f["responses"]["get"]["body"].as_str().unwrap()).unwrap();
    value["version"] = Value::from(i64::MAX);
    value["priority"] = Value::from(i64::MIN);
    let descriptor = read(json!({"kind":"get_work","work_kind":"Issue","id":"test-one"}));
    let raw = serde_json::to_vec(&value).unwrap();
    let decoded = descriptor.decode_response(&raw, &metadata(200)).unwrap();
    assert_eq!(decoded["version"].as_i64(), Some(i64::MAX));
    assert_eq!(decoded["priority"].as_i64(), Some(i64::MIN));
    for (field, bad) in [
        ("version", json!(0)),
        ("version", json!(true)),
        ("priority", json!(u64::MAX)),
        ("id", json!("other")),
        ("kind", json!("Task")),
        ("extra", json!(1)),
    ] {
        let mut bad_value = value.clone();
        bad_value[field] = bad;
        assert!(descriptor
            .decode_response(&serde_json::to_vec(&bad_value).unwrap(), &metadata(200))
            .is_err());
    }
}

#[test]
fn mutation_shape_status_subject_and_version_are_bound() {
    let f = fixture();
    let value: Value =
        serde_json::from_str(f["responses"]["create"]["body"].as_str().unwrap()).unwrap();
    for (pointer, bad) in [
        ("/receipt/operation", json!("devgraph.work.patch.v1")),
        ("/receipt/subject_id", json!("other")),
        ("/receipt/duplicate", json!(true)),
        ("/work", Value::Null),
        ("/work/version", json!(2)),
        ("/work/id", json!("other")),
        ("/receipt/correlation_id", json!("invalid")),
    ] {
        let mut changed = value.clone();
        *changed.pointer_mut(pointer).unwrap() = bad;
        assert!(
            prepared()
                .decode_response(&serde_json::to_vec(&changed).unwrap(), &metadata(201))
                .is_err(),
            "{pointer}"
        );
    }
    assert!(prepared()
        .decode_response(&serde_json::to_vec(&value).unwrap(), &metadata(200))
        .is_err());
    let mut missing = value.clone();
    missing.as_object_mut().unwrap().remove("work");
    assert!(prepared()
        .decode_response(&serde_json::to_vec(&missing).unwrap(), &metadata(201))
        .is_err());
}

#[test]
fn only_documented_precommit_problems_are_definitive_rejections() {
    for (status, title, kind) in [
        (403, "Named Work authority denied", ErrorKind::Rejected),
        (412, "Version precondition failed", ErrorKind::Rejected),
        (409, "Idempotency scope conflict", ErrorKind::Rejected),
        (503, "Audit unavailable", ErrorKind::Transport),
        (400, "Unrecognized error", ErrorKind::Transport),
    ] {
        let mut metadata = metadata(status);
        metadata.content_type = "application/problem+json".into();
        let body = serde_json::to_vec(
            &json!({"title":title,"status":status,"detail":"secret must never escape"}),
        )
        .unwrap();
        let error = prepared().decode_response(&body, &metadata).unwrap_err();
        assert_eq!(error.kind, kind);
        assert!(!format!("{error:?}").contains("secret"));
    }
    let mut meta = metadata(403);
    meta.content_type = "application/problem+json".into();
    assert_eq!(
        prepared()
            .decode_response(
                b"{\"title\":\"Named Work authority denied\",\"status\":503,\"detail\":\"\"}",
                &meta
            )
            .unwrap_err()
            .kind,
        ErrorKind::Protocol
    );
}

#[test]
fn descriptors_are_closed_fixed_receiver_requests() {
    let descriptor = read(
        json!({"kind":"list_work","work_kind":"Issue","filters":{"include_archived":true,"descending":true},"limit":7,"after_id":"last"}),
    );
    assert_eq!(descriptor.method(), "GET");
    assert_eq!(
        descriptor.path(),
        "/work/Issue?include_archived=true&descending=true&limit=7&after_id=last"
    );
    let descriptor = read(
        json!({"kind":"list_relations","subject":{"kind":"Task","id":"task"},"relation_kind":"blockers","after_resource":"Task/last"}),
    );
    assert!(descriptor.path().ends_with("after_resource=Task%2Flast"));
    for input in [
        json!({"kind":"get_work","work_kind":"Issue","id":"../secrets"}),
        json!({"kind":"get_work","work_kind":"Issue","id":"id","url":"https://evil"}),
        json!({"kind":"list_work","work_kind":"Issue","limit":65537}),
        json!({"kind":"list_work","work_kind":"Issue","filters":{"credential":"secret"}}),
    ] {
        assert!(prepare_read(&serde_json::to_vec(&input).unwrap()).is_err());
    }
}

#[test]
fn pages_must_advance_past_cursor_without_duplicates() {
    let f = fixture();
    let work: Value =
        serde_json::from_str(f["responses"]["get"]["body"].as_str().unwrap()).unwrap();
    let descriptor = read(json!({"kind":"list_work","work_kind":"Issue","after_id":"test-one"}));
    assert!(descriptor
        .decode_response(
            &serde_json::to_vec(&json!({"items":[work.clone()]})).unwrap(),
            &metadata(200)
        )
        .is_err());
    let descriptor = read(json!({"kind":"list_work","work_kind":"Issue"}));
    assert!(descriptor
        .decode_response(
            &serde_json::to_vec(&json!({"items":[work.clone(),work]})).unwrap(),
            &metadata(200)
        )
        .is_err());
}

#[test]
fn content_encoding_media_type_status_and_caps_are_enforced() {
    let f = fixture();
    let raw = f["responses"]["get"]["body"].as_str().unwrap().as_bytes();
    let descriptor = read(json!({"kind":"get_work","work_kind":"Issue","id":"test-one"}));
    let mut meta = metadata(200);
    meta.content_encoding = Some("gzip".into());
    assert_eq!(
        descriptor.decode_response(raw, &meta).unwrap_err().code,
        "unsupported_content_encoding"
    );
    meta.content_encoding = None;
    meta.content_type = "text/plain".into();
    assert_eq!(
        descriptor.decode_response(raw, &meta).unwrap_err().code,
        "invalid_content_type"
    );
    meta.content_type = "application/json; charset=utf-8".into();
    meta.max_bytes = Some(raw.len() - 1);
    assert_eq!(
        descriptor.decode_response(raw, &meta).unwrap_err().kind,
        ErrorKind::ResourceLimit
    );
    meta.max_bytes = Some(MAX_LIMIT + 1);
    assert_eq!(
        descriptor.decode_response(raw, &meta).unwrap_err().code,
        "invalid_response_limit"
    );
}

#[test]
fn cypher_result_matches_explicit_columns_types_and_limit() {
    use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
    let request = json!({"schema":"devgraph.cypher-read-request.v1","query":"MATCH (i:Issue) RETURN i.version AS version LIMIT 2","parameters":{}});
    let descriptor = read(
        json!({"kind":"cypher","request_b64":URL_SAFE_NO_PAD.encode(serde_json::to_vec(&request).unwrap()),"expected_result":{"columns":["version"],"types":["integer"],"limit":2}}),
    );
    assert_eq!(descriptor.method(), "POST");
    assert_eq!(descriptor.path(), "/query/cypher");
    let value = json!({"schema":"devgraph.cypher-read-result.v1","columns":["version"],"rows":[[i64::MAX]],"row_count":1,"limit":2});
    assert!(descriptor
        .decode_response(&serde_json::to_vec(&value).unwrap(), &metadata(200))
        .is_ok());
    for (field, bad) in [
        ("limit", json!(3)),
        ("columns", json!(["priority"])),
        ("rows", json!([["9223372036854775807"]])),
        ("row_count", json!(0)),
    ] {
        let mut changed = value.clone();
        changed[field] = bad;
        assert!(descriptor
            .decode_response(&serde_json::to_vec(&changed).unwrap(), &metadata(200))
            .is_err());
    }
}

#[test]
fn typed_builder_and_shared_raw_protocol_have_identical_canonical_requests() {
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/sdk-work-v1/requests.json"
    ))
    .unwrap();
    for vector in corpus.as_array().unwrap() {
        let raw = vector["raw"].as_str().unwrap();
        let value: Value = serde_json::from_str(raw).unwrap();
        let raw_prepared = PreparedMutation::parse(raw.as_bytes(), "sdk-fixture-key-0001").unwrap();
        let typed_prepared = PreparedMutation::from_value(&value, "sdk-fixture-key-0001").unwrap();
        assert_eq!(raw_prepared.canonical(), typed_prepared.canonical());
    }
    let f = fixture();
    let mut input: Value = serde_json::from_str(f["request"].as_str().unwrap()).unwrap();
    input["payload"]["description"] = Value::Null;
    assert!(PreparedMutation::from_value(&input, "sdk-fixture-key-0001").is_err());
    input["payload"]
        .as_object_mut()
        .unwrap()
        .remove("description");
    input["unexpected"] = Value::Null;
    assert!(PreparedMutation::from_value(&input, "sdk-fixture-key-0001").is_err());
}

#[test]
fn every_named_operation_decodes_real_python_receiver_output() {
    let corpus: Value =
        serde_json::from_str(include_str!("fixtures/http-operation-responses.json")).unwrap();
    let mut operations = std::collections::BTreeSet::new();
    for vector in corpus["vectors"].as_array().unwrap() {
        let prepared = PreparedMutation::parse(
            vector["request"].as_str().unwrap().as_bytes(),
            vector["idempotency_key"].as_str().unwrap(),
        )
        .unwrap();
        operations.insert(prepared.request().operation().to_string());
        let result = prepared.decode_response(
            vector["body"].as_str().unwrap().as_bytes(),
            &metadata(vector["status"].as_u64().unwrap() as u16),
        );
        assert!(result.is_ok(), "{}: {result:?}", vector["name"]);
    }
    assert_eq!(operations.len(), 11);
}

#[test]
fn arena_protocol_support_does_not_expand_sdk_work_result_contract() {
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/arena-v1/requests.json"
    ))
    .unwrap();
    for vector in corpus.as_array().unwrap() {
        let raw = vector["raw"].as_str().unwrap().as_bytes();
        let parsed = devgraph_client_core::protocol::WorkRequest::parse(raw).unwrap();
        let prepared = PreparedMutation::parse(raw, "sdk-fixture-key-0001");
        if parsed.value()["schema"] == "devgraph.arena-request.v1" {
            assert!(prepared.is_err());
        } else {
            assert!(prepared.is_ok());
        }
    }
}

#[test]
fn workflow_typed_builders_match_frozen_canonical_requests() {
    let corpus: Value =
        serde_json::from_str(include_str!("fixtures/workflow-requests.json")).unwrap();
    for vector in corpus.as_array().unwrap() {
        let raw = vector["raw"].as_str().unwrap();
        let value: Value = serde_json::from_str(raw).unwrap();
        let raw_prepared =
            PreparedMutation::parse(raw.as_bytes(), "sdk-workflow-key-0001").unwrap();
        let typed_prepared = PreparedMutation::from_value(&value, "sdk-workflow-key-0001").unwrap();
        assert_eq!(
            raw_prepared.canonical(),
            typed_prepared.canonical(),
            "{}",
            vector["name"]
        );
        assert_eq!(
            std::str::from_utf8(raw_prepared.canonical()).unwrap(),
            vector["canonical"].as_str().unwrap()
        );
    }
}
