use devgraph_work_protocol::{digest, WorkRequest};
use serde_json::{json, Value};

#[test]
fn arena_and_parent_change_vectors_keep_exact_bytes_domains_and_resources() {
    let vectors: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/arena-v1/requests.json")).unwrap();
    for vector in vectors {
        let request = WorkRequest::parse(vector["raw"].as_str().unwrap().as_bytes()).unwrap();
        assert_eq!(
            request.canonical(),
            vector["canonical"].as_str().unwrap().as_bytes()
        );
        assert_eq!(request.operation(), vector["operation"]);
        assert_eq!(json!(request.resources()), vector["resources"]);
        assert_eq!(request.request_digest(), vector["digest"]);
        if request.value()["schema"] == "devgraph.arena-request.v1" {
            assert_ne!(
                request.request_digest(),
                digest(b"devgraph.work-request.v1\0", request.canonical())
            );
        }
    }
}

#[test]
fn arena_rejects_closed_contract_violations() {
    let vectors: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/arena-v1/invalid-requests.json")).unwrap();
    for vector in vectors {
        assert!(
            WorkRequest::parse(vector["raw"].as_str().unwrap().as_bytes()).is_err(),
            "{}",
            vector["name"]
        );
    }
}
