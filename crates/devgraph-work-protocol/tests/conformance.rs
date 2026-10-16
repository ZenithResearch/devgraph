use devgraph_work_protocol::{canonical_json, idempotency_key_digest, strict_json, WorkRequest};
use serde_json::{json, Value};

#[test]
fn existing_shared_request_vectors_are_byte_identical() {
    let vectors: Vec<Value> = serde_json::from_str(include_str!("fixtures/requests.json")).unwrap();
    for vector in vectors {
        let request = WorkRequest::parse(vector["raw"].as_str().unwrap().as_bytes()).unwrap();
        assert_eq!(
            request.canonical(),
            vector["canonical"].as_str().unwrap().as_bytes()
        );
        assert_eq!(request.operation(), vector["operation"]);
        assert_eq!(json!(request.resources()), vector["resources"]);
        assert_eq!(request.request_digest(), vector["digest"]);
    }
}

#[test]
fn independently_checked_adversarial_decisions() {
    let vectors: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/adversarial.json")).unwrap();
    for vector in vectors {
        let result = WorkRequest::parse(vector["raw"].as_str().unwrap().as_bytes());
        assert_eq!(
            result.is_ok(),
            vector["rust_accept"].as_bool().unwrap(),
            "{}",
            vector["name"]
        );
        if let Ok(request) = result {
            assert_eq!(
                request.canonical(),
                vector["canonical"].as_str().unwrap().as_bytes()
            );
            assert_eq!(request.request_digest(), vector["digest"]);
            assert_eq!(json!(request.resources()), vector["resources"]);
        }
    }
}

#[test]
fn canonicalization_does_not_depend_on_object_insertion_order() {
    let left: Value = serde_json::from_str(r#"{"z":[{"b":2,"a":1}],"a":"é"}"#).unwrap();
    let right: Value = serde_json::from_str(r#"{"a":"é","z":[{"a":1,"b":2}]}"#).unwrap();
    assert_eq!(
        canonical_json(&left).unwrap(),
        canonical_json(&right).unwrap()
    );
    assert_eq!(
        canonical_json(&left).unwrap(),
        r#"{"a":"é","z":[{"a":1,"b":2}]}"#.as_bytes()
    );
}

#[test]
fn strict_boundaries_and_idempotency_grammar() {
    assert!(strict_json(&[0xff], 16).is_err());
    assert!(strict_json(b"null", 3).is_err());
    for number in [b"-0".as_slice(), b"1.0", b"1e0", b"9007199254740992"] {
        assert!(strict_json(number, 128).is_err());
    }
    let nested = format!("{}0{}", "[".repeat(17), "]".repeat(17));
    assert!(strict_json(nested.as_bytes(), 128).is_err());
    assert!(idempotency_key_digest("0123456789abcdef").is_ok());
    for key in [
        "short",
        "0123456789abcdef\n",
        "0123456789abcdef/",
        "0123456789abcdeé",
    ] {
        assert_eq!(idempotency_key_digest(key), Err("invalid_idempotency_key"));
    }
}
