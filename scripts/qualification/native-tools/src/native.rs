//! Installed SDK consumer: real Wallet consent, shared result decoding, no retry.
use clap::Parser;
use devgraph_client_core::{ErrorKind, PreparedMutation, ResponseMetadata};
use devgraph_client_native::{
    CancellationToken, DispatchState, InstallationProfile, NativeClient, WalletSignerReference,
};
use secs_native_private_files as private;
use serde_json::{json, Value};
use std::{path::PathBuf, sync::Arc};

#[derive(Parser)]
#[command(
    about = "Disposable macOS qualification only; never signs without interactive Wallet approval"
)]
struct Args {
    #[arg(long)]
    profile: PathBuf,
    #[arg(long)]
    request_file: PathBuf,
    #[arg(long)]
    idempotency_key_file: PathBuf,
    #[arg(long)]
    signing_key_file: PathBuf,
    #[arg(long)]
    public_key: String,
    #[arg(long)]
    output: PathBuf,
    /// Only reconcile the original bytes/key with fresh approval; never resubmit.
    #[arg(long)]
    reconcile: bool,
}

fn failure(code: &str, dispatched: bool) -> Value {
    json!({"schema":"devgraph.qualification-native-result.v1", "state":if dispatched {"unknown"} else {"not_dispatched"},
           "code":code,"dispatched":dispatched,"retry_performed":false})
}

async fn execute(args: &Args) -> Result<Value, Value> {
    devgraph_qualification_tools::guest().map_err(|code| failure(code, false))?;
    let output =
        private::Output::prepare(&args.output).map_err(|_| failure("unsafe_output", false))?;
    let raw = private::read(&args.request_file, 131_072)
        .map_err(|_| failure("unsafe_request_file", false))?;
    let key_raw = private::read(&args.idempotency_key_file, 130)
        .map_err(|_| failure("unsafe_idempotency_file", false))?;
    let key = std::str::from_utf8(
        key_raw
            .strip_suffix(b"\n")
            .ok_or_else(|| failure("invalid_idempotency_file", false))?,
    )
    .map_err(|_| failure("invalid_idempotency_file", false))?;
    let prepared =
        PreparedMutation::parse(&raw, key).map_err(|error| failure(error.code, false))?;
    if prepared.canonical() != raw {
        return Err(failure("canonical_request_bytes_required", false));
    }
    let profile = InstallationProfile::load(&args.profile)
        .map_err(|error| failure(error.code, error.dispatched))?;
    let client =
        NativeClient::new(profile).map_err(|error| failure(error.code, error.dispatched))?;
    let signer = WalletSignerReference {
        key_file: args.signing_key_file.clone(),
        public_key: args.public_key.clone(),
    };
    let cancel = CancellationToken::new();
    let dispatch = Arc::new(DispatchState::default());
    let signal_cancel = cancel.clone();
    let signal_dispatch = dispatch.clone();
    let signal = tokio::spawn(async move {
        if tokio::signal::ctrl_c().await.is_ok() {
            signal_dispatch.cancel();
            signal_cancel.cancel();
        }
    });
    let response = client
        .execute_with_wallet(
            &raw,
            key,
            &signer,
            args.reconcile,
            &cancel,
            dispatch.clone(),
        )
        .await;
    let mut result = async {
        let mut response = response.map_err(|error| failure(error.code, error.dispatched))?;
        let head = response.head().clone();
        let metadata = ResponseMetadata {
            status: head.status,
            content_type: head.content_type,
            content_encoding: Some(head.content_encoding),
            max_bytes: Some(head.limit),
        };
        let mut body = Vec::new();
        loop {
            let (chunk, _, done) = response
                .next_chunk(&cancel)
                .await
                .map_err(|error| failure(error.code, error.dispatched))?;
            body.extend(chunk);
            if done {
                break;
            }
        }
        decode_result(
            &prepared,
            &body,
            &metadata,
            args.reconcile,
            dispatch.dispatched(),
        )
    }
    .await;
    signal.abort();
    let value = match &mut result {
        Ok(value) | Err(value) => value,
    };
    let summary = prepared.summary();
    value["request_digest_sha256"] = summary["request_digest_sha256"].clone();
    value["idempotency_key_digest_sha256"] = summary["idempotency_key_digest_sha256"].clone();
    value["reconciliation"] = json!(args.reconcile);
    let bytes = serde_json::to_vec(value)
        .map_err(|_| failure("result_encoding_failed", dispatch.dispatched()))?;
    // Failure to retain a response after dispatch is still an unknown outcome.
    output
        .write(&bytes)
        .map_err(|_| failure("result_storage_failed", dispatch.dispatched()))?;
    result
}

fn decode_result(
    prepared: &PreparedMutation,
    body: &[u8],
    metadata: &ResponseMetadata,
    reconcile: bool,
    dispatched: bool,
) -> Result<Value, Value> {
    match prepared.decode_credential_response(body, metadata, reconcile) {
        Ok(result) => Ok(
            json!({"schema":"devgraph.qualification-native-result.v1", "state":"committed",
                              "reconciliation":reconcile, "result":result,"retry_performed":false}),
        ),
        // A denied status lookup says nothing about the original mutation.
        Err(error)
            if !reconcile
                && matches!(error.kind, ErrorKind::Authorization | ErrorKind::Rejected) =>
        {
            Err(
                json!({"schema":"devgraph.qualification-native-result.v1", "state":"rejected",
                       "code":error.code,"dispatched":dispatched,"retry_performed":false}),
            )
        }
        Err(error) => Err(failure(error.code, dispatched)),
    }
}

#[tokio::main]
async fn main() {
    let args = Args::parse();
    match execute(&args).await {
        Ok(result) => println!("{result}"),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn prepared() -> PreparedMutation {
        PreparedMutation::from_value(&json!({"schema":"devgraph.work-request.v1", "kind":"Task",
            "id":"example", "expected_version":7,"operation":"workflow.assign", "payload":{
            "reason":"Explicitly classify legacy work","stage":"backlog","workflow_id":"vibe-ceo.v1"}}),
            "named-work-native-test-0001").unwrap()
    }
    fn metadata(status: u16) -> ResponseMetadata {
        ResponseMetadata {
            status,
            content_type: if status == 200 {
                "application/json"
            } else {
                "application/problem+json"
            }
            .into(),
            content_encoding: None,
            max_bytes: None,
        }
    }
    #[test]
    fn denied_reconciliation_never_releases_the_original_unknown_outcome() {
        let request = prepared();
        let raw = serde_json::to_vec(
            &json!({"status":403,"title":"Forbidden","detail":"revoked fixture authority"}),
        )
        .unwrap();
        assert_eq!(
            decode_result(&request, &raw, &metadata(403), false, true).unwrap_err()["state"],
            "rejected"
        );
        let recovery = decode_result(&request, &raw, &metadata(403), true, true).unwrap_err();
        assert_eq!(recovery["state"], "unknown");
        assert_eq!(recovery["retry_performed"], false);
    }
    #[test]
    fn missing_or_partial_receipts_cannot_be_announced_as_committed() {
        let request = prepared();
        for body in [
            json!({"state":"unknown"}),
            json!({"state":"committed","receipt":{}}),
        ] {
            for reconcile in [true, false] {
                assert_eq!(
                    decode_result(
                        &request,
                        &serde_json::to_vec(&body).unwrap(),
                        &metadata(200),
                        reconcile,
                        true
                    )
                    .unwrap_err()["state"],
                    "unknown"
                );
            }
        }
    }
    #[test]
    fn status_only_recovery_accepts_a_complete_matching_duplicate_receipt() {
        let request = prepared();
        let body = json!({"state":"committed","receipt":{"receipt_id":"qualification-fixture-receipt",
            "operation":request.request().operation(),"subject_label":"Task","subject_id":"example",
            "receipt_status":"pending","duplicate":true,"correlation_id":format!("dg:sha256:{}","0".repeat(64))}});
        let result = decode_result(
            &request,
            &serde_json::to_vec(&body).unwrap(),
            &metadata(200),
            true,
            true,
        )
        .unwrap();
        assert_eq!(result["state"], "committed");
        assert_eq!(result["result"]["receipt"]["duplicate"], true);
        assert_eq!(result["retry_performed"], false);
        let mut wrong = body;
        wrong["receipt"]["subject_id"] = json!("different-work");
        assert_eq!(
            decode_result(
                &request,
                &serde_json::to_vec(&wrong).unwrap(),
                &metadata(200),
                true,
                true
            )
            .unwrap_err()["state"],
            "unknown"
        );
    }
    #[test]
    fn canonical_results_for_all_six_todo_types_use_the_shared_decoder() {
        for kind in ["Todo", "Proposal", "Initiative", "Project", "Issue", "Task"] {
            let request=PreparedMutation::from_value(&json!({"schema":"devgraph.work-request.v2","kind":kind,
                "id":"example","expected_version":null,"operation":"create","payload":{"id":"example",
                "title":"New work","description":"","priority":0,"artifact_ids":[],"external_link_ids":[]}}),
                "named-work-native-test-0001").unwrap();
            let workflow = match kind {
                "Todo" => None,
                "Issue" | "Task" => Some("execution.v1"),
                _ => Some("vibe-ceo.v1"),
            };
            let body = json!({"state":"committed","schema":"devgraph.work-result.v2","work":{
                "key":format!("{kind}/example"),"kind":kind,"id":"example","title":"New work","description":"",
                "priority":"0","version":"1","progress":"not_started","progress_label":"Not started",
                "archived":false,"classification_required":false,"workflow_id":workflow,
                "stage":workflow.map(|_|"backlog"),"parent":null,"created_at":"2026-10-16T00:00:00Z",
                "updated_at":"2026-10-16T00:00:00Z","artifact_ids":[],"external_link_ids":[]},
                "receipt":{"receipt_id":"qualification-fixture-receipt","operation":request.request().operation(),
                "subject_label":kind,"subject_id":"example","receipt_status":"pending","duplicate":false,
                "correlation_id":format!("dg:sha256:{}","0".repeat(64))}});
            let result = decode_result(
                &request,
                &serde_json::to_vec(&body).unwrap(),
                &metadata(200),
                false,
                true,
            )
            .unwrap();
            assert_eq!(result["state"], "committed");
            assert_eq!(result["result"]["work"]["kind"], kind);
            let mut contradictory = body;
            contradictory["work"]["progress"] = json!("done");
            assert_eq!(
                decode_result(
                    &request,
                    &serde_json::to_vec(&contradictory).unwrap(),
                    &metadata(200),
                    false,
                    true
                )
                .unwrap_err()["state"],
                "unknown"
            );
        }
    }
}
