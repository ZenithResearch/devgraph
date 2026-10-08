//! Typed builder schema. Accepted builders still pass the shared strict protocol.
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
pub enum WorkKind {
    Todo,
    Proposal,
    Initiative,
    Project,
    Issue,
    Task,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
pub enum ProposalKind {
    Proposal,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
pub enum TaskKind {
    Task,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
pub enum ChildKind {
    Project,
    Issue,
    Task,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(rename_all = "snake_case")]
pub enum WorkStatus {
    Draft,
    Review,
    Accepted,
    Archived,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
pub enum RequestSchema {
    #[serde(rename = "devgraph.work-request.v1")]
    V1,
    #[serde(rename = "devgraph.work-request.v2")]
    V2,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct RequestReference {
    pub kind: WorkKind,
    pub id: String,
    #[cfg_attr(feature = "declarations", ts(type = "number | bigint"))]
    pub expected_version: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct CreatePayload {
    pub id: String,
    pub title: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[cfg_attr(feature = "declarations", ts(optional))]
    pub description: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[cfg_attr(feature = "declarations", ts(optional, type = "number | bigint"))]
    pub priority: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[cfg_attr(feature = "declarations", ts(optional))]
    pub artifact_ids: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[cfg_attr(feature = "declarations", ts(optional))]
    pub external_link_ids: Option<Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct PatchPayload {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[cfg_attr(feature = "declarations", ts(optional))]
    pub title: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[cfg_attr(feature = "declarations", ts(optional))]
    pub description: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[cfg_attr(feature = "declarations", ts(optional, type = "number | bigint"))]
    pub priority: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[cfg_attr(feature = "declarations", ts(optional))]
    pub artifact_ids: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[cfg_attr(feature = "declarations", ts(optional))]
    pub external_link_ids: Option<Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct StatusPayload {
    pub status: WorkStatus,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
#[cfg_attr(feature = "declarations", ts(type = "Record<string, never>"))]
pub struct EmptyPayload {}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct AcceptPayload {
    pub decision_id: String,
    pub decision_title: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct ConvertPayload {
    pub issue_id: String,
    pub decision_id: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct ParentPayload {
    pub previous_parent: Option<RequestReference>,
    pub parent: Option<RequestReference>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct TargetPayload {
    pub target: RequestReference,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct TaskReference {
    pub kind: TaskKind,
    pub id: String,
    #[cfg_attr(feature = "declarations", ts(type = "number | bigint"))]
    pub expected_version: i64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct TaskTargetPayload {
    pub target: TaskReference,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct WorkflowAssignPayload {
    pub workflow_id: String,
    #[serde(default = "workflow_backlog")]
    pub stage: String,
    pub reason: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct WorkflowTransitionPayload {
    pub stage: String,
    #[serde(default)]
    pub reason: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct WorkflowEvidence {
    pub kind: String,
    pub id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub url: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct WorkflowRequirement {
    pub id: String,
    #[serde(default)]
    pub subject: String,
    pub title: String,
    pub outcome: String,
    #[serde(default)]
    pub evidence_ids: Vec<String>,
    #[serde(default)]
    pub reason: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct WorkflowLayer {
    pub layer: String,
    pub verdict: String,
    #[serde(default)]
    pub reason: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct WorkflowReviewPayload {
    pub record_id: String,
    pub phase: String,
    pub verdict: String,
    pub summary: String,
    #[serde(default)]
    pub evidence: Vec<WorkflowEvidence>,
    #[serde(default)]
    pub requirements: Vec<WorkflowRequirement>,
    #[serde(default)]
    pub layers: Vec<WorkflowLayer>,
}
fn workflow_backlog() -> String {
    "backlog".into()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(rename_all = "snake_case")]
pub enum TodoProgress {
    NotStarted,
    InProgress,
    Done,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct ProgressPayload {
    pub progress: TodoProgress,
    pub reason: String,
    pub record_id: String,
    #[serde(default)]
    pub evidence: Vec<WorkflowEvidence>,
    #[serde(default)]
    pub requirements: Vec<WorkflowRequirement>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[cfg_attr(feature = "declarations", derive(ts_rs::TS))]
#[serde(deny_unknown_fields)]
pub struct RejectPayload {
    pub decision_id: String,
    pub reason: String,
}

macro_rules! inputs {
    ($(($variant:ident, $name:literal, $kind:ty, $payload:ty, $expected:ty, $ts:literal)),+ $(,)?) => {
        #[derive(Debug, Clone, Serialize, Deserialize)]
        #[cfg_attr(feature="declarations", derive(ts_rs::TS))]
        #[serde(tag="operation")]
        pub enum WorkRequestInput {
            $(#[serde(rename=$name)] $variant {
                schema: RequestSchema, kind: $kind, id: String,
                #[cfg_attr(feature="declarations", ts(type=$ts))]
                expected_version: $expected,
                payload: $payload,
            }),+
        }
    }
}
inputs!(
    (ProgressSet,"progress.set",WorkKind,ProgressPayload,i64,"number | bigint"),
    (Restore,"restore",WorkKind,EmptyPayload,i64,"number | bigint"),
    (Reject,"proposal.reject",ProposalKind,RejectPayload,i64,"number | bigint"),
    (WorkflowAssign,"workflow.assign",WorkKind,WorkflowAssignPayload,i64,"number | bigint"),
    (WorkflowReview,"workflow.review",WorkKind,WorkflowReviewPayload,i64,"number | bigint"),
    (WorkflowTransition,"workflow.transition",WorkKind,WorkflowTransitionPayload,i64,"number | bigint"),
    (Create,"create",WorkKind,CreatePayload,Option<()>,"null"),
    (Patch,"patch",WorkKind,PatchPayload,i64,"number | bigint"),
    (Status,"status",WorkKind,StatusPayload,i64,"number | bigint"),
    (Archive,"archive",WorkKind,EmptyPayload,i64,"number | bigint"),
    (Accept,"accept",ProposalKind,AcceptPayload,i64,"number | bigint"),
    (Convert,"convert",ProposalKind,ConvertPayload,i64,"number | bigint"),
    (ParentSet,"parent.set",ChildKind,ParentPayload,i64,"number | bigint"),
    (DependencyAdd,"dependency.add",WorkKind,TargetPayload,i64,"number | bigint"),
    (DependencyRemove,"dependency.remove",WorkKind,TargetPayload,i64,"number | bigint"),
    (BlockerAdd,"blocker.add",TaskKind,TaskTargetPayload,i64,"number | bigint"),
    (BlockerRemove,"blocker.remove",TaskKind,TaskTargetPayload,i64,"number | bigint"),
);
