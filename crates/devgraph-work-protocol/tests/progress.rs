use devgraph_work_protocol::WorkRequest;
use serde_json::{json, Value};
#[test]
fn progress_python_rust_parity() {
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/progress-v2/requests.json")).unwrap();
    for v in cases {
        let r = WorkRequest::parse(v["raw"].as_str().unwrap().as_bytes())
            .expect(v["name"].as_str().unwrap());
        assert_eq!(r.canonical(), v["canonical"].as_str().unwrap().as_bytes());
        assert_eq!(r.request_digest(), v["digest"]);
        assert_eq!(r.operation(), v["operation"]);
        assert_eq!(json!(r.resources()), v["resources"]);
    }
}
#[test]
fn progress_denials_match_python() {
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/progress-v2/invalid-requests.json")).unwrap();
    for v in cases {
        assert!(
            WorkRequest::parse(v["raw"].as_str().unwrap().as_bytes()).is_err(),
            "{}",
            v["name"]
        );
    }
}
