//! Portable Devgraph client contracts. No networking, credentials, custody or OS I/O.
pub use devgraph_work_protocol as protocol;
use devgraph_work_protocol::{
    canonical_json, digest, idempotency_key_digest, identifier, kind, WorkRequest,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fmt;

mod input;
mod json;
mod read;
pub use input::*;
pub use json::parse as parse_response_json;
pub use read::{
    prepare_read, CypherResult, CypherScalar, ExpectedResult, PreparedRead, ReadInput, Subject,
    WorkFilters,
};

pub const ABI_VERSION: &str = "devgraph.web.v1";
pub const RECEIVER_ORIGIN: &str = "http://127.0.0.1:8080";
pub const RECEIVER_AUDIENCE: &str = "devgraph://receiver-local";
pub const WORK_LIMIT: usize = 8 * 1024 * 1024;
pub const PAGE_LIMIT: usize = 16 * 1024 * 1024;
pub const PROBLEM_LIMIT: usize = 65_536;
pub const CYPHER_LIMIT: usize = 262_144;
pub const MAX_LIMIT: usize = 64 * 1024 * 1024;
pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(rename_all = "snake_case")]
pub enum ErrorKind {
    Validation,
    Authorization,
    Rejected,
    Protocol,
    ResourceLimit,
    Transport,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
pub struct Error {
    pub kind: ErrorKind,
    pub code: &'static str,
}
impl Error {
    pub const fn validation(code: &'static str) -> Self {
        Self {
            kind: ErrorKind::Validation,
            code,
        }
    }
    pub const fn protocol(code: &'static str) -> Self {
        Self {
            kind: ErrorKind::Protocol,
            code,
        }
    }
    pub const fn limit(code: &'static str) -> Self {
        Self {
            kind: ErrorKind::ResourceLimit,
            code,
        }
    }
}
impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code)
    }
}
impl std::error::Error for Error {}

pub fn receiver_profile_digest(stable_issuer: &str) -> Result<String> {
    let bytes = stable_issuer.as_bytes();
    if bytes.is_empty()
        || bytes.len() > 256
        || !bytes[0].is_ascii_alphanumeric()
        || !bytes
            .iter()
            .all(|b| b.is_ascii_alphanumeric() || b"._:@/-".contains(b))
    {
        return Err(Error::validation("invalid_receiver_profile"));
    }
    let profile = json!({"schema":"devgraph.sdk-receiver-profile.v1", "origin":RECEIVER_ORIGIN,
                         "audience":RECEIVER_AUDIENCE, "stable_issuer":stable_issuer});
    Ok(digest(
        b"devgraph.sdk-receiver-profile.v1\0",
        &canonical_json(&profile).map_err(|_| Error::validation("invalid_receiver_profile"))?,
    ))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResponseMetadata {
    pub status: u16,
    pub content_type: String,
    #[serde(default)]
    pub content_encoding: Option<String>,
    #[serde(default)]
    pub max_bytes: Option<usize>,
}
impl ResponseMetadata {
    pub fn body_limit(&self, default: usize) -> Result<usize> {
        let maximum = self.max_bytes.unwrap_or(default);
        if maximum == 0
            || maximum > MAX_LIMIT
            || (default == CYPHER_LIMIT && maximum > CYPHER_LIMIT)
        {
            return Err(Error::validation("invalid_response_limit"));
        }
        Ok(if (200..300).contains(&self.status) {
            maximum
        } else {
            maximum.min(PROBLEM_LIMIT)
        })
    }
}

fn media_type(value: &str, expected: &str) -> bool {
    if value.len() > 256 {
        return false;
    }
    let mut parts = value.split(';');
    parts
        .next()
        .is_some_and(|s| s.trim().eq_ignore_ascii_case(expected))
        && parts.all(|s| s.trim().eq_ignore_ascii_case("charset=utf-8"))
}

pub(crate) fn response_value(
    raw: &[u8],
    metadata: &ResponseMetadata,
    default: usize,
    mutation: bool,
) -> Result<Value> {
    if metadata
        .content_encoding
        .as_deref()
        .is_some_and(|s| !s.eq_ignore_ascii_case("identity"))
    {
        return Err(Error::protocol("unsupported_content_encoding"));
    }
    if !(100..=599).contains(&metadata.status) {
        return Err(Error::protocol("invalid_http_status"));
    }
    let maximum = metadata.body_limit(default)?;
    if !(200..300).contains(&metadata.status) {
        if !media_type(&metadata.content_type, "application/problem+json") {
            return Err(Error::protocol("invalid_error_content_type"));
        }
        let problem = json::parse(raw, maximum.min(PROBLEM_LIMIT), 64)?;
        let object = problem
            .as_object()
            .ok_or(Error::protocol("invalid_problem"))?;
        if object
            .keys()
            .any(|k| !["type", "title", "status", "detail", "correlation_id"].contains(&k.as_str()))
            || problem["status"].as_u64() != Some(metadata.status.into())
            || !problem["title"].is_string()
            || !problem["detail"].is_string()
            || problem.get("type").is_some_and(|v| !v.is_string())
            || problem
                .get("correlation_id")
                .is_some_and(|v| !v.is_null() && !v.is_string())
        {
            return Err(Error::protocol("invalid_problem"));
        }
        let definitive = matches!(
            (metadata.status, problem["title"].as_str().unwrap()),
            (
                400,
                "Invalid request" | "Unknown work object kind" | "Invalid version precondition"
            ) | (401, "Unauthenticated")
                | (
                    403,
                    "Named Work authority denied" | "Forbidden" | "Authorization error"
                )
                | (404, "Work object not found" | "Not found")
                | (
                    409,
                    "Work relationship conflict"
                        | "Work object already exists"
                        | "Invalid status transition"
                        | "Lifecycle conflict"
                        | "Idempotency scope conflict"
                )
                | (412, "Version precondition failed")
                | (422, "Validation failed")
                | (428, "Precondition required")
        );
        return Err(Error {
            kind: if mutation && definitive {
                ErrorKind::Rejected
            } else if matches!(metadata.status, 401 | 403) {
                ErrorKind::Authorization
            } else {
                ErrorKind::Transport
            },
            code: "http_rejected",
        });
    }
    if !media_type(&metadata.content_type, "application/json") {
        return Err(Error::protocol("invalid_content_type"));
    }
    json::parse(raw, maximum, 64)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct Work {
    pub id: String,
    #[cfg_attr(feature = "declarations", ts(type = "WorkKind"))]
    pub kind: String,
    pub title: String,
    pub description: String,
    #[cfg_attr(feature = "declarations", ts(type = "WorkStatus"))]
    pub status: String,
    #[cfg_attr(feature = "declarations", ts(type = "bigint"))]
    pub version: i64,
    #[cfg_attr(feature = "declarations", ts(type = "bigint"))]
    pub priority: i64,
    pub artifact_ids: Vec<String>,
    pub external_link_ids: Vec<String>,
}
impl Work {
    pub fn validate(&self) -> Result<()> {
        if !identifier(&self.id)
            || !kind(&self.kind)
            || self.version < 1
            || !["draft", "review", "accepted", "archived"].contains(&self.status.as_str())
            || !self
                .artifact_ids
                .iter()
                .chain(&self.external_link_ids)
                .all(|s| identifier(s))
        {
            return Err(Error::protocol("invalid_work"));
        }
        Ok(())
    }
}
pub(crate) fn decode_work(value: Value) -> Result<Work> {
    let work: Work = serde_json::from_value(value).map_err(|_| Error::protocol("invalid_work"))?;
    work.validate()?;
    Ok(work)
}

/// Legacy wire `receipt`: unsigned mutation/outbox record, not execution proof.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct MutationRecord {
    pub receipt_id: String,
    #[cfg_attr(feature = "declarations", ts(type = "NamedWorkOperation"))]
    pub operation: String,
    #[cfg_attr(feature = "declarations", ts(type = "WorkKind"))]
    pub subject_label: String,
    pub subject_id: String,
    #[cfg_attr(
        feature = "declarations",
        ts(type = "\"pending\" | \"dispatched_dry_run\" | \"retry_scheduled\" | \"failed\"")
    )]
    pub receipt_status: String,
    pub duplicate: bool,
    pub correlation_id: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct MutationResult {
    pub work: Option<Work>,
    pub receipt: MutationRecord,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct ProgressWork {
    pub key: String,
    pub kind: String,
    pub id: String,
    pub title: String,
    pub description: String,
    pub priority: String,
    pub version: String,
    pub progress: Option<TodoProgress>,
    pub progress_label: String,
    pub archived: bool,
    pub classification_required: bool,
    pub workflow_id: Option<String>,
    pub stage: Option<String>,
    pub parent: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub artifact_ids: Vec<String>,
    pub external_link_ids: Vec<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct ProgressMutationResult {
    pub schema: String,
    pub work: Option<ProgressWork>,
    pub receipt: MutationRecord,
}

#[derive(Debug, Clone)]
pub struct PreparedMutation {
    request: WorkRequest,
    key_digest: String,
}
impl PreparedMutation {
    pub fn from_value(value: &Value, key: &str) -> Result<Self> {
        let _: WorkRequestInput = serde_json::from_value(value.clone())
            .map_err(|_| Error::validation("invalid_work_request"))?;
        let raw = canonical_json(value).map_err(|_| Error::validation("invalid_work_request"))?;
        Self::parse(&raw, key)
    }
    pub fn parse(raw: &[u8], key: &str) -> Result<Self> {
        let request =
            WorkRequest::parse(raw).map_err(|_| Error::validation("invalid_work_request"))?;
        // This SDK's typed requests and result envelope remain Work-only. The
        // shared protocol also supports Arena for other consumers.
        if !matches!(
            request.value()["schema"].as_str(),
            Some("devgraph.work-request.v1" | "devgraph.work-request.v2")
        ) {
            return Err(Error::validation("invalid_work_request"));
        }
        let key_digest = idempotency_key_digest(key)
            .map_err(|_| Error::validation("invalid_idempotency_key"))?;
        Ok(Self {
            request,
            key_digest,
        })
    }
    pub fn request(&self) -> &WorkRequest {
        &self.request
    }
    pub fn canonical(&self) -> &[u8] {
        self.request.canonical()
    }
    pub fn summary(&self) -> Value {
        json!({"operation":self.request.operation(),"resources":self.request.resources(),
               "request_digest_sha256":self.request.request_digest(),
               "idempotency_key_digest_sha256":self.key_digest,"request":self.request.value()})
    }
    /// Decode the application's credential transport without passing JSON numbers
    /// or duplicate-key semantics through JavaScript first.
    pub fn decode_credential_response(
        &self,
        raw: &[u8],
        metadata: &ResponseMetadata,
        reconcile: bool,
    ) -> Result<Value> {
        let mut value = response_value(raw, metadata, WORK_LIMIT, true)?;
        let object = value
            .as_object_mut()
            .ok_or(Error::protocol("invalid_mutation_result"))?;
        if metadata.status != 200 || object.remove("state") != Some(json!("committed")) {
            return Err(Error::protocol("invalid_mutation_result"));
        }
        let v2 = self.request.value()["schema"] == "devgraph.work-request.v2";
        if reconcile {
            if object.len() != 1
                || object.get("receipt").and_then(|r| r.get("duplicate"))
                    != Some(&Value::Bool(true))
            {
                return Err(Error::protocol("invalid_mutation_result"));
            }
            object.insert("work".into(), Value::Null);
            if v2 {
                object.insert("schema".into(), json!("devgraph.work-result.v2"));
            }
        } else if !v2 && object.remove("arena") != Some(Value::Null) {
            return Err(Error::protocol("invalid_mutation_result"));
        }
        let mut inner_meta = metadata.clone();
        inner_meta.status = if self.request.value()["operation"] == "create"
            && value["receipt"]["duplicate"] == false
        {
            201
        } else {
            200
        };
        self.decode_response_value(
            &serde_json::to_vec(&value).map_err(|_| Error::protocol("invalid_mutation_result"))?,
            &inner_meta,
        )
    }
    pub fn decode_response_value(&self, raw: &[u8], metadata: &ResponseMetadata) -> Result<Value> {
        if self.request.value()["schema"] != "devgraph.work-request.v2" {
            return serde_json::to_value(self.decode_response(raw, metadata)?)
                .map_err(|_| Error::protocol("invalid_mutation_result"));
        }
        let value = response_value(raw, metadata, WORK_LIMIT, true)?;
        let result: ProgressMutationResult = serde_json::from_value(value.clone())
            .map_err(|_| Error::protocol("invalid_mutation_result"))?;
        let request = self.request.value();
        let converted = request["operation"] == "convert";
        let kind = if converted {
            "Issue"
        } else {
            request["kind"].as_str().unwrap()
        };
        let id = if converted {
            request["payload"]["issue_id"].as_str().unwrap()
        } else {
            request["id"].as_str().unwrap()
        };
        let receipt = &result.receipt;
        let expected_status = if request["operation"] == "create" && !receipt.duplicate {
            201
        } else {
            200
        };
        if result.schema != "devgraph.work-result.v2"
            || metadata.status != expected_status
            || receipt.operation != self.request.operation()
            || receipt.subject_label != kind
            || receipt.subject_id != id
            || receipt.receipt_id.is_empty()
            || receipt.receipt_id.len() > 256
            || !["pending", "dispatched_dry_run", "retry_scheduled", "failed"]
                .contains(&receipt.receipt_status.as_str())
            || !receipt
                .correlation_id
                .strip_prefix("dg:sha256:")
                .is_some_and(|s| {
                    s.len() == 64
                        && s.bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                })
            || receipt.duplicate == result.work.is_some()
            || value.get("work").is_none()
        {
            return Err(Error::protocol("mutation_result_mismatch"));
        }
        if let Some(work) = result.work {
            let wire = &value["work"];
            let progress = wire["progress"].as_str();
            let label = match progress {
                Some("not_started") => "Not started",
                Some("in_progress") => "In progress",
                Some("done") => "Done",
                _ => "Needs classification",
            };
            let stage_progress = match work.stage.as_deref() {
                Some("backlog") => Some("not_started"),
                Some("done") => Some("done"),
                Some(_) => Some("in_progress"),
                None => None,
            };
            if ["progress", "workflow_id", "stage", "parent"]
                .iter()
                .any(|k| wire.get(k).is_none())
                || work.progress_label != label
                || work.workflow_id.is_some() != work.stage.is_some()
                || (work.kind == "Todo" && work.workflow_id.is_some())
                || (stage_progress.is_some() && progress.is_some() && stage_progress != progress)
            {
                return Err(Error::protocol("invalid_progress_result"));
            }

            let expected_version = if converted || request["operation"] == "create" {
                1
            } else {
                request["expected_version"].as_u64().unwrap() + 1
            };
            if work.kind != kind
                || work.id != id
                || work.key != format!("{kind}/{id}")
                || work.version != expected_version.to_string()
                || work.priority.parse::<i64>().is_err()
                || work.classification_required != work.progress.is_none()
            {
                return Err(Error::protocol("mutation_work_mismatch"));
            }
        }
        Ok(value)
    }
    pub fn decode_response(
        &self,
        raw: &[u8],
        metadata: &ResponseMetadata,
    ) -> Result<MutationResult> {
        let value = response_value(raw, metadata, WORK_LIMIT, true)?;
        // Option<T> otherwise treats a missing field as null. Wire null is explicit.
        if value.as_object().is_none_or(|v| !v.contains_key("work")) {
            return Err(Error::protocol("invalid_mutation_result"));
        }
        let result: MutationResult = serde_json::from_value(value)
            .map_err(|_| Error::protocol("invalid_mutation_result"))?;
        let record = &result.receipt;
        let request = self.request.value();
        let converted = request["operation"] == "convert";
        let label = if converted {
            "Issue"
        } else {
            request["kind"].as_str().unwrap()
        };
        let id = if converted {
            request["payload"]["issue_id"].as_str().unwrap()
        } else {
            request["id"].as_str().unwrap()
        };
        let expected_status = if request["operation"] == "create" && !record.duplicate {
            201
        } else {
            200
        };
        if metadata.status != expected_status
            || record.operation != self.request.operation()
            || record.subject_label != label
            || record.subject_id != id
            || record.receipt_id.is_empty()
            || record.receipt_id.len() > 256
            || !["pending", "dispatched_dry_run", "retry_scheduled", "failed"]
                .contains(&record.receipt_status.as_str())
            || !record
                .correlation_id
                .strip_prefix("dg:sha256:")
                .is_some_and(|s| {
                    s.len() == 64
                        && s.bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                })
            || record.duplicate == result.work.is_some()
        {
            return Err(Error::protocol("mutation_result_mismatch"));
        }
        if let Some(work) = &result.work {
            work.validate()?;
            let expected_version = if converted || request["operation"] == "create" {
                1
            } else {
                request["expected_version"].as_i64().unwrap() + 1
            };
            if work.kind != label || work.id != id || work.version != expected_version {
                return Err(Error::protocol("mutation_work_mismatch"));
            }
        }
        Ok(result)
    }
}
