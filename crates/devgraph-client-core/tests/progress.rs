use devgraph_client_core::PreparedMutation;
use serde_json::Value;
#[test]
fn progress_vectors_prepare_without_changing_bytes() {
    let vectors: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/progress-requests.json")).unwrap();
    for case in vectors {
        let prepared = PreparedMutation::parse(
            case["raw"].as_str().unwrap().as_bytes(),
            "progress-test-key-0001",
        )
        .unwrap();
        let typed = PreparedMutation::from_value(
            &serde_json::from_str(case["raw"].as_str().unwrap()).unwrap(),
            "progress-test-key-0001",
        )
        .unwrap();
        assert_eq!(typed.canonical(), prepared.canonical());
        assert_eq!(
            prepared.canonical(),
            case["canonical"].as_str().unwrap().as_bytes()
        );
        assert_eq!(prepared.summary()["request_digest_sha256"], case["digest"]);
    }
}

#[test]
fn python_receiver_results_decode_and_mismatches_fail_closed() {
    use devgraph_client_core::ResponseMetadata;
    use serde_json::json;
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/progress-results.json")).unwrap();
    for case in cases {
        let prepared =
            PreparedMutation::from_value(&case["request"], "progress-test-key-0001").unwrap();
        let metadata = ResponseMetadata {
            status: case["status"].as_u64().unwrap() as u16,
            content_type: "application/json".into(),
            content_encoding: Some("identity".into()),
            max_bytes: None,
        };
        let decode = |value: &Value| {
            prepared.decode_response_value(&serde_json::to_vec(value).unwrap(), &metadata)
        };
        assert_eq!(decode(&case["result"]).unwrap(), case["result"]);
        for (field, value) in [
            ("version", json!("999")),
            ("progress", json!("archived")),
            ("key", json!("Todo/another")),
            ("classification_required", json!(true)),
        ] {
            let mut bad = case["result"].clone();
            bad["work"][field] = value;
            assert!(decode(&bad).is_err(), "{field}");
        }
        let mut bad = case["result"].clone();
        bad["work"]["stage"] = json!("implementing_commit");
        bad["work"]["workflow_id"] = json!("execution.v1");
        assert!(decode(&bad).is_err());
        for key in ["progress", "workflow_id", "stage", "parent"] {
            let mut bad = case["result"].clone();
            bad["work"].as_object_mut().unwrap().remove(key);
            assert!(decode(&bad).is_err(), "missing {key}");
        }
    }
}

#[test]
fn canonical_todo_read_uses_new_route_and_legacy_read_stays_v1() {
    use devgraph_client_core::{prepare_read, ResponseMetadata};
    let read = prepare_read(br#"{"kind":"get_todo","work_kind":"Todo","id":"example"}"#).unwrap();
    assert_eq!(read.path(), "/todos/v2/Todo/example");
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/progress-results.json")).unwrap();
    let metadata = ResponseMetadata {
        status: 200,
        content_type: "application/json".into(),
        content_encoding: None,
        max_bytes: None,
    };
    let value = &cases[0]["result"]["work"];
    assert_eq!(
        read.decode_response(&serde_json::to_vec(value).unwrap(), &metadata)
            .unwrap(),
        *value
    );
    assert_eq!(
        prepare_read(br#"{"kind":"get_work","work_kind":"Task","id":"example"}"#)
            .unwrap()
            .path(),
        "/work/Task/example"
    );
    assert!(prepare_read(br#"{"kind":"get_todo","work_kind":"Actor","id":"example"}"#).is_err());
}

#[test]
fn credential_transport_preserves_raw_json_and_requires_status_only_receipt() {
    use devgraph_client_core::ResponseMetadata;
    use serde_json::json;
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/progress-results.json")).unwrap();
    let meta = ResponseMetadata {
        status: 200,
        content_type: "application/json".into(),
        content_encoding: None,
        max_bytes: None,
    };
    for case in cases {
        let prepared =
            PreparedMutation::from_value(&case["request"], "progress-test-key-0001").unwrap();
        let mut wrapped = case["result"].clone();
        wrapped["state"] = json!("committed");
        let raw = serde_json::to_vec(&wrapped).unwrap();
        assert_eq!(
            prepared
                .decode_credential_response(&raw, &meta, false)
                .unwrap(),
            case["result"]
        );
        let duplicate = format!(
            "{{\"state\":\"committed\",{}",
            std::str::from_utf8(&raw[1..]).unwrap()
        );
        assert!(prepared
            .decode_credential_response(duplicate.as_bytes(), &meta, false)
            .is_err());
        let mut status = json!({"state":"committed","receipt":case["result"]["receipt"]});
        assert!(prepared
            .decode_credential_response(&serde_json::to_vec(&status).unwrap(), &meta, true)
            .is_err());
        status["receipt"]["duplicate"] = json!(true);
        let recovered = prepared
            .decode_credential_response(&serde_json::to_vec(&status).unwrap(), &meta, true)
            .unwrap();
        assert_eq!(recovered["schema"], "devgraph.work-result.v2");
        assert!(recovered["work"].is_null());
        status["work"] = case["result"]["work"].clone();
        assert!(prepared
            .decode_credential_response(&serde_json::to_vec(&status).unwrap(), &meta, true)
            .is_err());
    }
}
