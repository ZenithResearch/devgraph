//! Shared, strict Work and Arena v1 requests for native producers and WASM.
//!
//! Derived fields cannot be independently constructed or modified:
//! ```compile_fail
//! use devgraph_work_protocol::WorkRequest;
//! let request = WorkRequest { value: serde_json::Value::Null,
//!     canonical: vec![], operation: String::new(), resources: vec![] };
//! ```
mod workflow;

use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Value};
use std::collections::BTreeSet;
use std::fmt;

pub const MAX_SAFE: u64 = 9_007_199_254_740_991;
pub type Result<T> = std::result::Result<T, &'static str>;

struct StrictValue(Value);
impl<'de> Deserialize<'de> for StrictValue {
    fn deserialize<D: Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = StrictValue;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("strict JSON")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> std::result::Result<Self::Value, E> {
                Ok(StrictValue(v.into()))
            }
            fn visit_unit<E: de::Error>(self) -> std::result::Result<Self::Value, E> {
                Ok(StrictValue(Value::Null))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> std::result::Result<Self::Value, E> {
                Ok(StrictValue(v.into()))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> std::result::Result<Self::Value, E> {
                if v.unsigned_abs() > MAX_SAFE {
                    return Err(E::custom("unsafe integer"));
                }
                Ok(StrictValue(v.into()))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> std::result::Result<Self::Value, E> {
                if v > MAX_SAFE {
                    return Err(E::custom("unsafe integer"));
                }
                Ok(StrictValue(v.into()))
            }
            fn visit_seq<A: SeqAccess<'de>>(
                self,
                mut a: A,
            ) -> std::result::Result<Self::Value, A::Error> {
                let mut items = Vec::new();
                while let Some(StrictValue(item)) = a.next_element()? {
                    items.push(item);
                }
                Ok(StrictValue(Value::Array(items)))
            }
            fn visit_map<A: MapAccess<'de>>(
                self,
                mut a: A,
            ) -> std::result::Result<Self::Value, A::Error> {
                let mut fields = Map::new();
                while let Some((key, StrictValue(value))) = a.next_entry::<String, StrictValue>()? {
                    if fields.insert(key, value).is_some() {
                        return Err(de::Error::custom("duplicate key"));
                    }
                }
                Ok(StrictValue(Value::Object(fields)))
            }
        }
        d.deserialize_any(V)
    }
}

pub fn strict_json(raw: &[u8], maximum: usize) -> Result<Value> {
    if raw.len() > maximum {
        return Err("json_too_large");
    }
    // serde_json/arbitrary_precision changes lexical -0 into integer zero and
    // represents other numbers through a private map visitor. Preserve the
    // original closed integer grammar before feature-dependent deserialization.
    validate_integer_tokens(raw)?;
    let StrictValue(value) = serde_json::from_slice(raw).map_err(|_| "invalid_json")?;
    fn depth(v: &Value, n: usize) -> bool {
        n <= 16
            && match v {
                Value::Array(items) => items.iter().all(|v| depth(v, n + 1)),
                Value::Object(items) => items.values().all(|v| depth(v, n + 1)),
                _ => true,
            }
    }
    if !depth(&value, 0) {
        return Err("invalid_json");
    }
    Ok(value)
}

fn validate_integer_tokens(raw: &[u8]) -> Result<()> {
    let (mut i, mut quoted, mut escaped) = (0, false, false);
    while i < raw.len() {
        let b = raw[i];
        if quoted {
            if escaped {
                escaped = false;
            } else if b == b'\\' {
                escaped = true;
            } else if b == b'"' {
                quoted = false;
            }
            i += 1;
        } else if b == b'"' {
            quoted = true;
            i += 1;
        } else if b == b'-' || b.is_ascii_digit() {
            let start = i;
            while i < raw.len() && !b" \t\r\n,]}:".contains(&raw[i]) {
                i += 1;
            }
            let token = &raw[start..i];
            let digits = token.strip_prefix(b"-").unwrap_or(token);
            if token == b"-0" || digits.is_empty() || !digits.iter().all(u8::is_ascii_digit) {
                return Err("invalid_json");
            }
            let value = std::str::from_utf8(token)
                .ok()
                .and_then(|s| s.parse::<i64>().ok());
            if !value.is_some_and(|n| n.unsigned_abs() <= MAX_SAFE) {
                return Err("invalid_json");
            }
        } else {
            i += 1;
        }
    }
    Ok(())
}

pub fn identifier(s: &str) -> bool {
    let b = s.as_bytes();
    let alnum = |b: u8| b.is_ascii_lowercase() || b.is_ascii_digit();
    !b.is_empty()
        && b.len() <= 256
        && alnum(b[0])
        && alnum(b[b.len() - 1])
        && b.iter().all(|b| alnum(*b) || *b == b'-')
}
pub fn kind(s: &str) -> bool {
    matches!(s, "Proposal" | "Initiative" | "Project" | "Issue" | "Task")
}
pub fn text<'a>(v: &'a Value, key: &str) -> Result<&'a str> {
    v.get(key)
        .and_then(Value::as_str)
        .ok_or("invalid_work_request")
}
fn positive(v: &Value) -> Result<u64> {
    v.as_u64()
        .filter(|v| (1..=MAX_SAFE).contains(v))
        .ok_or("invalid_work_request")
}
fn exact(v: &Value, fields: &[&str]) -> Result<()> {
    let object = v.as_object().ok_or("invalid_work_request")?;
    if object.len() != fields.len() || fields.iter().any(|k| !object.contains_key(*k)) {
        return Err("invalid_work_request");
    }
    Ok(())
}
fn reference(v: &Value) -> Result<String> {
    exact(v, &["kind", "id", "expected_version"])?;
    if !kind(text(v, "kind")?) || !identifier(text(v, "id")?) {
        return Err("invalid_work_request");
    }
    positive(&v["expected_version"])?;
    Ok(format!("{}/{}", text(v, "kind")?, text(v, "id")?))
}

#[derive(Clone, Debug)]
pub struct WorkRequest {
    value: Value,
    canonical: Vec<u8>,
    operation: String,
    resources: Vec<String>,
}

impl WorkRequest {
    pub fn value(&self) -> &Value {
        &self.value
    }
    pub fn canonical(&self) -> &[u8] {
        &self.canonical
    }
    pub fn operation(&self) -> &str {
        &self.operation
    }
    pub fn resources(&self) -> &[String] {
        &self.resources
    }
    pub fn request_digest(&self) -> String {
        digest(self.request_domain(), &self.canonical)
    }
    pub fn parse(raw: &[u8]) -> Result<Self> {
        let mut value = strict_json(raw, 131_072)?;
        exact(
            &value,
            &[
                "schema",
                "operation",
                "kind",
                "id",
                "expected_version",
                "payload",
            ],
        )?;
        if text(&value, "schema")? == "devgraph.arena-request.v1" {
            return Self::parse_arena(value);
        }
        let v2 = text(&value, "schema")? == "devgraph.work-request.v2";
        if !v2 && text(&value, "schema")? != "devgraph.work-request.v1" {
            return Err("invalid_work_request");
        }
        let op = text(&value, "operation")?.to_string();
        let label = text(&value, "kind")?.to_string();
        let id = text(&value, "id")?.to_string();
        if !(kind(&label) || v2 && label == "Todo") || !identifier(&id) {
            return Err("invalid_work_request");
        }
        if (v2 && op == "status")
            || (!v2 && matches!(op.as_str(), "progress.set" | "restore" | "proposal.reject"))
            || (label == "Todo"
                && !matches!(
                    op.as_str(),
                    "create" | "patch" | "archive" | "restore" | "progress.set"
                ))
        {
            return Err("invalid_work_request");
        }
        if op == "create" {
            if !value["expected_version"].is_null() {
                return Err("invalid_work_request");
            }
        } else {
            positive(&value["expected_version"])?;
        }
        let mut payload = value["payload"].clone();
        let fields = payload.as_object_mut().ok_or("invalid_work_request")?;
        let mut resources = BTreeSet::from([format!("{label}/{id}")]);
        match op.as_str() {
            "create" | "patch" => {
                let allowed = [
                    "id",
                    "title",
                    "description",
                    "priority",
                    "artifact_ids",
                    "external_link_ids",
                ];
                if fields
                    .keys()
                    .any(|k| !allowed.contains(&k.as_str()) || (op == "patch" && k == "id"))
                {
                    return Err("invalid_work_request");
                }
                if op == "create" {
                    if fields.get("id").and_then(Value::as_str) != Some(&id)
                        || !fields.contains_key("title")
                    {
                        return Err("invalid_work_request");
                    }
                    fields
                        .entry("description")
                        .or_insert(Value::String(String::new()));
                    fields.entry("priority").or_insert(Value::from(0));
                    fields.entry("artifact_ids").or_insert(Value::Array(vec![]));
                    fields
                        .entry("external_link_ids")
                        .or_insert(Value::Array(vec![]));
                } else if fields.is_empty() {
                    return Err("invalid_work_request");
                }
                for (key, item) in fields.iter() {
                    let valid = match key.as_str() {
                        "id" => item.as_str().is_some_and(identifier),
                        "title" => item.as_str().is_some_and(|s| !s.is_empty()),
                        "description" => item.is_string(),
                        "priority" => item.as_i64().is_some_and(|v| v.unsigned_abs() <= MAX_SAFE),
                        "artifact_ids" | "external_link_ids" => item
                            .as_array()
                            .is_some_and(|a| a.iter().all(|i| i.as_str().is_some_and(identifier))),
                        _ => false,
                    };
                    if !valid {
                        return Err("invalid_work_request");
                    }
                }
            }
            "status" => {
                exact(&payload, &["status"])?;
                if !matches!(
                    text(&payload, "status")?,
                    "draft" | "review" | "accepted" | "archived"
                ) {
                    return Err("invalid_work_request");
                }
            }
            "archive" | "restore" => exact(&payload, &[])?,
            "progress.set" => {
                let allowed = [
                    "progress",
                    "reason",
                    "record_id",
                    "evidence",
                    "requirements",
                ];
                if fields.keys().any(|k| !allowed.contains(&k.as_str())) {
                    return Err("invalid_work_request");
                }
                let progress = text(&payload, "progress")?.to_string();
                let reason = text(&payload, "reason")?.to_string();
                if !matches!(progress.as_str(), "not_started" | "in_progress" | "done")
                    || reason.trim().is_empty()
                {
                    return Err("invalid_work_request");
                }
                let normalized = workflow::normalize(
                    "workflow.review",
                    serde_json::json!({
                        "record_id": text(&payload, "record_id")?, "phase": "requirements",
                        "verdict": "approved", "summary": reason,
                        "evidence": payload.get("evidence").cloned().unwrap_or(serde_json::json!([])),
                        "requirements": payload.get("requirements").cloned().unwrap_or(serde_json::json!([])),
                    }),
                    &mut resources,
                )?;
                payload = serde_json::json!({"progress": progress, "reason": reason,
                    "record_id": normalized["record_id"], "evidence": normalized["evidence"],
                    "requirements": normalized["requirements"]});
            }
            "proposal.reject" => {
                exact(&payload, &["decision_id", "reason"])?;
                let reason = text(&payload, "reason")?;
                if label != "Proposal"
                    || !identifier(text(&payload, "decision_id")?)
                    || reason.trim().is_empty()
                    || reason.chars().count() > 8192
                {
                    return Err("invalid_work_request");
                }
                resources.insert(format!("Decision/{}", text(&payload, "decision_id")?));
            }
            "accept" | "convert" => {
                if label != "Proposal" {
                    return Err("invalid_work_request");
                }
                let (destination, key) = if op == "accept" {
                    exact(&payload, &["decision_id", "decision_title"])?;
                    if text(&payload, "decision_title")?.is_empty() {
                        return Err("invalid_work_request");
                    }
                    ("Decision", "decision_id")
                } else {
                    exact(&payload, &["issue_id", "decision_id"])?;
                    if !identifier(text(&payload, "decision_id")?) {
                        return Err("invalid_work_request");
                    }
                    resources.insert(format!("Decision/{}", text(&payload, "decision_id")?));
                    ("Issue", "issue_id")
                };
                if !identifier(text(&payload, key)?) {
                    return Err("invalid_work_request");
                }
                resources.insert(format!("{destination}/{}", text(&payload, key)?));
            }
            "parent.set" => {
                if payload.get("previous_arena").is_some() {
                    exact(&payload, &["previous_parent", "parent", "previous_arena"])?;
                    if !payload["previous_arena"].is_null() {
                        if label != "Task" || payload["parent"].is_null() {
                            return Err("invalid_work_request");
                        }
                        resources.insert(arena_reference(&payload["previous_arena"])?);
                    }
                } else {
                    exact(&payload, &["previous_parent", "parent"])?;
                }
                let expected_kind = match label.as_str() {
                    "Project" => &["Initiative"][..],
                    "Issue" => &["Initiative", "Project"][..],
                    "Task" => &["Initiative", "Project", "Issue"][..],
                    _ => return Err("invalid_work_request"),
                };
                if payload["previous_parent"].is_null() && payload["parent"].is_null() {
                    return Err("invalid_work_request");
                }
                for key in ["previous_parent", "parent"] {
                    let parent = &payload[key];
                    if !parent.is_null() {
                        let resource = reference(parent)?;
                        if !expected_kind.contains(&text(parent, "kind")?) {
                            return Err("invalid_work_request");
                        }
                        resources.insert(resource);
                    }
                }
            }
            "dependency.add" | "dependency.remove" | "blocker.add" | "blocker.remove" => {
                exact(&payload, &["target"])?;
                let target = &payload["target"];
                let resource = reference(target)?;
                if resources.contains(&resource)
                    || (op.starts_with("blocker.")
                        && (label != "Task" || text(target, "kind")? != "Task"))
                {
                    return Err("invalid_work_request");
                }
                resources.insert(resource);
            }
            "workflow.assign" | "workflow.review" | "workflow.transition" => {
                payload = workflow::normalize(&op, payload, &mut resources)?;
            }
            _ => return Err("invalid_work_request"),
        }
        value["payload"] = payload;
        let canonical = canonical_json(&value)?;
        if canonical.len() > 65_536 {
            return Err("request_too_large");
        }
        Ok(Self {
            value,
            canonical,
            operation: format!("devgraph.work.{op}.v{}", if v2 { 2 } else { 1 }),
            resources: resources.into_iter().collect(),
        })
    }
}

/// Canonical protocol JSON, independent of serde_json's downstream map features.
/// Arrays preserve order. Object keys sort by their UTF-8 lexical order.
pub fn canonical_json(value: &impl serde::Serialize) -> Result<Vec<u8>> {
    let value = serde_json::to_value(value).map_err(|_| "encoding_failed")?;
    fn write(value: &Value, out: &mut Vec<u8>, depth: usize) -> Result<()> {
        if depth > 64 {
            return Err("encoding_failed");
        }
        match value {
            Value::Object(map) => {
                out.push(b'{');
                let mut keys: Vec<_> = map.keys().collect();
                keys.sort_unstable();
                for (i, key) in keys.into_iter().enumerate() {
                    if i > 0 {
                        out.push(b',');
                    }
                    out.extend(serde_json::to_vec(key).map_err(|_| "encoding_failed")?);
                    out.push(b':');
                    write(&map[key], out, depth + 1)?;
                }
                out.push(b'}');
            }
            Value::Array(items) => {
                out.push(b'[');
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        out.push(b',');
                    }
                    write(item, out, depth + 1)?;
                }
                out.push(b']');
            }
            _ => out.extend(serde_json::to_vec(value).map_err(|_| "encoding_failed")?),
        }
        Ok(())
    }
    let mut bytes = Vec::new();
    write(&value, &mut bytes, 0)?;
    Ok(bytes)
}

pub fn digest(domain: &[u8], bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut hash = Sha256::new();
    hash.update(domain);
    hash.update(bytes);
    hash.finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

pub fn idempotency_key_digest(key: &str) -> Result<String> {
    if !(16..=128).contains(&key.len())
        || !key
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._~-".contains(&b))
    {
        return Err("invalid_idempotency_key");
    }
    Ok(digest(b"", key.as_bytes()))
}

fn arena_reference(v: &Value) -> Result<String> {
    exact(v, &["kind", "id", "expected_version"])?;
    if text(v, "kind")? != "Arena" || !identifier(text(v, "id")?) {
        return Err("invalid_arena_reference");
    }
    positive(&v["expected_version"])?;
    Ok(format!("Arena/{}", text(v, "id")?))
}

impl WorkRequest {
    pub fn request_domain(&self) -> &'static [u8] {
        if self.value["schema"] == "devgraph.arena-request.v1" {
            b"devgraph.arena-request.v1\0"
        } else if self.value["schema"] == "devgraph.work-request.v2" {
            b"devgraph.work-request.v2\0"
        } else {
            b"devgraph.work-request.v1\0"
        }
    }

    fn parse_arena(mut value: Value) -> Result<Self> {
        let op = text(&value, "operation")?.to_string();
        let label = text(&value, "kind")?.to_string();
        let id = text(&value, "id")?.to_string();
        if !identifier(&id) || !matches!(op.as_str(), "create" | "patch" | "archive" | "member.set")
        {
            return Err("invalid_arena_request");
        }
        if (op == "member.set" && !matches!(label.as_str(), "Initiative" | "Task"))
            || (op != "member.set" && label != "Arena")
        {
            return Err("invalid_arena_request");
        }
        if op == "create" {
            if !value["expected_version"].is_null() {
                return Err("invalid_arena_request");
            }
        } else {
            positive(&value["expected_version"])?;
        }
        let mut payload = value["payload"].clone();
        let fields = payload.as_object_mut().ok_or("invalid_arena_request")?;
        let mut resources = BTreeSet::from([format!("{label}/{id}")]);
        match op.as_str() {
            "create" | "patch" => {
                if fields.keys().any(|key| {
                    !["id", "title", "description"].contains(&key.as_str())
                        || (op == "patch" && key == "id")
                }) {
                    return Err("invalid_arena_request");
                }
                if op == "create" {
                    if fields.get("id").and_then(Value::as_str) != Some(&id)
                        || !fields.contains_key("title")
                    {
                        return Err("invalid_arena_request");
                    }
                    fields
                        .entry("description")
                        .or_insert(Value::String(String::new()));
                } else if fields.is_empty() {
                    return Err("invalid_arena_request");
                }
                for (key, item) in fields.iter() {
                    let valid = match key.as_str() {
                        "id" => item.as_str().is_some_and(identifier),
                        "title" => item
                            .as_str()
                            .is_some_and(|s| !s.is_empty() && s.chars().count() <= 4096),
                        "description" => item.as_str().is_some_and(|s| s.chars().count() <= 32768),
                        _ => false,
                    };
                    if !valid {
                        return Err("invalid_arena_request");
                    }
                }
            }
            "archive" => exact(&payload, &[])?,
            "member.set" => {
                exact(&payload, &["previous_arena", "arena"])?;
                if payload["previous_arena"].is_null() && payload["arena"].is_null() {
                    return Err("invalid_arena_request");
                }
                for key in ["previous_arena", "arena"] {
                    if !payload[key].is_null() {
                        resources.insert(arena_reference(&payload[key])?);
                    }
                }
            }
            _ => return Err("invalid_arena_request"),
        }
        value["payload"] = payload;
        let canonical = canonical_json(&value)?;
        if canonical.len() > 65_536 {
            return Err("request_too_large");
        }
        Ok(Self {
            value,
            canonical,
            operation: format!("devgraph.arena.{op}.v1"),
            resources: resources.into_iter().collect(),
        })
    }
}
