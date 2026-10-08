//! Additive workflow payloads. Old request canonicalization is unchanged.
use super::{identifier, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeSet;

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Assign {
    workflow_id: String,
    #[serde(default = "backlog")]
    stage: String,
    reason: String,
}
fn backlog() -> String {
    "backlog".into()
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Transition {
    stage: String,
    #[serde(default)]
    reason: String,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Evidence {
    kind: String,
    id: String,
    #[serde(default)]
    title: String,
    #[serde(default)]
    url: String,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Requirement {
    id: String,
    #[serde(default)]
    subject: String,
    title: String,
    outcome: String,
    #[serde(default)]
    evidence_ids: Vec<String>,
    #[serde(default)]
    reason: String,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Layer {
    layer: String,
    verdict: String,
    #[serde(default)]
    reason: String,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Review {
    record_id: String,
    phase: String,
    verdict: String,
    summary: String,
    #[serde(default)]
    evidence: Vec<Evidence>,
    #[serde(default)]
    requirements: Vec<Requirement>,
    #[serde(default)]
    layers: Vec<Layer>,
}
fn bounded(s: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&s.chars().count())
}
fn verdict(s: &str) -> bool {
    matches!(s, "approved" | "changes_requested" | "not_applicable")
}
fn unique<'a>(items: impl Iterator<Item = &'a str>) -> bool {
    let mut seen = BTreeSet::new();
    items.into_iter().all(|s| seen.insert(s))
}
fn stage(workflow: &str, stage: &str) -> bool {
    let stages: &[&str] = match workflow {
        "vibe-ceo.v1" => &[
            "backlog",
            "planning",
            "developer_plan_approval",
            "ceo_plan_approval",
            "ready",
            "in_progress",
            "ceo_delivery_approval",
            "technical_review",
            "handoff_verification",
            "done",
            "blocked_waiting_input",
        ],
        "execution.v1" => &[
            "backlog",
            "intake",
            "requirements_normalized",
            "baseline_captured",
            "implementing_commit",
            "commit_ready_for_review",
            "layer_review",
            "reconciling_ledger",
            "delivery_packet_ready",
            "done",
            "blocked_waiting_input",
        ],
        _ => return false,
    };
    stages.contains(&stage)
}
fn record_kind(phase: &str) -> Option<&'static str> {
    match phase {
        "developer_plan" | "ceo_plan" | "ceo_delivery" | "resolution" => Some("Decision"),
        "handoff" => Some("Handoff"),
        "requirements" | "baseline" | "commit" | "technical" => Some("ReviewPacket"),
        _ => None,
    }
}
fn safe_url(url: &str) -> bool {
    if url.chars().any(|c| c <= ' ' || c == '\\' || c == '\u{7f}') {
        return false;
    }
    let Some(rest) = url
        .strip_prefix("https://")
        .or_else(|| url.strip_prefix("http://"))
    else {
        return false;
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    let (host, port) = if authority.starts_with('[') {
        let Some(end) = authority.find(']') else {
            return false;
        };
        if authority[1..end].parse::<std::net::Ipv6Addr>().is_err() {
            return false;
        }
        (&authority[..=end], &authority[end + 1..])
    } else {
        let mut parts = authority.splitn(2, ':');
        let host = parts.next().unwrap_or("");
        if !host
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b".-".contains(&b))
        {
            return false;
        }
        (host, &authority[host.len()..])
    };
    !host.is_empty()
        && (port.is_empty()
            || port.strip_prefix(':').is_some_and(|p| {
                !p.is_empty()
                    && p.len() <= 5
                    && p.bytes().all(|b| b.is_ascii_digit())
                    && p.parse::<u16>().is_ok()
            }))
}
pub(super) fn normalize(
    op: &str,
    payload: Value,
    resources: &mut BTreeSet<String>,
) -> Result<Value> {
    let invalid = "invalid_work_request";
    match op {
        "workflow.assign" => {
            let v: Assign = serde_json::from_value(payload).map_err(|_| invalid)?;
            if !stage(&v.workflow_id, &v.stage) || !bounded(&v.reason, 1, 4096) {
                return Err(invalid);
            }
            serde_json::to_value(v).map_err(|_| invalid)
        }
        "workflow.transition" => {
            let v: Transition = serde_json::from_value(payload).map_err(|_| invalid)?;
            if !bounded(&v.stage, 1, 64) || !bounded(&v.reason, 0, 4096) {
                return Err(invalid);
            }
            serde_json::to_value(v).map_err(|_| invalid)
        }
        "workflow.review" => {
            let v: Review = serde_json::from_value(payload).map_err(|_| invalid)?;
            let kind = record_kind(&v.phase).ok_or(invalid)?;
            if !identifier(&v.record_id)
                || !verdict(&v.verdict)
                || !bounded(&v.summary, 1, 8192)
                || v.evidence.len() > 50
                || v.requirements.len() > 100
                || v.layers.len() > 6
                || !unique(v.evidence.iter().map(|e| e.id.as_str()))
                || !unique(v.requirements.iter().map(|e| e.id.as_str()))
                || !unique(v.layers.iter().map(|e| e.layer.as_str()))
            {
                return Err(invalid);
            }
            resources.insert(format!("{kind}/{}", v.record_id));
            for e in &v.evidence {
                if !matches!(e.kind.as_str(), "Artifact" | "ExternalLink")
                    || !identifier(&e.id)
                    || !bounded(&e.title, 0, 4096)
                    || !bounded(&e.url, 0, 4096)
                {
                    return Err(invalid);
                }
                if e.url.is_empty() {
                    if !e.title.is_empty() {
                        return Err(invalid);
                    }
                } else {
                    if e.kind != "ExternalLink" || e.title.trim().is_empty() || !safe_url(&e.url) {
                        return Err(invalid);
                    }
                    resources.insert(format!("ExternalLink/{}", e.id));
                }
            }
            for r in &v.requirements {
                if !r.subject.is_empty() {
                    let (kind, id) = r.subject.split_once('/').ok_or(invalid)?;
                    if !(super::kind(kind) || matches!(kind, "Requirement" | "AcceptanceCriterion"))
                        || !identifier(id)
                    {
                        return Err(invalid);
                    }
                }
                if !identifier(&r.id)
                    || !bounded(&r.title, 1, 4096)
                    || !bounded(&r.reason, 0, 4096)
                    || !matches!(r.outcome.as_str(), "met" | "unmet" | "excluded")
                    || r.evidence_ids.len() > 50
                    || (r.outcome == "met" && r.evidence_ids.is_empty())
                    || (r.outcome == "excluded" && r.reason.trim().is_empty())
                    || r.evidence_ids
                        .iter()
                        .any(|id| !v.evidence.iter().any(|e| &e.id == id))
                {
                    return Err(invalid);
                }
            }
            for l in &v.layers {
                if !matches!(
                    l.layer.as_str(),
                    "product" | "architecture" | "implementation" | "e2e" | "design" | "ux"
                ) || !verdict(&l.verdict)
                    || !bounded(&l.reason, 0, 4096)
                    || (l.verdict == "not_applicable" && l.reason.trim().is_empty())
                {
                    return Err(invalid);
                }
            }
            serde_json::to_value(v).map_err(|_| invalid)
        }
        _ => Err(invalid),
    }
}
